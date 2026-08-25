"""Supervision of the bot subprocesses and the saved sessions they run as."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import BASE_DIR, BOT_VARIANTS, MAX_BOTS


def session_status(path: Path) -> tuple[str, str]:
    """Classify a saved Playwright storage state without launching a browser."""
    if not path.is_file():
        return "missing", "No session file"
    try:
        cookies = json.loads(path.read_text(encoding="utf-8")).get("cookies") or []
    except (OSError, ValueError):
        return "invalid", "Corrupted session file"
    if not cookies:
        return "expired", "Empty session"
    now = time.time()
    # A negative expiry means a session cookie, which is still usable.
    if not any(c.get("expires", -1) < 0 or c.get("expires", 0) > now for c in cookies):
        return "expired", "Session expired"
    return "active", "Session active"


def scan_sessions() -> tuple[dict[str, list[dict]], list[str]]:
    by_variant: dict[str, list[dict]] = {}
    warnings: list[str] = []
    for variant, cfg in BOT_VARIANTS.items():
        found = []
        directory = cfg["cwd"] / "sessions"
        if directory.is_dir():
            for path in sorted(directory.glob("*.storage.json")):
                code, label = session_status(path)
                account_id = path.name[: -len(".storage.json")]
                if code in {"expired", "invalid"}:
                    warnings.append(f"{cfg['name']}: session for '{account_id}' is unusable ({label}).")
                meta = {}
                try:
                    meta = json.loads(
                        path.with_name(path.name.replace(".storage.json", ".meta.json"))
                        .read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    pass
                found.append({
                    "account_id": account_id,
                    "path": str(path),
                    "display_name": str(meta.get("display_name") or ""),
                    "phone_tail": str(meta.get("phone_tail") or ""),
                    "status": code,
                    "status_label": label,
                    "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime)),
                    "usable": code == "active",
                })
        by_variant[variant] = found
    return by_variant, warnings


@dataclass
class BotProcess:
    bot_id: str
    variant: str
    game: str
    stake: int
    account_id: str
    session_path: str
    process: subprocess.Popen
    started_at: float = field(default_factory=time.time)
    last_line: str = "starting"
    exit_code: int | None = None

    @property
    def alive(self) -> bool:
        return self.process.poll() is None


class Fleet:
    """Owns every running bot process."""

    def __init__(self, coordinator, log):
        self._coordinator = coordinator
        self._log = log
        self._lock = threading.Lock()
        self._bots: dict[str, BotProcess] = {}
        self._counter = 0

    # -- queries ---------------------------------------------------------
    def alive_count(self) -> int:
        with self._lock:
            return sum(1 for bot in self._bots.values() if bot.alive)

    def snapshot(self) -> list[dict]:
        with self._lock:
            bots = list(self._bots.values())
        rows = []
        for bot in bots:
            telemetry = self._coordinator.telemetry(bot.bot_id)
            alive = bot.alive
            rows.append({
                "bot_id": bot.bot_id,
                "variant": bot.variant,
                "variant_name": BOT_VARIANTS[bot.variant]["name"],
                "game": bot.game,
                "stake": bot.stake,
                "account_id": bot.account_id,
                "pid": bot.process.pid,
                "alive": alive,
                "exit_code": bot.process.poll(),
                "uptime_seconds": int(time.time() - bot.started_at),
                "started_at": time.strftime("%H:%M:%S", time.localtime(bot.started_at)),
                "last_line": bot.last_line,
                "telemetry": telemetry,
                "state": telemetry.get("state", "starting") if alive else "stopped",
            })
        return rows

    # -- launching -------------------------------------------------------
    def session_path(self, variant: str, account_id: str) -> Path | None:
        """Resolve an account to its session file, refusing anything outside.

        ``account_id`` arrives from the browser, so a name like ``../../x`` must
        not be able to reach a file elsewhere on disk.
        """
        directory = (BOT_VARIANTS[variant]["cwd"] / "sessions").resolve()
        path = (directory / f"{account_id}.storage.json").resolve()
        return path if path.parent == directory else None

    def running_on(self, variant: str, account_id: str) -> bool:
        with self._lock:
            return any(bot.alive and bot.variant == variant and bot.account_id == account_id
                       for bot in self._bots.values())

    def delete_session(self, variant: str, account_id: str) -> str:
        """Remove a saved sign-in. Returns an error message, or "" on success."""
        if variant not in BOT_VARIANTS:
            return f"Unknown game '{variant}'."
        if self.running_on(variant, account_id):
            return f"'{account_id}' is running a bot right now. Stop it first."
        path = self.session_path(variant, account_id)
        if path is None or not path.is_file():
            return f"There is no saved session for '{account_id}'."
        try:
            path.unlink()
            path.with_name(path.name.replace(".storage.json", ".meta.json")).unlink(missing_ok=True)
        except OSError as error:
            return f"Could not delete '{account_id}': {error}"
        self._log(f"deleted the saved session for '{account_id}' ({BOT_VARIANTS[variant]['name']})")
        return ""

    def plan_launch(self, variant: str, stake: int, count: int,
                    accounts: list[str] | None = None) -> tuple[list[dict], str | None]:
        """Pick the sessions to run, or explain why we cannot.

        Named accounts are used exactly as given; otherwise free ones are taken
        in order.  Either way each bot gets its own login -- two bots sharing a
        session fight over it.
        """
        cfg = BOT_VARIANTS[variant]
        sessions, _ = scan_sessions()
        available = sessions.get(variant, [])
        with self._lock:
            busy = {bot.account_id for bot in self._bots.values()
                    if bot.alive and bot.variant == variant}
        room = MAX_BOTS - self.alive_count()

        if accounts:
            by_id = {entry["account_id"]: entry for entry in available}
            chosen: list[dict] = []
            for account_id in dict.fromkeys(accounts):  # de-duplicate, keep order
                entry = by_id.get(account_id)
                if entry is None:
                    return [], f"There is no session called '{account_id}' for {cfg['name']}."
                if not entry["usable"]:
                    return [], (f"'{account_id}' cannot sign in ({entry['status_label']}). "
                                f"Re-authenticate it before injecting.")
                if account_id in busy:
                    return [], f"'{account_id}' is already running a bot."
                chosen.append(entry)
            if len(chosen) > room:
                return [], (f"Fleet limit is {MAX_BOTS} bots; only {max(0, room)} slot(s) free "
                            f"but {len(chosen)} account(s) selected.")
            return chosen, None

        usable = [entry for entry in available if entry["usable"]]
        free = [entry for entry in usable if entry["account_id"] not in busy]
        if not usable:
            return [], (f"No usable session for {cfg['name']}. Add an account below "
                        f"before injecting bots.")
        if len(free) < count:
            return [], (f"Only {len(free)} free session(s) for {cfg['name']} but {count} bot(s) "
                        f"requested. Each bot needs its own account, otherwise they share one "
                        f"login and fight over it. Authenticate "
                        f"{count - len(free)} more, or lower the count.")
        if count > room:
            return [], f"Fleet limit is {MAX_BOTS} bots; only {max(0, room)} slot(s) free."
        return free[:count], None

    def launch(self, variant: str, stake: int, sessions: list[dict], control_url: str,
               token: str = "", headless: bool = True, min_balance: float = 10.0) -> list[str]:
        cfg = BOT_VARIANTS[variant]
        env = dict(os.environ)
        # The bot's own package plus botkit, which lives at the repo root.
        env["PYTHONPATH"] = os.pathsep.join(
            [str(cfg["cwd"] / "src"), str(BASE_DIR), env.get("PYTHONPATH", "")]
        ).strip(os.pathsep)
        env["PYTHONUNBUFFERED"] = "1"

        launched = []
        for session in sessions:
            with self._lock:
                self._counter += 1
                bot_id = f"{variant}-{session['account_id']}-{self._counter}"
            command = [
                sys.executable, "-m", cfg["module"],
                "--stake", str(stake),
                "--session-file", session["path"],
                "--control-url", control_url,
                "--bot-id", bot_id,
                "--min-balance", str(min_balance),
                "--headless" if headless else "--headed",
            ]
            if session.get("phone_tail"):
                command += ["--phone-tail", session["phone_tail"]]
            if session.get("display_name"):
                command += ["--display-name", session["display_name"]]
            if token:
                command += ["--control-token", token]
            try:
                process = subprocess.Popen(
                    command,
                    cwd=str(cfg["cwd"]),
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    encoding="utf-8",
                    errors="replace",
                )
            except OSError as error:
                self._log(f"failed to start {bot_id}: {error}")
                continue

            bot = BotProcess(
                bot_id=bot_id,
                variant=variant,
                game=cfg["game"],
                stake=stake,
                account_id=session["account_id"],
                session_path=session["path"],
                process=process,
            )
            with self._lock:
                self._bots[bot_id] = bot
            self._coordinator.report(bot_id, "starting", game=cfg["game"], stake=stake)
            threading.Thread(target=self._pump, args=(bot,), daemon=True).start()
            self._log(f"injected {bot_id} ({cfg['name']}, stake {stake}, account {session['account_id']})")
            launched.append(bot_id)
        return launched

    def _pump(self, bot: BotProcess) -> None:
        """Mirror one bot's stdout into the dashboard log."""
        stream = bot.process.stdout
        if stream is not None:
            for line in stream:
                text = line.rstrip()
                if text:
                    bot.last_line = text
                    self._log(f"[{bot.bot_id}] {text}")
            stream.close()
        code = bot.process.wait()
        bot.exit_code = code
        self._coordinator.report(bot.bot_id, "stopped", detail=f"exited with code {code}")
        self._log(f"{bot.bot_id} exited (code {code})")

    # -- stopping --------------------------------------------------------
    def stop(self, bot_id: str) -> bool:
        with self._lock:
            bot = self._bots.get(bot_id)
        if bot is None:
            return False
        self._terminate(bot)
        return True

    def stop_all(self) -> int:
        with self._lock:
            bots = list(self._bots.values())
        stopped = 0
        for bot in bots:
            if bot.alive:
                self._terminate(bot)
                stopped += 1
        return stopped

    def _terminate(self, bot: BotProcess) -> None:
        if bot.alive:
            bot.process.terminate()
            try:
                bot.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                bot.process.kill()
        self._coordinator.forget(bot.bot_id)

    def prune(self) -> int:
        """Forget bots that exited, so the table shows only the live fleet."""
        with self._lock:
            dead = [bot_id for bot_id, bot in self._bots.items() if not bot.alive]
            for bot_id in dead:
                del self._bots[bot_id]
        for bot_id in dead:
            self._coordinator.forget(bot_id)
        return len(dead)
