"""Reading match results and wallet balances off the site's own wording."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from botkit.results import DRAW, LOSS, UNKNOWN, WIN, classify_result
from botkit.wallet import parse_money, shortfall


# Every phrase below is one damaplus.online actually renders -- see
# finishReasonMessage and winnerStatusLabel in its app.js.
@pytest.mark.parametrize("text,expected", [
    ("You won by checkmate.", WIN),
    ("You lost by checkmate.", LOSS),
    ("You won because your opponent ran out of time.", WIN),
    ("You lost because your time expired.", LOSS),
    ("You won by capturing every opponent piece.", WIN),
    ("You lost because all your pieces were captured.", LOSS),
    ("You won because your opponent had no legal move.", WIN),
    ("You lost because you had no legal move.", LOSS),
    ("You won because your opponent resigned.", WIN),
    ("You won", WIN),
    ("Draw", DRAW),
    ("DRAW", DRAW),
    ("Won", WIN),
    ("Lost", LOSS),
])
def test_site_phrases_are_read_correctly(text, expected):
    assert classify_result(text) == expected


@pytest.mark.parametrize("text", [
    "Abebe won",
    "Sara Tesfaye won",
    "Player 2 wins",
    "Bot-xo-3 won",
])
def test_an_opponent_winning_is_a_loss(text):
    """The regression behind wins showing up on the dashboard for lost games.

    ``winnerStatusLabel`` renders somebody else's victory as "<name> won", so a
    substring test for "won" scored every one of these as ours.
    """
    assert classify_result(text) == LOSS


@pytest.mark.parametrize("text", ["Your turn (White)", "Waiting for player", "", None, "   "])
def test_non_results_are_not_counted(text):
    assert classify_result(text) == UNKNOWN


def test_a_result_is_never_both():
    """Whatever the wording, exactly one bucket is chosen."""
    for text in ("You won by checkmate.", "You lost by checkmate.", "Abebe won", "Draw"):
        assert classify_result(text) in {WIN, LOSS, DRAW}


# -- wallet ---------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("0 birr", 0.0),
    ("45 birr", 45.0),
    ("1,250 birr", 1250.0),
    ("12.50 birr", 12.5),
    ("", None),
    (None, None),
    ("no digits", None),
])
def test_balance_parsing(text, expected):
    assert parse_money(text) == expected


@pytest.mark.parametrize("balance,stake,stops", [
    (50, 5, False),
    (10, 5, False),      # exactly the floor is still playable
    (9.5, 5, True),
    (0, 5, True),
    (20, 30, True),      # clears the floor but cannot cover the stake
])
def test_when_a_bot_should_stop_entering(balance, stake, stops):
    assert bool(shortfall(balance, stake, 10.0)) is stops


def test_an_unreadable_balance_does_not_halt_a_bot():
    """Failing open is safer: the site refuses an unfunded join anyway."""
    assert shortfall(None, 5, 10.0) == ""
