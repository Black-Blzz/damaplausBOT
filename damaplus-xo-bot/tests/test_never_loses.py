"""Proof that the XO bot cannot lose.

Tic-tac-toe is a solved game: perfect play draws at worst. These tests do not
sample or spot-check — they walk the *entire* game tree with the opponent
allowed every legal reply at every turn, from both sides, and assert the bot
never reaches a lost position.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from xo_bot.engine import choose_move, winner

EMPTY = ("",) * 9


def other(mark: str) -> str:
    return "O" if mark == "X" else "X"


def open_cells(board: tuple[str, ...]) -> list[int]:
    return [i for i, value in enumerate(board) if not value]


def place(board: tuple[str, ...], index: int, mark: str) -> tuple[str, ...]:
    return tuple(mark if i == index else value for i, value in enumerate(board))


def explore(board: tuple[str, ...], turn: str, bot: str, results: set[str]) -> None:
    """Walk every continuation, with the opponent trying everything."""
    won = winner(board)
    if won:
        results.add("win" if won == bot else "loss")
        return
    if not open_cells(board):
        results.add("draw")
        return

    if turn == bot:
        # The bot plays its single chosen move.
        explore(place(board, choose_move(board, bot, mistake_rate=0.0), bot),
                other(turn), bot, results)
    else:
        # The opponent may do anything at all, including play perfectly.
        for index in open_cells(board):
            explore(place(board, index, turn), other(turn), bot, results)


@pytest.mark.parametrize("bot_mark", ["X", "O"])
def test_the_bot_never_loses_against_any_opponent(bot_mark):
    """Exhaustive: every legal opponent line, from an empty board."""
    results: set[str] = set()
    explore(EMPTY, "X", bot_mark, results)
    assert "loss" not in results, f"the bot lost at least one line as {bot_mark}"
    assert results <= {"win", "draw"}
    assert "draw" in results, "a perfect opponent should still force a draw"


def test_going_first_the_bot_still_cannot_lose():
    results: set[str] = set()
    explore(EMPTY, "X", "X", results)
    assert "loss" not in results
    assert "win" in results, "an imperfect opponent should get punished"


def test_a_won_position_is_taken_immediately():
    board = ("X", "X", "", "O", "O", "", "", "", "")
    assert choose_move(board, "X", mistake_rate=0.0) == 2


def test_a_lost_position_is_defended_for_as_long_as_possible():
    """This position is already lost -- O forks from the centre whatever X does.

    The bot cannot reach it playing perfectly from the start, but if it is ever
    handed one it should still block rather than resign the fastest line.
    """
    board = ("O", "O", "", "X", "", "", "", "", "")
    assert choose_move(board, "X", mistake_rate=0.0) == 2


def test_a_win_is_taken_at_once_rather_than_postponed():
    """Regression: several moves all led to a forced win, and the engine could
    not tell them apart, so it played the centre instead of completing the row."""
    board = ("X", "X", "", "O", "", "", "O", "", "")
    assert choose_move(board, "X", mistake_rate=0.0) == 2, "finish it now"


def test_the_configured_bot_makes_no_mistakes():
    """The shipped config must ask for perfect play."""
    import json
    config = json.loads(
        (Path(__file__).resolve().parent.parent / "config.json").read_text(encoding="utf-8"))
    assert config["difficulty_weights"]["mistake_rate"] == 0.0


def test_a_mistake_rate_would_reintroduce_losses():
    """Guards the setting above: with mistakes allowed, losing lines exist."""
    rng_always_slips = type("R", (), {"random": staticmethod(lambda: 0.0),
                                      "choice": staticmethod(lambda seq: seq[0])})()
    board = ("O", "O", "", "X", "", "", "", "", "")
    slip = choose_move(board, "X", mistake_rate=1.0, rng=rng_always_slips)
    assert slip != 2, "a deliberate mistake should decline the block"
