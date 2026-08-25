"""The rules that stop two of our own bots meeting across a table.

Three layers, tested here in order: one lease per table, an odd waiting count,
and after-the-fact opponent verification.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from botkit.lobby import LobbySnapshot
from dashboard import coordinator as coordinator_module
from dashboard.coordinator import Coordinator

XO = "xo"
STAKE = 5
KEY = f"{XO}:{STAKE}"


class FakeLobby:
    """Stands in for the site so counts can be set exactly."""

    def __init__(self, stakes=(5, 30)):
        self.online: dict[str, int] = {}
        self.stakes = stakes

    def set(self, count: int, game: str = XO, stake: int = STAKE) -> None:
        self.online[f"{game}:{stake}"] = count

    def snapshot(self) -> LobbySnapshot:
        return LobbySnapshot(self.stakes, self.online, time.time())


@pytest.fixture
def lobby():
    return FakeLobby()


@pytest.fixture
def coord(lobby, monkeypatch):
    # The settle cooldown lets the live count catch up; it only gets in the way
    # of deterministic tests.
    monkeypatch.setattr(coordinator_module, "SETTLE_SECONDS", 0.0)
    return Coordinator(lobby)


def ask(coord, bot, game=XO, stake=STAKE):
    return coord.request_lease(bot, game, stake)


# -- layer 2: parity ------------------------------------------------------

@pytest.mark.parametrize("waiting,granted", [(0, False), (1, True), (2, False), (3, True), (4, False)])
def test_only_an_odd_queue_admits_a_bot(coord, lobby, waiting, granted):
    lobby.set(waiting)
    assert ask(coord, "bot-1")["granted"] is granted


def test_an_empty_table_is_refused_even_though_zero_is_even(coord, lobby):
    lobby.set(0)
    assert "nobody is waiting" in ask(coord, "bot-1")["reason"]


# -- layer 1: one lease per table ----------------------------------------

def test_a_second_bot_must_wait_its_turn(coord, lobby):
    lobby.set(3)
    assert ask(coord, "bot-1")["granted"]
    refusal = ask(coord, "bot-2")
    assert not refusal["granted"]
    assert refusal["waiting_turn"], "the console should show this as taking turns, not a fault"


def test_retrying_returns_the_same_lease(coord, lobby):
    lobby.set(3)
    first = ask(coord, "bot-1")
    assert ask(coord, "bot-1")["token"] == first["token"]


def test_a_queued_bot_blocks_the_table_even_after_releasing(coord, lobby):
    """Releasing early must not let a second bot stack into the same queue."""
    lobby.set(3)
    lease = ask(coord, "bot-1")
    coord.report("bot-1", "queued", game=XO, stake=STAKE)
    coord.release_lease("bot-1", lease["token"])

    lobby.set(4)  # bot-1 is now part of the count
    refusal = ask(coord, "bot-2")
    assert not refusal["granted"] and "already queuing" in refusal["reason"]


# -- heartbeats: the fix for a lease dying mid-wait -----------------------

def test_a_renewed_lease_survives_a_long_wait(coord, lobby, monkeypatch):
    """The old permit expired on a fixed timer while its holder still queued,
    which handed the table to a second bot.  Renewing must prevent that."""
    monkeypatch.setattr(coordinator_module, "LEASE_TIMEOUT_SECONDS", 0.25)
    lobby.set(3)
    lease = ask(coord, "bot-1")

    for _ in range(4):                       # keep queuing, keep renewing
        time.sleep(0.1)
        assert coord.renew_lease("bot-1", lease["token"])["ok"]

    assert not ask(coord, "bot-2")["granted"], "the table must still be held"


def test_a_lease_dies_when_its_holder_stops_renewing(coord, lobby, monkeypatch):
    monkeypatch.setattr(coordinator_module, "LEASE_TIMEOUT_SECONDS", 0.2)
    lobby.set(3)
    ask(coord, "bot-1")
    time.sleep(0.3)                          # bot-1 crashed; no heartbeat
    assert ask(coord, "bot-2")["granted"], "a dead bot must not wedge the table"


def test_renewing_an_expired_lease_tells_the_bot_to_stop_queuing(coord, lobby, monkeypatch):
    monkeypatch.setattr(coordinator_module, "LEASE_TIMEOUT_SECONDS", 0.2)
    lobby.set(3)
    lease = ask(coord, "bot-1")
    time.sleep(0.3)
    answer = coord.renew_lease("bot-1", lease["token"])
    assert not answer["ok"] and "request again" in answer["reason"]


def test_another_bots_token_cannot_renew_a_lease(coord, lobby):
    lobby.set(3)
    lease = ask(coord, "bot-1")
    assert not coord.renew_lease("bot-2", lease["token"])["ok"]


# -- discounting our own bots --------------------------------------------

def test_our_own_games_do_not_shift_the_parity(coord, lobby):
    """A bot and the human it plays contribute 2, so they cancel out."""
    for bot in ("bot-1", "bot-2"):
        coord.report(bot, "playing", game=XO, stake=STAKE)

    lobby.set(4)                              # our two bots plus their opponents
    refusal = ask(coord, "bot-3")
    assert not refusal["granted"] and refusal["humans"] == 0

    lobby.set(5)                              # one unpaired human arrives
    grant = ask(coord, "bot-3")
    assert grant["granted"] and grant["humans"] == 1


def test_a_bot_that_stopped_reporting_is_ignored(coord, lobby):
    coord.report("ghost", "queued", game=XO, stake=STAKE)
    coord._telemetry["ghost"].updated_at -= coordinator_module.STALE_REPORT_SECONDS + 1
    lobby.set(1)
    assert ask(coord, "bot-1")["granted"]


def test_bots_on_another_table_do_not_count(coord, lobby):
    coord.report("other", "queued", game="chess", stake=STAKE)
    lobby.set(1)
    assert ask(coord, "bot-1")["granted"]


def test_each_table_is_leased_independently(coord, lobby):
    lobby.set(1, XO, 5)
    lobby.set(1, XO, 30)
    assert ask(coord, "bot-1", XO, 5)["granted"]
    assert ask(coord, "bot-2", XO, 30)["granted"], "a different stake is a different table"


# -- layer 3: opponent verification --------------------------------------

def test_an_ordinary_opponent_is_fine(coord):
    coord.register("bot-1", account_id="xo-1", display_name="Selam", phone_tail="1111")
    assert coord.check_opponent("bot-1", "Abebe", "9999") == {"ours": False}


def test_being_paired_with_our_own_account_is_caught_by_phone(coord):
    coord.register("bot-1", account_id="xo-1", display_name="Selam", phone_tail="1111")
    coord.register("bot-2", account_id="xo-2", display_name="Eyob", phone_tail="2222")
    coord.report("bot-1", "matched", game=XO, stake=STAKE)

    verdict = coord.check_opponent("bot-1", "****2222", "2222")
    assert verdict["ours"] and "xo-2" in verdict["reason"]


def test_being_paired_with_our_own_account_is_caught_by_name(coord):
    coord.register("bot-1", account_id="xo-1", display_name="Selam", phone_tail="1111")
    coord.register("bot-2", account_id="xo-2", display_name="Eyob", phone_tail="")
    coord.report("bot-1", "matched", game=XO, stake=STAKE)
    assert coord.check_opponent("bot-1", "eyob", "")["ours"]


def test_a_bot_is_not_its_own_opponent(coord):
    coord.register("bot-1", account_id="xo-1", display_name="Selam", phone_tail="1111")
    coord.report("bot-1", "matched", game=XO, stake=STAKE)
    assert not coord.check_opponent("bot-1", "Selam", "1111")["ours"]


def test_a_self_pair_pauses_that_table(coord, lobby):
    coord.register("bot-1", account_id="xo-1", display_name="Selam", phone_tail="1111")
    coord.register("bot-2", account_id="xo-2", display_name="Eyob", phone_tail="2222")
    coord.report("bot-1", "matched", game=XO, stake=STAKE)
    coord.check_opponent("bot-1", "****2222", "2222")

    assert KEY in coord.paused_tables()
    lobby.set(3)
    refusal = ask(coord, "bot-3")
    assert not refusal["granted"] and "paused" in refusal["reason"]


def test_an_operator_can_resume_a_paused_table(coord, lobby):
    coord.register("bot-1", account_id="xo-1", phone_tail="1111")
    coord.register("bot-2", account_id="xo-2", phone_tail="2222")
    coord.report("bot-1", "matched", game=XO, stake=STAKE)
    coord.check_opponent("bot-1", "****2222", "2222")

    assert coord.resume(KEY)
    assert not coord.resume(KEY), "resuming twice is not an error the second time"
    lobby.set(3)
    assert ask(coord, "bot-3")["granted"]


def test_forgetting_a_bot_frees_its_lease_and_identity(coord, lobby):
    lobby.set(3)
    coord.register("bot-1", account_id="xo-1", phone_tail="1111")
    ask(coord, "bot-1")
    coord.forget("bot-1")
    assert ask(coord, "bot-2")["granted"]
    assert not coord.identities()


# -- stakes and the gate --------------------------------------------------

def test_a_stake_the_site_stopped_offering_is_reported_distinctly(coord, lobby):
    lobby.set(3)
    refusal = ask(coord, "bot-1", stake=10)
    assert refusal["stake_unavailable"]
    assert "5, 30" in refusal["reason"], "the operator needs to see what is on offer"
    assert refusal["retry_after"] >= 10, "must not be retried in a tight loop"


def test_disabling_the_gate_admits_a_bot_to_an_even_queue(coord, lobby):
    coord.odd_gate_enabled = False
    lobby.set(2)
    assert ask(coord, "bot-1")["granted"]


def test_disabling_the_gate_still_leases_one_bot_at_a_time(coord, lobby):
    coord.odd_gate_enabled = False
    lobby.set(2)
    ask(coord, "bot-1")
    assert not ask(coord, "bot-2")["granted"], "turn-taking is not optional"


def test_the_settle_cooldown_spaces_out_grants(lobby):
    coord = Coordinator(lobby)  # real SETTLE_SECONDS
    lobby.set(3)
    lease = ask(coord, "bot-1")
    coord.release_lease("bot-1", lease["token"])
    refusal = ask(coord, "bot-2")
    assert not refusal["granted"] and "settle" in refusal["reason"]


# -- telemetry ------------------------------------------------------------

def test_results_are_tallied_per_bot(coord):
    for result in ("You won", "You lost", "Draw", "Abebe won"):
        coord.report("bot-1", "finished", game=XO, stake=STAKE, result=result)
    telemetry = coord.telemetry("bot-1")
    assert (telemetry["matches"], telemetry["wins"], telemetry["losses"], telemetry["draws"]) == (4, 1, 2, 1)


# -- the whole sequence ---------------------------------------------------

def test_two_bots_take_turns_across_a_full_cycle(coord, lobby):
    """The behaviour asked for: bot B waits while A queues, waits while A plays,
    takes the table when A is done, and A then waits for B."""
    lobby.set(1)                                            # one human waiting
    a = ask(coord, "bot-a")
    assert a["granted"]
    coord.report("bot-a", "queued", game=XO, stake=STAKE)

    assert ask(coord, "bot-b")["waiting_turn"], "B waits while A queues"

    coord.report("bot-a", "playing", game=XO, stake=STAKE)  # A got paired
    coord.release_lease("bot-a", a["token"])
    lobby.set(2)                                            # A + its opponent
    assert not ask(coord, "bot-b")["granted"], "no unpaired human yet"

    lobby.set(3)                                            # a human arrives
    b = ask(coord, "bot-b")
    assert b["granted"], "B takes its turn"
    coord.report("bot-b", "queued", game=XO, stake=STAKE)

    coord.report("bot-a", "finished", game=XO, stake=STAKE, result="You won")
    coord.report("bot-a", "idle", game=XO, stake=STAKE)
    assert ask(coord, "bot-a")["waiting_turn"], "A now waits for B, rather than rejoining"
