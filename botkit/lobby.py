"""Client for the DamaPlus public lobby endpoint.

``GET /api/lobby/public`` is what the site itself uses to render the stake
chooser, so it is the authoritative source for both the stake options that are
actually on offer and the live per-game/stake player counts::

    {"ok": true,
     "stakeOptions": [5, 30],
     "online": {"chess:5": 1, "dama-tankegna:5": 2, "xo:5": 3}}

Keys in ``online`` are ``"<game>:<stake>"``.  Absent keys mean zero.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

# Game identifiers used by the site, keyed by the bot variant that plays them.
GAME_KEYS = {
    "tankegna": "dama-tankegna",
    "egregna": "dama-egregna",
    "xo": "xo",
    "chess": "chess",
}

# Used only when the endpoint is unreachable; the site falls back to these too.
FALLBACK_STAKES = (10, 25, 50)

_USER_AGENT = "Mozilla/5.0 (compatible; damaplus-fleet/1.0)"


@dataclass(frozen=True)
class LobbySnapshot:
    stake_options: tuple[int, ...]
    online: dict[str, int] = field(default_factory=dict)
    fetched_at: float = 0.0
    stale: bool = False

    def count(self, game: str, stake: int) -> int:
        """Players currently online for one game at one stake."""
        return int(self.online.get(f"{game}:{int(stake)}", 0))

    def offers(self, stake: int) -> bool:
        return int(stake) in self.stake_options


def fetch_lobby(base_url: str = "https://damaplus.online", timeout: float = 5.0) -> LobbySnapshot:
    """Read one lobby snapshot.  Raises ``OSError``/``ValueError`` on failure."""
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/lobby/public",
        headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    stakes = tuple(sorted({int(value) for value in payload.get("stakeOptions") or ()}))
    online = {str(key): int(value) for key, value in (payload.get("online") or {}).items()}
    return LobbySnapshot(stakes or FALLBACK_STAKES, online, time.time(), stale=not stakes)
