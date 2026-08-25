"""Per-account wallets, kept fresh independently of the bots.

The console reads every saved account's balance straight from the site on a
timer, so a top-up shows up whether or not a bot happens to be running for that
account.  Asking the site also gives a truthful answer about whether a session
still works, which reading cookie expiry dates never could.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from botkit.account import read_account

from .config import BOT_VARIANTS, SITE_URL
from .processes import scan_sessions

REFRESH_SECONDS = 90.0
# Enough to get through sixteen accounts quickly without hammering the site.
MAX_PARALLEL = 4
# Do not re-query the same account more often than this, even on demand.
MIN_INTERVAL_SECONDS = 5.0


class WalletMonitor:
    def __init__(self, log=None, base_url: str = SITE_URL):
        self._log = log or (lambda _message: None)
        self._base_url = base_url
        self._lock = threading.Lock()
        self._wallets: dict[str, dict] = {}      # "variant:account" -> row
        self._checked_at: dict[str, float] = {}
        self._stop = threading.Event()
        self._busy = threading.Lock()

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        self.refresh()
        while not self._stop.wait(REFRESH_SECONDS):
            self.refresh()

    # -- reading ---------------------------------------------------------
    def refresh(self, force: bool = False) -> int:
        """Re-read every saved account. Returns how many were queried."""
        if not self._busy.acquire(blocking=False):
            return 0                       # a refresh is already running
        try:
            sessions, _ = scan_sessions()
            jobs = []
            now = time.time()
            for variant, accounts in sessions.items():
                for account in accounts:
                    key = f"{variant}:{account['account_id']}"
                    if not force and now - self._checked_at.get(key, 0.0) < MIN_INTERVAL_SECONDS:
                        continue
                    jobs.append((key, variant, account))
            if not jobs:
                return 0
            with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
                pool.map(lambda job: self._read_one(*job), jobs)
            return len(jobs)
        finally:
            self._busy.release()

    def _read_one(self, key: str, variant: str, account: dict) -> None:
        state = read_account(account["path"], self._base_url)
        row = {
            "key": key,
            "variant": variant,
            "variant_name": BOT_VARIANTS[variant]["name"],
            "game": BOT_VARIANTS[variant]["game"],
            "account_id": account["account_id"],
            "display_name": state.display_name or account.get("display_name", ""),
            "phone_tail": state.phone_tail or account.get("phone_tail", ""),
            "balance": state.balance,
            "bonus": state.bonus,
            "reachable": state.ok,
            "signed_out": state.signed_out,
            "error": state.error,
            "checked_at": time.time(),
        }
        with self._lock:
            previous = self._wallets.get(key, {})
            self._wallets[key] = row
            self._checked_at[key] = row["checked_at"]
        before, after = previous.get("balance"), row["balance"]
        if state.ok and before is not None and after is not None and before != after:
            direction = "up" if after > before else "down"
            self._log(f"{account['account_id']} wallet {direction}: "
                      f"{before:g} -> {after:g} birr")

    # -- queries ---------------------------------------------------------
    def rows(self) -> list[dict]:
        with self._lock:
            rows = list(self._wallets.values())
        rows.sort(key=lambda row: (row["variant_name"], row["account_id"]))
        return rows

    def balance_of(self, variant: str, account_id: str) -> float | None:
        with self._lock:
            row = self._wallets.get(f"{variant}:{account_id}")
        return row.get("balance") if row else None

    def summary(self) -> dict:
        rows = self.rows()
        funded = [r["balance"] for r in rows if r["reachable"] and r["balance"] is not None]
        return {
            "accounts": len(rows),
            "reachable": sum(1 for r in rows if r["reachable"]),
            "signed_out": sum(1 for r in rows if r["signed_out"]),
            "total": round(sum(funded), 2) if funded else None,
            "checked_at": max((r["checked_at"] for r in rows), default=0.0),
        }

    def forget(self, variant: str, account_id: str) -> None:
        key = f"{variant}:{account_id}"
        with self._lock:
            self._wallets.pop(key, None)
            self._checked_at.pop(key, None)
