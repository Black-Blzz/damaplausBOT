"""Cached poller in front of the site's public lobby endpoint.

Every bot asking for a permit needs the current counts, so the raw endpoint is
fetched on a timer in one background thread rather than once per request.  The
last good snapshot is kept and served if the site briefly goes away, flagged so
the dashboard can say the numbers are stale.
"""

from __future__ import annotations

import threading
import time

from botkit.lobby import FALLBACK_STAKES, LobbySnapshot, fetch_lobby

REFRESH_SECONDS = 3.0
# Serve the last good snapshot for this long before admitting it is stale.
STALE_AFTER_SECONDS = 20.0


class LobbyMonitor:
    def __init__(self, base_url: str, on_error=None):
        self._base_url = base_url
        self._on_error = on_error
        self._lock = threading.Lock()
        self._snapshot = LobbySnapshot(FALLBACK_STAKES, {}, 0.0, stale=True)
        self._last_error = ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.refresh()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(REFRESH_SECONDS):
            self.refresh()

    def refresh(self) -> None:
        try:
            fresh = fetch_lobby(self._base_url)
        except Exception as error:  # network, JSON, HTTP -- all non-fatal
            message = f"lobby refresh failed: {error}"
            with self._lock:
                first_time = message != self._last_error
                self._last_error = message
            if first_time and self._on_error:
                self._on_error(message)
            return
        with self._lock:
            self._snapshot = fresh
            self._last_error = ""

    def snapshot(self) -> LobbySnapshot:
        with self._lock:
            snapshot = self._snapshot
        age = time.time() - snapshot.fetched_at
        if age > STALE_AFTER_SECONDS and not snapshot.stale:
            return LobbySnapshot(snapshot.stake_options, snapshot.online, snapshot.fetched_at, stale=True)
        return snapshot

    def status(self) -> dict:
        snapshot = self.snapshot()
        with self._lock:
            error = self._last_error
        return {
            "stake_options": list(snapshot.stake_options),
            "online": dict(snapshot.online),
            "fetched_at": snapshot.fetched_at,
            "age_seconds": round(time.time() - snapshot.fetched_at, 1) if snapshot.fetched_at else None,
            "stale": snapshot.stale,
            "error": error,
        }
