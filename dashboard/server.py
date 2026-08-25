"""HTTP surface for the fleet dashboard.

Threaded on purpose: every running bot polls ``/api/coord/permit`` while the
operator's browser polls ``/api/state``, and the single-threaded server this
replaced would serialise all of it behind one slow request.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .auth import AuthManager, slug_account_id
from .users import COOKIE_NAME, Accounts, display_name
from .config import (
    BOT_VARIANTS,
    GAME_KEYS,
    MAX_BOTS,
    SITE_URL,
    STATIC_DIR,
    load_settings,
    save_settings,
)
from .coordinator import Coordinator
from .lobby import LobbyMonitor
from .processes import Fleet, scan_sessions
from .wallets import WalletMonitor

LOG_CAPACITY = 400

PUBLIC_PATHS = {"/login", "/styles.css"}

# Everything the browser needs. Checked at startup so a half-copied deployment
# is reported once, loudly, instead of turning into a 404 the operator has to
# guess at.
PAGE_ASSETS = ("index.html", "login.html", "wallets.html", "app.js", "wallets.js", "styles.css")

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}


class Dashboard:
    """Everything the request handler needs, assembled once."""

    def __init__(self, port: int, host: str = "127.0.0.1", bot_token: str = ""):
        self.port = port
        self.host = host
        # Bots cannot sign in, so they get a secret of their own.  One is always
        # present, which keeps the coordinator endpoints closed by default.
        self.bot_token = bot_token or secrets.token_urlsafe(32)
        self.started_at = time.time()
        self._log_lock = threading.Lock()
        self._log: deque[dict] = deque(maxlen=LOG_CAPACITY)

        self.settings = load_settings()
        self.lobby = LobbyMonitor(SITE_URL, on_error=self.log)
        self.coordinator = Coordinator(self.lobby, self.log)
        self.coordinator.odd_gate_enabled = bool(self.settings["odd_gate_enabled"])
        self.fleet = Fleet(self.coordinator, self.log)
        self.auth = AuthManager(self.log)
        self.accounts = Accounts(self.log)
        self.wallets = WalletMonitor(self.log, SITE_URL)

    @property
    def control_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def log(self, message: str) -> None:
        entry = {"at": time.strftime("%H:%M:%S"), "message": message}
        with self._log_lock:
            self._log.append(entry)
        print(f"[{entry['at']}] {message}", flush=True)

    def logs(self) -> list[dict]:
        with self._log_lock:
            return list(self._log)

    # -- aggregate state -------------------------------------------------
    def state(self) -> dict:
        bots = self.fleet.snapshot()
        sessions, warnings = scan_sessions()
        for variant, accounts in sessions.items():
            for account in accounts:
                account["balance"] = self.wallets.balance_of(variant, account["account_id"])
        lobby = self.lobby.status()
        if lobby["stale"]:
            warnings.append("Live lobby counts are stale - the site did not answer. Entry gating is paused.")
        for key, why in self.coordinator.paused_tables().items():
            warnings.append(f"SELF-PAIR on {key}: {why}. No bot will enter until you resume it.")
        playing = sum(1 for bot in bots if bot["state"] in {"matched", "playing"})
        wallet_summary = self.wallets.summary()
        floor = float(self.settings.get("min_balance", 10.0))
        for bot in bots:
            money = bot["telemetry"].get("balance")
            if money is None:
                money = self.wallets.balance_of(bot["variant"], bot["account_id"])
                if money is not None:
                    bot["telemetry"]["balance"] = money
            if money is not None and money < floor and bot["alive"]:
                warnings.append(
                    f"{bot['account_id']} is down to {money:g} birr (floor {floor:g}) "
                    f"and has stopped entering games. Top it up to resume."
                )
        return {
            "bots": bots,
            "sessions": sessions,
            "warnings": warnings,
            "logs": self.logs(),
            "lobby": lobby,
            "tables": self.coordinator.lobby_view(GAME_KEYS),
            "auth": self.auth.status(),
            "operators": [display_name(name) for name in self.accounts.signed_in()],
            "settings": {**self.settings, "odd_gate_enabled": self.coordinator.odd_gate_enabled},
            "wallet_summary": wallet_summary,
            "variants": [
                {"id": key, "name": cfg["name"], "game": cfg["game"]}
                for key, cfg in BOT_VARIANTS.items()
            ],
            "totals": {
                "alive": sum(1 for bot in bots if bot["alive"]),
                "playing": playing,
                "max_bots": MAX_BOTS,
                "matches": sum(bot["telemetry"].get("matches", 0) for bot in bots),
                "wins": sum(bot["telemetry"].get("wins", 0) for bot in bots),
                "losses": sum(bot["telemetry"].get("losses", 0) for bot in bots),
                "draws": sum(bot["telemetry"].get("draws", 0) for bot in bots),
                "unknown": sum(bot["telemetry"].get("unknown", 0) for bot in bots),
                "wallet": wallet_summary["total"],
                "uptime_seconds": int(time.time() - self.started_at),
            },
        }

    # -- actions ---------------------------------------------------------
    def launch(self, body: dict) -> tuple[dict, int]:
        variant = str(body.get("variant") or "")
        if variant not in BOT_VARIANTS:
            return {"error": f"Unknown game '{variant}'."}, 400

        snapshot = self.lobby.snapshot()
        stake = int(body.get("stake") or 0) or (snapshot.stake_options[0] if snapshot.stake_options else 0)
        if not snapshot.offers(stake):
            offered = ", ".join(str(value) for value in snapshot.stake_options)
            return {"error": f"The site is not offering stake {stake}. Available right now: {offered}."}, 400

        picked = [str(a) for a in (body.get("accounts") or []) if str(a).strip()]
        count = len(picked) or max(1, min(int(body.get("count") or 1), MAX_BOTS))
        stagger = max(0.0, min(float(body.get("stagger_seconds") or 2.0), 15.0))

        sessions, problem = self.fleet.plan_launch(variant, stake, count, picked or None)
        if problem:
            return {"error": problem}, 409

        self.settings.update({
            "last_variant": variant, "last_stake": stake,
            "last_count": count, "stagger_seconds": stagger,
        })
        save_settings(self.settings)

        def spawn():
            for index, session in enumerate(sessions):
                self.fleet.launch(
                    variant, stake, [session], self.control_url,
                    token=self.bot_token,
                    headless=bool(self.settings.get("headless", True)),
                    min_balance=float(self.settings.get("min_balance", 10.0)),
                )
                if index < len(sessions) - 1 and stagger:
                    time.sleep(stagger)

        threading.Thread(target=spawn, daemon=True).start()
        accounts = ", ".join(session["account_id"] for session in sessions)
        return {"message": f"Injecting {count} bot(s) at stake {stake} as {accounts}."}, 200

    def update_settings(self, body: dict) -> tuple[dict, int]:
        if "odd_gate_enabled" in body:
            enabled = bool(body["odd_gate_enabled"])
            self.coordinator.odd_gate_enabled = enabled
            self.settings["odd_gate_enabled"] = enabled
            self.log(f"odd-count entry gate {'enabled' if enabled else 'disabled'}")
        if "min_balance" in body:
            floor = max(0.0, float(body["min_balance"]))
            self.settings["min_balance"] = floor
            self.log(f"minimum balance set to {floor:g} birr (applies to bots started from now on)")
        if "headless" in body:
            self.settings["headless"] = bool(body["headless"])
        save_settings(self.settings)
        return {"settings": self.settings}, 200


class Handler(BaseHTTPRequestHandler):
    dashboard: Dashboard  # injected by serve()
    server_version = "DamaplusFleet/2.0"

    def log_message(self, *_args) -> None:  # silence per-request stderr noise
        pass

    # -- helpers ---------------------------------------------------------
    def _send(self, body: bytes, content_type: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, payload: dict, code: int = 200) -> None:
        self._send(json.dumps(payload).encode("utf-8"), "application/json", code)

    def _static(self, name: str) -> None:
        root = STATIC_DIR.resolve()
        path = (STATIC_DIR / name).resolve()
        if root not in path.parents:
            self._json({"error": f"{name} is not a page asset"}, 404)
            return
        if not path.is_file():
            # Nearly always a deployment that did not copy every file across.
            self.dashboard.log(
                f"MISSING PAGE FILE: {name} is not in {root}. "
                f"Copy the whole dashboard/static folder to this server."
            )
            self._json({
                "error": f"{name} is missing from this deployment. "
                         f"Copy dashboard/static/{name} to the server and reload.",
                "missing_file": name,
            }, 404)
            return
        self._send(path.read_bytes(), CONTENT_TYPES.get(path.suffix, "application/octet-stream"))

    def _cookie(self, name: str) -> str:
        for part in self.headers.get("Cookie", "").split(";"):
            key, _, value = part.strip().partition("=")
            if key == name:
                return value
        return ""

    def _operator(self) -> str:
        """The signed-in operator for this request, or an empty string."""
        return self.dashboard.accounts.username_for(self._cookie(COOKIE_NAME))

    def _is_bot(self) -> bool:
        """Bots cannot sign in, so they carry a shared token instead."""
        token = self.dashboard.bot_token
        return bool(token) and secrets.compare_digest(
            self.headers.get("X-Fleet-Token", ""), token
        )

    def _deny(self, message: str = "Sign in to use the console.") -> None:
        self._json({"error": message, "login_required": True}, 401)

    def _set_session_cookie(self, token: str, clear: bool = False) -> None:
        if clear:
            self.send_header("Set-Cookie",
                             f"{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict")
        else:
            self.send_header("Set-Cookie",
                             f"{COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Strict")

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    # -- routes ----------------------------------------------------------
    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        board = self.dashboard

        if path in PUBLIC_PATHS:
            # An already-signed-in operator has no reason to see the form again.
            if path == "/login" and self._operator():
                self._redirect("/")
                return
            self._static("login.html" if path == "/login" else path.lstrip("/"))
            return

        if not self._operator():
            # A browser gets the sign-in page; a script gets a clear 401.
            if path in ("/", "/index.html"):
                self._redirect("/login")
            else:
                self._deny()
            return

        if path in ("/", "/index.html"):
            self._static("index.html")
        elif path in ("/app.js", "/wallets.js"):
            self._static(path.lstrip("/"))
        elif path == "/wallets":
            self._static("wallets.html")
        elif path == "/api/wallets":
            self._json({
                "wallets": board.wallets.rows(),
                "summary": board.wallets.summary(),
                "user": display_name(self._operator()),
            })
        elif path == "/api/state":
            self._json({**board.state(), "user": display_name(self._operator())})
        else:
            self._json({"error": f"no such page: {path}"}, 404)

    def do_POST(self) -> None:
        path = self.path.split("?")[0]
        board = self.dashboard
        body = self._body()

        if path == "/api/login":
            token, problem = board.accounts.authenticate(
                str(body.get("username") or ""), str(body.get("password") or "")
            )
            if problem:
                self._json({"error": problem}, 401)
                return
            payload = json.dumps({"ok": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self._set_session_cookie(token)
            self.end_headers()
            self.wfile.write(payload)
            return

        if path == "/api/logout":
            board.accounts.logout(self._cookie(COOKIE_NAME))
            payload = json.dumps({"ok": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self._set_session_cookie("", clear=True)
            self.end_headers()
            self.wfile.write(payload)
            return

        # Bots reach only the coordinator, and only with their own token.
        if path.startswith("/api/coord/"):
            if not (self._is_bot() or self._operator()):
                self._deny("Bots must send a valid X-Fleet-Token.")
                return
        elif not self._operator():
            self._deny()
            return

        # -- fleet control
        if path == "/api/fleet/launch":
            self._json(*board.launch(body))
        elif path == "/api/fleet/stop":
            target = str(body.get("bot_id") or "")
            if target == "all":
                self._json({"message": f"Stopped {board.fleet.stop_all()} bot(s)."})
            elif board.fleet.stop(target):
                board.log(f"stopped {target}")
                self._json({"message": f"Stopped {target}."})
            else:
                self._json({"error": "No such bot."}, 404)
        elif path == "/api/sessions/delete":
            variant = str(body.get("variant") or "")
            account_id = slug_account_id(str(body.get("account_id") or ""))
            problem = board.fleet.delete_session(variant, account_id)
            if problem:
                self._json({"error": problem}, 409)
            else:
                board.wallets.forget(variant, account_id)
                self._json({"message": "Session deleted."})
        elif path == "/api/wallets/refresh":
            checked = board.wallets.refresh(force=True)
            self._json({"message": f"Re-read {checked} account wallet(s)."})
        elif path == "/api/tables/resume":
            key = str(body.get("key") or "")
            if board.coordinator.resume(key):
                self._json({"message": f"Entry to {key} resumed."})
            else:
                self._json({"error": "That table is not paused."}, 404)
        elif path == "/api/fleet/prune":
            self._json({"message": f"Cleared {board.fleet.prune()} finished bot(s)."})
        elif path == "/api/settings":
            self._json(*board.update_settings(body))

        # -- coordinator (called by the bots)
        elif path == "/api/coord/permit":
            self._json(board.coordinator.request_lease(
                str(body.get("bot_id") or ""),
                str(body.get("game") or ""),
                int(body.get("stake") or 0),
            ))
        elif path == "/api/coord/renew":
            self._json(board.coordinator.renew_lease(
                str(body.get("bot_id") or ""), str(body.get("token") or "")
            ))
        elif path == "/api/coord/release":
            self._json(board.coordinator.release_lease(
                str(body.get("bot_id") or ""),
                str(body.get("token") or ""),
                str(body.get("outcome") or "done"),
            ))
        elif path == "/api/coord/register":
            board.coordinator.register(
                str(body.get("bot_id") or ""),
                account_id=str(body.get("account_id") or ""),
                display_name=str(body.get("display_name") or ""),
                phone_tail=str(body.get("phone_tail") or ""),
            )
            self._json({"ok": True})
        elif path == "/api/coord/opponent":
            self._json(board.coordinator.check_opponent(
                str(body.get("bot_id") or ""),
                str(body.get("name") or ""),
                str(body.get("phone_tail") or ""),
            ))
        elif path == "/api/coord/report":
            payload = {k: v for k, v in body.items() if k not in ("bot_id", "state")}
            board.coordinator.report(
                str(body.get("bot_id") or ""), str(body.get("state") or "idle"), **payload
            )
            self._json({"ok": True})

        # -- account enrolment
        elif path == "/api/auth/send-otp":
            variant = str(body.get("variant") or "")
            account_id = slug_account_id(str(body.get("account_id") or ""))
            phone = str(body.get("phone") or "").strip()
            if variant not in BOT_VARIANTS or not account_id or not phone:
                self._json({"error": "Game, account name and phone number are all required."}, 400)
                return
            threading.Thread(
                target=board.auth.send_otp,
                args=(variant, account_id, phone, str(body.get("proxy") or "").strip() or None),
                daemon=True,
            ).start()
            self._json({"message": f"Requesting a code for {phone}..."})
        elif path == "/api/auth/verify-otp":
            variant = str(body.get("variant") or "")
            account_id = slug_account_id(str(body.get("account_id") or ""))
            code = str(body.get("code") or "").strip()
            if not code:
                self._json({"error": "Enter the code you received."}, 400)
                return
            threading.Thread(
                target=board.auth.verify_otp, args=(variant, account_id, code), daemon=True
            ).start()
            self._json({"message": "Verifying..."})
        else:
            self._json({"error": f"no such endpoint: {path}"}, 404)


def serve(port: int = 8080, host: str = "127.0.0.1", bot_token: str = "") -> None:
    board = Dashboard(port, host, bot_token)
    board.lobby.start()
    board.wallets.start()

    def housekeeping():
        while True:
            time.sleep(60)
            board.auth.reap()
            board.accounts.sweep()

    threading.Thread(target=housekeeping, daemon=True).start()

    handler = type("BoundHandler", (Handler,), {"dashboard": board})
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True

    missing = [name for name in PAGE_ASSETS if not (STATIC_DIR / name).is_file()]
    if missing:
        board.log(f"MISSING PAGE FILES: {', '.join(missing)} -- copy the whole "
                  f"dashboard/static folder to this server, or those pages will 404")

    snapshot = board.lobby.snapshot()
    board.log(f"console on http://{host}:{port}  (sign-in required)")
    board.log(f"site is offering stakes: {', '.join(str(s) for s in snapshot.stake_options)}")
    if host not in ("127.0.0.1", "localhost", "::1"):
        board.log("bound to a public interface - put TLS in front of it")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        board.log("shutting down; stopping bots")
        board.fleet.stop_all()
    finally:
        board.lobby.stop()
        board.wallets.stop()
        httpd.server_close()
