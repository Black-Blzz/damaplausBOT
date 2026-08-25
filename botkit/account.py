"""Reading an account's wallet straight from a saved session.

A balance used to be visible only while a bot was mid-queue, because only the
bot ever looked at the page.  So topping an account up showed nothing until
that bot happened to try entering a table again -- and nothing at all if no bot
was running for it.

A saved Playwright storage state already holds everything needed to ask the
site directly: the session cookies, and the auth blob in local storage that
carries the CSRF token.  ``GET /api/player/session-delta`` then answers with the
account's live wallet, with no browser involved.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

AUTH_SESSION_KEY = "damaplus-auth-session-v1"
SITE_HOST = "damaplus.online"
USER_AGENT = "Mozilla/5.0 (compatible; damaplus-fleet/1.0)"


@dataclass(frozen=True)
class AccountState:
    ok: bool
    balance: float | None = None
    bonus: float | None = None
    display_name: str = ""
    phone: str = ""
    error: str = ""

    @property
    def phone_tail(self) -> str:
        return "".join(ch for ch in self.phone if ch.isdigit())[-4:]

    @property
    def signed_out(self) -> bool:
        """The site rejected the session, which no cookie expiry can tell us."""
        return not self.ok and "signed out" in self.error


def _cookie_header(storage: dict) -> str:
    parts = []
    for cookie in storage.get("cookies") or ():
        domain = str(cookie.get("domain") or "")
        if SITE_HOST not in domain:
            continue
        name, value = cookie.get("name"), cookie.get("value")
        if name and value is not None:
            parts.append(f"{name}={value}")
    return "; ".join(parts)


def _csrf_token(storage: dict) -> str:
    for origin in storage.get("origins") or ():
        if SITE_HOST not in str(origin.get("origin") or ""):
            continue
        for item in origin.get("localStorage") or ():
            if item.get("name") != AUTH_SESSION_KEY:
                continue
            try:
                return str(json.loads(item.get("value") or "{}").get("csrfToken") or "")
            except ValueError:
                return ""
    return ""


def read_account(session_file: str | Path, base_url: str = "https://damaplus.online",
                 timeout: float = 8.0) -> AccountState:
    """Ask the site for this account's wallet, using its saved session."""
    try:
        storage = json.loads(Path(session_file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return AccountState(False, error=f"session file unreadable: {error}")

    cookies = _cookie_header(storage)
    if not cookies:
        return AccountState(False, error="session file carries no cookies for the site")

    headers = {
        "Cookie": cookies,
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
        "X-Damaplus-Client": "web-v1",
        "X-Damaplus-Role": "player",
    }
    token = _csrf_token(storage)
    if token:
        headers["X-CSRF-Token"] = token

    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/player/session-delta", headers=headers
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            return AccountState(False, error="the site says this account is signed out")
        return AccountState(False, error=f"site returned HTTP {error.code}")
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as error:
        return AccountState(False, error=f"could not reach the site: {error}")

    player = payload.get("player") or {}
    if not player:
        return AccountState(False, error="the site says this account is signed out")

    def number(name: str) -> float | None:
        try:
            return float(player[name])
        except (KeyError, TypeError, ValueError):
            return None

    return AccountState(
        True,
        balance=number("balance"),
        bonus=number("bonusBalance"),
        display_name=str(player.get("displayName") or ""),
        phone=str(player.get("phone") or ""),
    )
