"""Phone + OTP enrolment for new bot accounts.

Signing in is a two-step conversation with the site, so the browser has to stay
open between "send the code" and "here is the code".  Each pending sign-in keeps
its Playwright context alive under a key and is reaped if the operator walks
away, which the previous version leaked.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
import time
from pathlib import Path

from playwright.async_api import async_playwright

from .config import BOT_VARIANTS, SITE_URL

# A half-finished sign-in holds a browser open; drop it after this long.
PENDING_TTL_SECONDS = 600.0

# Names new accounts show to opponents.  The site displays whatever name an
# account registered with, so calling them "Bot-<id>" told every opponent
# exactly what they were playing.  One is chosen per account, deterministically,
# so the same account always presents the same name.
NEUTRAL_NAMES = (
    "Abel", "Bereket", "Chala", "Dawit", "Eyob", "Fikru", "Girma", "Hanna",
    "Kalkidan", "Lemma", "Meron", "Nardos", "Robel", "Selam", "Tigist", "Yonas",
)


def display_name_for(account_id: str) -> str:
    """A stable, ordinary-looking name for one account."""
    digest = hashlib.sha256(account_id.encode("utf-8")).digest()
    return NEUTRAL_NAMES[digest[0] % len(NEUTRAL_NAMES)]


def phone_tail(number: str) -> str:
    """Last four digits -- all the site reveals of another player's phone."""
    return "".join(ch for ch in str(number or "") if ch.isdigit())[-4:]


def meta_path(session_file: Path) -> Path:
    return session_file.with_name(session_file.name.replace(".storage.json", ".meta.json"))


