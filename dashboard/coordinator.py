"""Fleet coordinator: table leases, bot identity, and telemetry.

Two of our own bots must never be paired with each other.  That cannot be
decided by the bots themselves — five or sixteen of them all checking "is the
count odd?" at the same instant will all see odd, all join, and pair up.  So
every bot asks here, and the answer is enforced in three layers:

1. **One lease per table.**  A ``(game, stake)`` table admits at most one of our
   bots into its queue at a time.  The holder keeps the lease alive with
   heartbeats and only gives it up once it is actually in a game, so a bot that
   waits a long time can never have the table pulled out from under it.  (The
   permit this replaced expired on a fixed 45s timer while the bot was still
   legitimately queuing — which handed the table to a second bot.)

2. **Odd waiting count.**  A bot only enters when an odd number of players is
   waiting, meaning somebody is unpaired and will take our bot as an opponent.

3. **Opponent verification.**  Once paired, the bot reports who it is facing.
   If that turns out to be one of ours, the table is paused and the operator is
   told — prevention you cannot verify is not worth much.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field

from botkit.results import DRAW, LOSS, WIN, classify_result

# A lease dies this long after its last heartbeat.  Short, because the holder
# renews every few seconds -- see RENEW_SECONDS in botkit.matchmaking.
LEASE_TIMEOUT_SECONDS = 20.0
# After a grant, wait for the lobby count to catch up with the bot that joined.
SETTLE_SECONDS = 5.0
# Telemetry older than this means the bot stopped talking to us.
STALE_REPORT_SECONDS = 90.0

# A bot sitting in the queue holds one slot in the lobby count.
WAITING_STATES = frozenset({"queued"})
# A bot in a game holds one slot *and* occupies one human opponent, so the pair
# it forms is parity-neutral -- see _waiting_humans below.
PLAYING_STATES = frozenset({"matched", "playing"})


def table_key(game: str, stake: int) -> str:
    return f"{game}:{int(stake)}"


@dataclass
class _Lease:
    token: str
    bot_id: str
    game: str
    stake: int
    issued_at: float
    renewed_at: float

    def expired(self, now: float) -> bool:
        return now - self.renewed_at > LEASE_TIMEOUT_SECONDS


@dataclass
class BotIdentity:
    """How a bot is known to us, and to the site."""

    bot_id: str
    account_id: str = ""
    display_name: str = ""
    phone_tail: str = ""     # last 4 digits, which is all the site shows

    def matches(self, name: str, tail: str) -> bool:
        name = (name or "").strip().lower()
        tail = "".join(ch for ch in (tail or "") if ch.isdigit())
        if self.phone_tail and tail and self.phone_tail == tail:
            return True
        return bool(self.display_name) and name == self.display_name.strip().lower()


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
    opponent: str = ""
    updated_at: float = field(default_factory=time.time)

    @property
    def stale(self) -> bool:
        return time.time() - self.updated_at > STALE_REPORT_SECONDS

    def as_dict(self) -> dict:
        return {
            "state": self.state, "game": self.game, "stake": self.stake,
            "detail": self.detail, "result": self.result, "moves": self.moves,
            "wins": self.wins, "losses": self.losses, "draws": self.draws,
            "unknown": self.unknown, "matches": self.matches, "outcome": self.outcome,
            "balance": self.balance, "opponent": self.opponent,
            "updated_at": self.updated_at, "stale": self.stale,
        }


class Coordinator:
    def __init__(self, lobby, log=None):
        self._lobby = lobby
        self._log = log or (lambda _message: None)
        self._lock = threading.Lock()
        self._leases: dict[str, _Lease] = {}
        self._last_grant: dict[str, float] = {}
        self._telemetry: dict[str, BotTelemetry] = {}
        self._identities: dict[str, BotIdentity] = {}
        self._paused: dict[str, str] = {}      # table -> why it was paused
        self.odd_gate_enabled = True

    # -- identity --------------------------------------------------------
    def register(self, bot_id: str, account_id: str = "", display_name: str = "",
                 phone_tail: str = "") -> None:
        """Record who a bot is, so we can recognise it as an opponent later."""
        tail = "".join(ch for ch in str(phone_tail or "") if ch.isdigit())[-4:]
        with self._lock:
            self._identities[bot_id] = BotIdentity(
                bot_id, str(account_id or ""), str(display_name or ""), tail
            )

    def identities(self) -> list[dict]:
        with self._lock:
            return [
                {"bot_id": i.bot_id, "account_id": i.account_id,
                 "display_name": i.display_name, "phone_tail": i.phone_tail}
                for i in self._identities.values()
            ]

    def check_opponent(self, bot_id: str, name: str = "", phone_tail: str = "") -> dict:
        """Report who a bot was paired with, and say whether it is one of ours.

        A match here means the lease failed to keep two of our bots apart, which
        is worth stopping the table over rather than quietly continuing.
        """
        with self._lock:
            entry = self._telemetry.get(bot_id)
            if entry is not None:
                entry.opponent = str(name or (f"****{phone_tail}" if phone_tail else ""))
            mine = self._identities.get(bot_id)
            culprit = next(
                (i for bot, i in self._identities.items()
                 if bot != bot_id and i.matches(name, phone_tail)),
                None,
            )
            if culprit is None:
                return {"ours": False}
            game = entry.game if entry else ""
            stake = entry.stake if entry else 0
            key = table_key(game, stake)
            reason = (f"{mine.account_id if mine else bot_id} was paired with "
                      f"{culprit.account_id or culprit.bot_id}, which is also ours")
            self._paused[key] = reason
        self._log(f"SELF-PAIR on {key}: {reason}. Entry to that table is paused.")
        return {"ours": True, "table": key, "reason": reason}

    # -- pausing ---------------------------------------------------------
    def paused_tables(self) -> dict[str, str]:
        with self._lock:
            return dict(self._paused)

    def resume(self, key: str) -> bool:
        with self._lock:
            removed = self._paused.pop(key, None)
        if removed:
            self._log(f"entry to {key} resumed by the operator")
        return removed is not None

    # -- telemetry -------------------------------------------------------
    def report(self, bot_id: str, state: str, **fields) -> None:
        with self._lock:
            entry = self._telemetry.get(bot_id) or BotTelemetry(bot_id)
            entry.state = state
            entry.updated_at = time.time()
            for name in ("game", "detail", "result", "opponent"):
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
        """Drop a bot's state and free any lease it was holding."""
        with self._lock:
            self._telemetry.pop(bot_id, None)
            self._identities.pop(bot_id, None)
            for key, lease in list(self._leases.items()):
                if lease.bot_id == bot_id:
                    del self._leases[key]

    # -- leases ----------------------------------------------------------
    def request_lease(self, bot_id: str, game: str, stake: int) -> dict:
        key = table_key(game, stake)
        now = time.time()
        with self._lock:
            self._expire_leases(now)

            if key in self._paused:
                return self._refuse(f"entry paused: {self._paused[key]}", retry_after=30.0)

            holder = self._leases.get(key)
            if holder is not None:
                if holder.bot_id == bot_id:  # idempotent retry
                    return {"granted": True, "token": holder.token,
                            "reason": "already holding this table"}
                return self._refuse("another of our bots holds this table", waiting_turn=True)

            waited = now - self._last_grant.get(key, 0.0)
            if waited < SETTLE_SECONDS:
                return self._refuse("waiting for the lobby count to settle",
                                    retry_after=round(SETTLE_SECONDS - waited, 1))

            snapshot = self._lobby.snapshot()
            if not snapshot.offers(stake):
                offered = ", ".join(str(value) for value in snapshot.stake_options)
                return self._refuse(
                    f"stake {stake} is not offered right now (site offers {offered})",
                    retry_after=10.0, stake_unavailable=True,
                )

            # Belt and braces: never stack a second bot into a queue that still
            # contains one of ours, whatever the lease bookkeeping says.
            queued, _ = self._occupancy(key, exclude=bot_id)
            if queued:
                return self._refuse("one of our bots is already queuing here", waiting_turn=True)

            online = snapshot.count(game, stake)
            humans = self._waiting_humans(key, online, exclude=bot_id)

            if not self.odd_gate_enabled:
                return self._grant(key, bot_id, game, stake, now, humans, online,
                                   "odd-gate disabled")
            if humans == 0:
                return self._refuse("nobody is waiting to play", humans=humans, online=online)
            if humans % 2 == 0:
                return self._refuse(f"{humans} waiting (even) - they pair with each other",
                                    humans=humans, online=online)
            return self._grant(key, bot_id, game, stake, now, humans, online,
                               f"{humans} waiting (odd) - one is unpaired")

    def renew_lease(self, bot_id: str, token: str) -> dict:
        """Keep a lease alive while its holder is still queuing."""
        now = time.time()
        with self._lock:
            self._expire_leases(now)
            for lease in self._leases.values():
                if lease.token == token and lease.bot_id == bot_id:
                    lease.renewed_at = now
                    return {"ok": True}
        # Losing a lease mid-wait means the bot must stop queuing and re-ask.
        return {"ok": False, "reason": "lease expired; leave the queue and request again"}

    def release_lease(self, bot_id: str, token: str, outcome: str = "done") -> dict:
        with self._lock:
            for key, lease in list(self._leases.items()):
                if lease.token == token and lease.bot_id == bot_id:
                    del self._leases[key]
                    return {"released": True, "outcome": outcome}
        return {"released": False}

    # -- introspection ---------------------------------------------------
    def lobby_view(self, games: list[str]) -> list[dict]:
        """Per game/stake table behind the console's lobby grid."""
        snapshot = self._lobby.snapshot()
        now = time.time()
        rows = []
        with self._lock:
            self._expire_leases(now)
            for game in games:
                for stake in snapshot.stake_options:
                    key = table_key(game, stake)
                    online = snapshot.count(game, stake)
                    waiting, playing = self._occupancy(key)
                    humans = self._waiting_humans(key, online)
                    holder = self._leases.get(key)
                    odd = humans % 2 == 1
                    paused = self._paused.get(key, "")
                    rows.append({
                        "key": key, "game": game, "stake": stake, "online": online,
                        "ours_queued": waiting, "ours_playing": playing,
                        "humans": humans, "odd": odd,
                        "enterable": bool(not paused and (odd if self.odd_gate_enabled else True)),
                        "holder": holder.bot_id if holder else "",
                        "paused": paused,
                    })
        return rows

    # -- internals -------------------------------------------------------
    def _expire_leases(self, now: float) -> None:
        for key, lease in list(self._leases.items()):
            if lease.expired(now):
                del self._leases[key]

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
        """Estimate how many players sit unpaired in this table's queue.

        The site's ``online`` figure counts everyone waiting *and* playing::

            online = waiting_humans + our_waiting + 2*human_vs_human + 2*our_playing

        Each of our playing bots is paired with exactly one human, so that pair
        contributes 2 and cancels out of the parity, as does every human-vs-human
        game.  Subtracting our own queued bots and both halves of our own games
        therefore leaves a number with the same parity as the unpaired players --
        which is the only thing the odd/even rule actually needs.
        """
        waiting, playing = self._occupancy(key, exclude)
        return max(0, online - waiting - 2 * playing)

    def _grant(self, key, bot_id, game, stake, now, humans, online, reason) -> dict:
        token = uuid.uuid4().hex
        self._leases[key] = _Lease(token, bot_id, game, stake, now, now)
        self._last_grant[key] = now
        return {"granted": True, "token": token, "reason": reason,
                "humans": humans, "online": online}

    @staticmethod
    def _refuse(reason: str, retry_after: float = 2.0, **extra) -> dict:
        return {"granted": False, "reason": reason, "retry_after": retry_after, **extra}
