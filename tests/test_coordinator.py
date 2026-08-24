"""Tests for the entry-permit rules that keep bots away from each other.

Two things must hold no matter what the site reports:

* only one of our bots is ever entering a table at a time, and
* a bot enters only when an odd number of players is waiting there.
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


class FakeLobby:
    """Stands in for the site so counts can be set exactly."""

    def __init__(self, online=None, stakes=(5, 30)):
        self.online = dict(online or {})
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
    # The settle cooldown exists to let the live count catch up; it only gets
    # in the way of deterministic tests.
    monkeypatch.setattr(coordinator_module, "SETTLE_SECONDS", 0.0)
    return Coordinator(lobby)


def ask(coord, bot: str, game: str = XO, stake: int = STAKE) -> dict:
    return coord.request_permit(bot, game, stake)


# -- parity ---------------------------------------------------------------

@pytest.mark.parametrize("waiting,granted", [(0, False), (1, True), (2, False), (3, True), (4, False)])
def test_only_an_odd_queue_admits_a_bot(coord, lobby, waiting, granted):
    lobby.set(waiting)
    assert ask(coord, "bot-1")["granted"] is granted


def test_an_empty_table_is_refused_even_though_zero_is_even(coord, lobby):
    lobby.set(0)
    assert "nobody is waiting" in ask(coord, "bot-1")["reason"]


# -- serialisation --------------------------------------------------------

def test_a_second_bot_cannot_enter_while_the_first_holds_a_permit(coord, lobby):
    lobby.set(3)
    assert ask(coord, "bot-1")["granted"]
    refusal = ask(coord, "bot-2")
    assert not refusal["granted"]
    assert "another bot" in refusal["reason"]


def test_retrying_returns_the_same_permit(coord, lobby):
    lobby.set(3)
    first = ask(coord, "bot-1")
    assert ask(coord, "bot-1")["token"] == first["token"]


def test_a_queued_bot_blocks_the_table_even_after_releasing(coord, lobby):
    """Releasing early must not let a second bot stack into the same queue."""
    lobby.set(3)
    permit = ask(coord, "bot-1")
    coord.report("bot-1", "queued", game=XO, stake=STAKE)
    coord.release("bot-1", permit["token"])

    lobby.set(4)  # bot-1 is now part of the count
    refusal = ask(coord, "bot-2")
    assert not refusal["granted"]
    assert "already queuing" in refusal["reason"]


def test_an_expired_permit_frees_the_table(coord, lobby, monkeypatch):
    lobby.set(3)
    assert ask(coord, "bot-1")["granted"]
    monkeypatch.setattr(coordinator_module, "PERMIT_TTL_SECONDS", 0.0)
    assert ask(coord, "bot-2")["granted"], "a crashed bot must not wedge the table"


# -- discounting our own bots --------------------------------------------

def test_our_own_games_do_not_shift_the_parity(coord, lobby):
    """A bot and the human it is playing contribute 2, so they cancel out."""
    for bot in ("bot-1", "bot-2"):
        coord.report(bot, "playing", game=XO, stake=STAKE)

    lobby.set(4)  # exactly our two bots plus their two opponents
    refusal = ask(coord, "bot-3")
    assert not refusal["granted"] and refusal["humans"] == 0

    lobby.set(5)  # one unpaired human arrives
    grant = ask(coord, "bot-3")
    assert grant["granted"] and grant["humans"] == 1


def test_a_queued_bot_of_ours_is_subtracted_from_the_count(coord, lobby):
    coord.report("bot-1", "queued", game=XO, stake=STAKE)
    lobby.set(3)  # 2 humans + our queued bot
    row = next(r for r in coord.lobby_view([XO]) if r["stake"] == STAKE)
    assert (row["online"], row["ours_queued"], row["humans"]) == (3, 1, 2)


def test_a_bot_that_stopped_reporting_is_ignored(coord, lobby):
    coord.report("ghost", "queued", game=XO, stake=STAKE)
    coord._telemetry["ghost"].updated_at -= coordinator_module.STALE_REPORT_SECONDS + 1
    lobby.set(1)
    assert ask(coord, "bot-1")["granted"]


def test_bots_on_another_table_do_not_count(coord, lobby):
    coord.report("other", "queued", game="chess", stake=STAKE)
    lobby.set(1)
    assert ask(coord, "bot-1")["granted"]


# -- stakes ---------------------------------------------------------------

def test_a_stake_the_site_stopped_offering_is_reported_distinctly(coord, lobby):
    lobby.set(3)
    refusal = ask(coord, "bot-1", stake=10)
    assert refusal["stake_unavailable"]
    assert "5, 30" in refusal["reason"], "the operator needs to see what is on offer"
    assert refusal["retry_after"] >= 10, "must not be retried in a tight loop"


# -- the gate itself ------------------------------------------------------

def test_disabling_the_gate_admits_a_bot_to_an_even_queue(coord, lobby):
    coord.odd_gate_enabled = False
    lobby.set(2)
    assert ask(coord, "bot-1")["granted"]


def test_disabling_the_gate_still_serialises_entry(coord, lobby):
    coord.odd_gate_enabled = False
    lobby.set(2)
    ask(coord, "bot-1")
    assert not ask(coord, "bot-2")["granted"]


def test_the_settle_cooldown_spaces_out_grants(lobby):
    coord = Coordinator(lobby)  # real SETTLE_SECONDS
    lobby.set(3)
    permit = ask(coord, "bot-1")
    coord.release("bot-1", permit["token"])
    refusal = ask(coord, "bot-2")
    assert not refusal["granted"] and "settle" in refusal["reason"]


# -- telemetry ------------------------------------------------------------

def test_results_are_tallied_per_bot(coord):
    for result in ("You won", "You lost", "Draw", "you won the match"):
        coord.report("bot-1", "finished", game=XO, stake=STAKE, result=result)
    telemetry = coord.telemetry("bot-1")
    assert (telemetry["matches"], telemetry["wins"], telemetry["losses"], telemetry["draws"]) == (4, 2, 1, 1)


def test_forgetting_a_bot_releases_its_permit(coord, lobby):
    lobby.set(3)
    ask(coord, "bot-1")
    coord.forget("bot-1")
    assert ask(coord, "bot-2")["granted"]