def load_meta(session_file: Path) -> dict:
    try:
        return json.loads(meta_path(session_file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def normalise_phone(raw: str) -> str:
    """Reduce any Ethiopian phone spelling to the local 0XXXXXXXXX form."""
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("251"):
        digits = digits[3:]
    digits = digits.lstrip("0")
    if len(digits) == 9 and digits[0] in "97":
        return f"0{digits}"
    return raw.strip()


def slug_account_id(raw: str) -> str:
    """Make an account name safe to use as a filename and an identifier."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", raw.strip()).strip("-._")
    return cleaned[:60]


def playwright_proxy(raw: str | None) -> dict | None:
    """Turn a proxy string into Playwright's proxy dict, or None."""
    if not raw or not raw.strip():
        return None
    value = raw.strip()
    match = re.match(
        r"^(https?|socks5|socks4)://(?:([^:@]+):([^@]+)@)?([^:/]+:\d+)$", value, re.IGNORECASE
    )
    if match:
        scheme, user, password, host_port = match.groups()
        proxy = {"server": f"{scheme}://{host_port}"}
        if user and password:
            proxy["username"], proxy["password"] = user, password
        return proxy
    if not value.startswith(("http://", "https://", "socks5://", "socks4://")):
        value = f"http://{value}"
    return {"server": value}


class AuthError(RuntimeError):
    pass


class AuthManager:
    """Owns every pending sign-in, and the one event loop they all live on.

    A sign-in spans two operator actions, so its browser has to stay open in
    between.  Playwright objects are bound to the loop that created them, so a
    fresh ``asyncio.run()`` per request would hand the second half of the
    conversation a browser whose transport belongs to a loop that has already
    closed.  Instead one loop runs for the life of the dashboard and every
    Playwright call is submitted to it.
    """

    def __init__(self, log):
        self._log = log
        self._lock = threading.Lock()
        self._pending: dict[str, dict] = {}
        self._status: dict[str, dict] = {}
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._run_loop, name="auth-loop", daemon=True).start()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _submit(self, coro):
        """Run a coroutine on the auth loop and wait for it from this thread."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    # -- status ----------------------------------------------------------
    def status(self) -> dict:
        with self._lock:
            return dict(self._status)

    def _set(self, key: str, state: str, message: str) -> None:
        with self._lock:
            self._status[key] = {"state": state, "message": message, "at": time.time()}

    @staticmethod
    def key(variant: str, account_id: str) -> str:
        return f"{variant}:{account_id}"

    # -- entry points (called on a request thread) -----------------------
    def send_otp(self, variant: str, account_id: str, phone: str, proxy: str | None) -> None:
        key = self.key(variant, account_id)
        try:
            self._submit(self._send_otp(key, variant, account_id, phone, proxy))
        except Exception as error:
            self._set(key, "error", str(error))
            self._log(f"OTP request failed for {account_id}: {error}")

    def verify_otp(self, variant: str, account_id: str, code: str) -> None:
        key = self.key(variant, account_id)
        try:
            self._submit(self._verify_otp(key, code))
        except Exception as error:
            self._set(key, "error", str(error))
            self._log(f"OTP verification failed for {account_id}: {error}")

    def reap(self) -> None:
        """Close browsers left behind by abandoned sign-ins."""
        now = time.time()
        with self._lock:
            stale = [k for k, v in self._pending.items() if now - v["created_at"] > PENDING_TTL_SECONDS]
            entries = [(k, self._pending.pop(k)) for k in stale]
        for key, entry in entries:
            self._set(key, "error", "Sign-in timed out. Start again.")
            try:
                self._submit(_shutdown(entry))
            except Exception:
                pass

    # -- implementation --------------------------------------------------
    async def _send_otp(self, key, variant, account_id, phone, proxy) -> None:
        if variant not in BOT_VARIANTS:
            raise AuthError(f"unknown variant {variant!r}")
        number = normalise_phone(phone)
        self._retire_others_on(number, keep=key)
        self._set(key, "sending", f"Opening the site and requesting a code for {number}...")

        proxy_config = playwright_proxy(proxy)
        playwright = await async_playwright().start()
        launch: dict = {"headless": True}
        if proxy_config:
            launch["proxy"] = proxy_config
            self._log(f"using proxy {proxy_config['server']} for the OTP request")
        browser = await playwright.chromium.launch(**launch)
        context = await browser.new_context()
        page = await context.new_page()
        entry = {"playwright": playwright, "browser": browser, "context": context, "page": page,
                 "variant": variant, "account_id": account_id, "phone": number,
                 "created_at": time.time()}

        # Every answer the site gives to a send-otp request, newest last.
        replies: list[dict] = []

        async def watch(response):
            if "send-otp" not in response.url:
                return
            try:
                data = await response.json()
            except Exception:
                data = {}
            replies.append({
                "status": response.status,
                "message": str(data.get("message") or data.get("error") or f"HTTP {response.status}"),
                "requires_signup": bool(data.get("requiresSignup")),
                "ok": response.status == 200 and data.get("ok") is not False,
            })

        page.on("response", watch)
        page.on("requestfailed", lambda request: replies.append({
            "status": 0,
            "message": "the request to the site's OTP service never completed",
            "requires_signup": False,
            "ok": False,
        }) if "send-otp" in request.url else None)

        try:
            try:
                await page.goto(SITE_URL, wait_until="commit", timeout=20_000)
                await page.locator("#authPhoneInput").wait_for(state="visible", timeout=10_000)
            except Exception as error:
                if proxy_config:
                    raise AuthError(
                        f"Proxy {proxy_config['server']} is unreachable or does not support HTTPS."
                    ) from error
                raise AuthError(f"Could not load {SITE_URL}: {error}") from error

            await page.locator("#authPhoneInput").fill(number)

            # The site's SMS provider drops roughly one request in three with a
            # 502, so a single attempt is not a fair test of whether a number
            # can be reached.  Retry that, but never retry a rate-limit -- that
            # only pushes the cooldown further out.
            reply = await self._submit_until_sent(page, replies, number, key)

            if reply.get("requires_signup"):
                self._set(key, "sending", f"{number} is new here - registering an account...")
                self._log(f"{number} is a new customer; filling in the signup fields")
                await page.locator("#authNameInput").fill(display_name_for(account_id))
                terms = page.locator("#authTermsInput")
                if await terms.count() and not await terms.is_checked():
                    await terms.check()
                await self._submit_until_sent(page, replies, number, key)

            await _await_code_form(page, replies, number)
        except Exception:
            await _shutdown(entry)
            raise

        with self._lock:
            self._pending[key] = entry
        self._set(key, "code_sent", f"Code sent to {number}. Enter the 6 digits below.")
        self._log(f"OTP sent to {number} for account '{account_id}'")

    async def _submit_until_sent(self, page, replies, number, key) -> dict:
        """Press "Send OTP" until the site accepts it, or explains why not."""
        for attempt in range(1, SEND_ATTEMPTS + 1):
            seen = len(replies)
            await page.locator("#phoneAuthForm button[type='submit']").click()

            deadline = time.monotonic() + SEND_TIMEOUT_SECONDS
            while len(replies) == seen and time.monotonic() < deadline:
                await page.wait_for_timeout(300)

            if len(replies) == seen:
                reply = {"status": 0, "ok": False, "requires_signup": False,
                         "message": "the site's OTP service did not respond"}
                replies.append(reply)
            else:
                reply = replies[-1]

            if reply.get("ok"):
                return reply
            if _is_rate_limited(reply):
                raise AuthError(_explain(replies, number))  # retrying extends the cooldown

            if attempt < SEND_ATTEMPTS:
                self._log(f"send-otp attempt {attempt} for {number} failed ({reply['message']}); retrying")
                self._set(key, "sending",
                          f"The site's SMS service failed on attempt {attempt}. Retrying...")
                await page.wait_for_timeout(int(RETRY_PAUSE_SECONDS * 1000))

        raise AuthError(_explain(replies, number))

    async def _verify_otp(self, key, code) -> None:
        # Peek rather than pop: a mistyped code should leave the sign-in open so
        # the same SMS can be entered again, instead of forcing a new one and
        # burning through the site's rate limit.
        with self._lock:
            entry = self._pending.get(key)
        if entry is None:
            raise AuthError("That sign-in expired. Press Send code again.")

        self._set(key, "verifying", "Submitting the code...")
        page, context = entry["page"], entry["context"]
        variant, account_id = entry["variant"], entry["account_id"]

        try:
            await page.locator("#otpInput").fill(code)
            await page.locator("#otpVerifyForm button[type='submit']").click()
            try:
                await page.locator("#playerApp:not(.hidden)").wait_for(state="visible", timeout=15_000)
            except Exception as error:
                toast = await _toast_text(page)
                raise AuthError(
                    f"The site said: “{toast}”" if toast
                    else "That code was not accepted. Check the six digits and try again."
                ) from error

            destination = BOT_VARIANTS[variant]["cwd"] / "sessions" / f"{account_id}.storage.json"
            destination.parent.mkdir(parents=True, exist_ok=True)
            await context.storage_state(path=str(destination))
            # Keep the account's identity beside its session: the coordinator
            # needs it to recognise this account if another of our bots is ever
            # paired against it.
            meta_path(destination).write_text(json.dumps({
                "account_id": account_id,
                "display_name": display_name_for(account_id),
                "phone_tail": phone_tail(entry.get("phone", "")),
                "saved_at": time.time(),
            }, indent=2), encoding="utf-8")
        except AuthError:
            raise  # the browser is still good; the operator can retype the code
        except Exception as error:
            self._discard(key)  # the browser itself is broken, so start over
            raise AuthError(f"The sign-in browser failed: {error}. Press Send code again.") from error

        self._discard(key)
        self._set(key, "success", f"Session saved for '{account_id}'. It can now run a bot.")
        self._log(f"session saved for '{account_id}' -> {destination.name}")

    def _retire_others_on(self, number: str, keep: str) -> None:
        """Drop other sign-ins waiting on this number.

        The site only keeps the newest code per phone, so a second request
        silently invalidates the first.  Leaving both on screen invites typing
        a dead code into the wrong account, which is a confusing failure.
        """
        with self._lock:
            doomed = [k for k, v in self._pending.items()
                      if k != keep and v.get("phone") == number]
        for other in doomed:
            self._discard(other)
            self._set(other, "error",
                      f"Superseded: a newer code was requested for {number}. "
                      f"That earlier code no longer works.")
            self._log(f"retired the pending sign-in {other} - {number} was re-requested")

    def _discard(self, key: str) -> None:
        """Forget a sign-in and close its browser."""
        with self._lock:
            entry = self._pending.pop(key, None)
        if entry is not None:
            self._loop.create_task(_shutdown(entry))


async def _toast_text(page) -> str:
    toast = page.locator("#toast")
    if await toast.count():
        return (await toast.text_content() or "").strip()
    return ""


CODE_STEP = "#otpVerifyForm:not(.hidden)"
SIGNUP_STEP = "#authNameRow:not(.hidden)"

# The site aborts its own request after 20s, so wait a little longer than that.
SEND_TIMEOUT_SECONDS = 26.0
SEND_ATTEMPTS = 4
RETRY_PAUSE_SECONDS = 3.0


async def _await_code_form(page, replies, number) -> None:
    try:
        await page.wait_for_selector(CODE_STEP, state="visible", timeout=15_000)
    except Exception as error:
        raise AuthError(_explain(replies, number, await _toast_text(page))) from error


def _is_rate_limited(reply: dict) -> bool:
    return reply.get("status") == 429


def _explain(replies: list[dict], number: str, toast: str = "") -> str:
    """Say what the site actually reported, quoting it verbatim."""
    if replies:
        last = replies[-1]
        if _is_rate_limited(last):
            return (f"The site is rate-limiting this connection: “{last['message']}” "
                    f"Wait it out, or put a proxy in the field above.")
        if last.get("status") == 502:
            return (f"The site's SMS provider could not deliver a code to {number} "
                    f"after {SEND_ATTEMPTS} attempts: “{last['message']}” "
                    f"This is a fault on their side — try again shortly.")
        if not last.get("ok"):
            return f"The site refused the request for {number}: “{last['message']}”"
    if toast:
        return f"The site said: “{toast}”"
    return f"The site never answered the code request for {number} and gave no reason."


async def _shutdown(entry: dict) -> None:
    for name, close in (("browser", "close"), ("playwright", "stop")):
        target = entry.get(name)
        if target is not None:
            try:
                await getattr(target, close)()
            except Exception:
                pass
