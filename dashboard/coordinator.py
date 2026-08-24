"""Fleet coordinator: entry permits and bot telemetry.

The odd-count rule only works if one process decides for the whole fleet.  Five
bots each checking "is the count odd?" independently all see odd at the same
instant, all join, and pair with each other.  So bots ask here instead.

Two guarantees:

* **Serialised entry** -- at most one outstanding permit per ``(game, stake)``,
  so two bots can never be mid-join at the same time.
* **Odd *human* count** -- the site's ``online`` figure includes our own bots,
  so they are subtracted before the parity test.  ``humans`` is what has to be
  odd: one unpaired real player waiting for an opponent.

A permit expires on its own, so a bot that crashes mid-join cannot wedge the
queue for its game and stake.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field

from botkit.results import DRAW, LOSS, UNKNOWN, WIN, classify_result

# A permit is abandoned if the holder neither matches nor releases in time.
PERMIT_TTL_SECONDS = 45.0
# After a grant, wait for the lobby count to reflect the bot that just joined.
SETTLE_SECONDS = 5.0
# Telemetry older than this means the bot stopped talking to us.
STALE_REPORT_SECONDS = 90.0

# A bot sitting in the queue holds one slot in the lobby count.
WAITING_STATES = frozenset({"queued"})
# A bot in a game holds one slot *and* occupies one human opponent, so the pair
# it forms is parity-neutral -- see _waiting_humans below.
PLAYING_STATES = frozenset({"matched", "playing"})


@dataclass
class _Permit:
    token: str
    bot_id: str
    game: str
    stake: int
    issued_at: float


@dataclass
class BotTelemetry:
    bot_id: str
    state: str = "starting"
    game: str = ""
    stake: int = 0
    detail: str = ""
    result: str = ""
    moves: int = 0
    wins: int = 0
    losses: int = 0
    draws: int = 0
    unknown: int = 0
    matches: int = 0
    outcome: str = ""
    balance: float | None = None
    updated_at: float = field(default_factory=time.time)

    @property
    def stale(self) -> bool:
        return time.time() - self.updated_at > STALE_REPORT_SECONDS

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "game": self.game,
            "stake": self.stake,
            "detail": self.detail,
            "result": self.result,
            "moves": self.moves,
            "wins": self.wins,
            "losses": self.losses,
            "draws": self.draws,
            "unknown": self.unknown,
            "matches": self.matches,
            "outcome": self.outcome,
            "balance": self.balance,
            "updated_at": self.updated_at,
            "stale": self.stale,
        }


class Coordinator:
    def __init__(self, lobby):
        self._lobby = lobby
        self._lock = threading.Lock()
        self._permits: dict[str, _Permit] = {}
        self._last_grant: dict[str, float] = {}
        self._telemetry: dict[str, BotTelemetry] = {}
        self.odd_gate_enabled = True

    # -- telemetry -------------------------------------------------------
    def report(self, bot_id: str, state: str, **fields) -> None:
        with self._lock:
            entry = self._telemetry.get(bot_id) or BotTelemetry(bot_id)
            entry.state = state
            entry.updated_at = time.time()
            for name in ("game", "detail", "result"):
                if fields.get(name) is not None:
                    setattr(entry, name, str(fields[name]))
            if fields.get("stake") is not None:
                entry.stake = int(fields["stake"])
            if fields.get("moves") is not None:
                entry.moves = int(fields["moves"])
            if fields.get("balance") is not None:
                entry.balance = float(fields["balance"])
            if state == "finished":
                entry.matches += 1
                # The site writes an opponent win as "<their name> won", so the
                # verb alone cannot be trusted -- see botkit.results.
                verdict = classify_result(fields.get("result"))
                entry.outcome = verdict
                if verdict == WIN:
                    entry.wins += 1
                elif verdict == LOSS:
                    entry.losses += 1
                elif verdict == DRAW:
                    entry.draws += 1
                else:
                    entry.unknown += 1
            self._telemetry[bot_id] = entry

    def telemetry(self, bot_id: str) -> dict:
        with self._lock:
            entry = self._telemetry.get(bot_id)
            return entry.as_dict() if entry else {}

    def forget(self, bot_id: str) -> None:
        """Drop a bot's state and free any permit it was holding."""
        with self._lock:
            self._telemetry.pop(bot_id, None)
            for key, permit in list(self._permits.items()):
                if permit.bot_id == bot_id:
                    del self._permits[key]

    # -- permits ---------------------------------------------------------
    def request_permit(self, bot_id: str, game: str, stake: int) -> dict:
        key = f"{game}:{int(stake)}"
        now = time.time()
        with self._lock:
            self._expire_permits(now)

            holder = self._permits.get(key)
            if holder is not None:
                if holder.bot_id == bot_id:  # idempotent retry
                    return {"granted": True, "token": holder.token, "reason": "permit already held"}
                return self._refuse("another bot is entering this table")

            waited = now - self._last_grant.get(key, 0.0)
            if waited < SETTLE_SECONDS:
                return self._refuse(
                    "waiting for the lobby count to settle",
                    retry_after=round(SETTLE_SECONDS - waited, 1),
                )

            snapshot = self._lobby.snapshot()
            if not snapshot.offers(stake):
                offered = ", ".join(str(value) for value in snapshot.stake_options)
                return self._refuse(
                    f"stake {stake} is not offered right now (site offers {offered})",
                    retry_after=10.0,
                    stake_unavailable=True,
                )

            # Belt and braces: even if a permit was released early or expired
            # while its holder was still queuing, never stack a second bot into
            # a queue that already contains one of ours -- that is exactly how
            # two bots end up paired with each other.
            queued, _ = self._occupancy(key, exclude=bot_id)
            if queued:
                return self._refuse("one of our bots is already queuing here")

            online = snapshot.count(game, stake)
            humans = self._waiting_humans(key, online, exclude=bot_id)

            if not self.odd_gate_enabled:
                return self._grant(key, bot_id, game, stake, now, humans, online, "odd-gate disabled")
            if humans == 0:
                return self._refuse("nobody is waiting to play", humans=humans, online=online)
            if humans % 2 == 0:
                return self._refuse(
                    f"{humans} waiting (even) - they pair with each other",
                    humans=humans,
                    online=online,
                )
            return self._grant(
                key, bot_id, game, stake, now, humans, online,
                f"{humans} waiting (odd) - one is unpaired",
            )

    def release(self, bot_id: str, token: str, outcome: str = "done") -> dict:
        with self._lock:
            for key, permit in list(self._permits.items()):
                if permit.token == token and permit.bot_id == bot_id:
                    del self._permits[key]
                    return {"released": True, "outcome": outcome}
        return {"released": False}

    # -- introspection ---------------------------------------------------
    def lobby_view(self, games: list[str]) -> list[dict]:
        """Per game/stake table behind the dashboard's lobby grid."""
        snapshot = self._lobby.snapshot()
        now = time.time()
        rows = []
        with self._lock:
            self._expire_permits(now)
            for game in games:
                for stake in snapshot.stake_options:
                    key = f"{game}:{stake}"
                    online = snapshot.count(game, stake)
                    waiting, playing = self._occupancy(key)
                    humans = self._waiting_humans(key, online)
                    holder = self._permits.get(key)
                    odd = humans % 2 == 1
                    rows.append({
                        "game": game,
                        "stake": stake,
                        "online": online,
                        "ours_queued": waiting,
                        "ours_playing": playing,
                        "humans": humans,
                        "odd": odd,
                        "enterable": odd if self.odd_gate_enabled else True,
                        "holder": holder.bot_id if holder else "",
                    })
        return rows

    # -- internals -------------------------------------------------------
    def _expire_permits(self, now: float) -> None:
        for key, permit in list(self._permits.items()):
            if now - permit.issued_at > PERMIT_TTL_SECONDS:
                del self._permits[key]

    def _occupancy(self, key: str, exclude: str = "") -> tuple[int, int]:
        """How many of our own bots are queued, and how many are in a game."""
        game, _, raw_stake = key.rpartition(":")
        stake = int(raw_stake)
        waiting = playing = 0
        for bot_id, entry in self._telemetry.items():
            if bot_id == exclude or entry.stale or entry.game != game or entry.stake != stake:
                continue
            if entry.state in WAITING_STATES:
                waiting += 1
            elif entry.state in PLAYING_STATES:
                playing += 1
        return waiting, playing

    def _waiting_humans(self, key: str, online: int, exclude: str = "") -> int:
        """Estimate how many players are sitting unpaired in this table's queue.

        The site's ``online`` figure counts everyone waiting *and* playing::

            online = waiting_humans + our_waiting + 2*human_vs_human + 2*our_playing

        Each of our playing bots is paired with exactly one human, so that pair
        contributes 2 and cancels out of the parity, as does every human-vs-human
        game.  Subtracting our own queued bots and both halves of our own games
        therefore leaves a number with the same parity as the unpaired humans --
        which is the only thing the odd/even rule actually needs.
        """
        waiting, playing = self._occupancy(key, exclude)
        return max(0, online - waiting - 2 * playing)

    def _grant(self, key, bot_id, game, stake, now, humans, online, reason) -> dict:
        token = uuid.uuid4().hex
        self._permits[key] = _Permit(token, bot_id, game, stake, now)
        self._last_grant[key] = now
        return {
            "granted": True,
            "token": token,
            "reason": reason,
            "humans": humans,
            "online": online,
        }

    @staticmethod
    def _refuse(reason: str, retry_after: float = 2.0, **extra) -> dict:
        return {"granted": False, "reason": reason, "retry_after": retry_after, **extra}
