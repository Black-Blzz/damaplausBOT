"""Bot-side client for the dashboard coordinator.

Two jobs:

* **Telemetry** -- push every state change so the dashboard can show what each
  bot is doing without scraping stdout.
* **Entry permits** -- ask before joining matchmaking.  The dashboard hands out
  at most one permit per ``(game, stake)`` at a time and only when the number of
  *real* players waiting is odd, which is what stops our own bots from being
  paired with each other.

Every call is best-effort: if the dashboard is down the bot keeps playing.  A
permit request that cannot reach the coordinator returns ``None``, which the
caller reads as "decide for yourself" rather than as a refusal.
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

# States a bot reports.  The dashboard colours its fleet table from these.
STATES = (
    "starting",
    "authenticating",
    "idle",
    "waiting_permit",
    "queued",
    "matched",
    "playing",
    "finished",
    "stake_unavailable",
    "session_invalid",
    "error",
    "stopped",
)


@dataclass(frozen=True)
class Permit:
    granted: bool
    token: str = ""
    reason: str = ""
    humans: int = 0
    online: int = 0


class NullControlClient:
    """Stand-in used when no ``--control-url`` was supplied."""

    enabled = False

    async def report(self, state: str, **fields: object) -> None:
        return None

    async def acquire(self, game: str, stake: int, wait_seconds: float = 120.0) -> Permit | None:
        return None

    async def release(self, token: str, outcome: str = "done") -> None:
        return None


class ControlClient:
    """HTTP client for the dashboard's ``/api/coord/*`` endpoints."""

    enabled = True

    def __init__(self, base_url: str, bot_id: str, timeout: float = 4.0, token: str = ""):
        self.base_url = base_url.rstrip("/")
        self.bot_id = bot_id
        self.timeout = timeout
        self.token = token

    # -- transport -------------------------------------------------------
    def _post(self, path: str, payload: dict) -> dict | None:
        body = json.dumps({"bot_id": self.bot_id, **payload}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-Fleet-Token"] = self.token
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError, TimeoutError):
            return None

    # -- api -------------------------------------------------------------
    async def report(self, state: str, **fields: object) -> None:
        await asyncio.to_thread(self._post, "/api/coord/report", {"state": state, **fields})

    async def acquire(self, game: str, stake: int, wait_seconds: float = 120.0) -> Permit | None:
        """Block until the coordinator grants entry.

        Returns a granted permit, a refused permit when the wait budget runs
        out, or ``None`` when the coordinator could not be reached at all.
        """
        deadline = time.monotonic() + wait_seconds
        reached = False
        reason = "coordinator unreachable"
        while True:
            answer = await asyncio.to_thread(
                self._post, "/api/coord/permit", {"game": game, "stake": int(stake)}
            )
            if answer is not None:
                reached = True
                reason = str(answer.get("reason", ""))
                if answer.get("granted"):
                    return Permit(
                        True,
                        str(answer.get("token", "")),
                        reason,
                        int(answer.get("humans", 0)),
                        int(answer.get("online", 0)),
                    )
            if time.monotonic() >= deadline:
                return Permit(False, reason=reason) if reached else None
            await asyncio.sleep(float((answer or {}).get("retry_after", 2.0)))

    async def release(self, token: str, outcome: str = "done") -> None:
        if token:
            await asyncio.to_thread(
                self._post, "/api/coord/release", {"token": token, "outcome": outcome}
            )


def make_client(base_url: str | None, bot_id: str, token: str = "") -> ControlClient | NullControlClient:
    return ControlClient(base_url, bot_id, token=token) if base_url else NullControlClient()
