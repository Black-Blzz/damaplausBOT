from __future__ import annotations

from functools import lru_cache
import random

LINES = ((0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6))
ORDER = (4, 0, 2, 6, 8, 1, 3, 5, 7)


def winner(board: tuple[str, ...]) -> str | None:
    for a, b, c in LINES:
        if board[a] and board[a] == board[b] == board[c]:
            return board[a]
    return None


@lru_cache(maxsize=None)
def _score(board: tuple[str, ...], turn: str, bot: str, depth: int = 0) -> int:
    """Minimax value of a position, counting how soon the result arrives.

    Scoring a win as ``10 - depth`` makes the bot take the shortest win and,
    when it is losing, the longest defence.  An earlier version nudged internal
    nodes by one instead, which cancelled out: a mate-in-one and a mate-in-five
    scored identically, so the bot would play the centre rather than complete a
    line it had already won.
    """
    won = winner(board)
    if won:
        return (10 - depth) if won == bot else (depth - 10)
    empty = [index for index in ORDER if not board[index]]
    if not empty:
        return 0
    values = []
    for index in empty:
        next_board = list(board)
        next_board[index] = turn
        values.append(_score(tuple(next_board), "O" if turn == "X" else "X", bot, depth + 1))
    return max(values) if turn == bot else min(values)


def choose_move(
    board: tuple[str, ...],
    bot_mark: str,
    mistake_rate: float = 0.0,
    rng: random.Random | None = None,
) -> int:
    """Choose a move by full minimax over the whole game tree.

    At the default ``mistake_rate`` of 0 the bot plays perfectly, and since
    tic-tac-toe is a solved game that means it cannot lose -- the worst
    available result is a draw.  A non-zero rate deliberately picks a
    worse-scoring move, which hands the opponent real chances; the shipped
    config asks for zero.
    """
    if not 0.0 <= mistake_rate <= 1.0:
        raise ValueError("mistake_rate must be between 0 and 1")
    options = [index for index in ORDER if not board[index]]
    if not options:
        raise ValueError("XO board is full")
    scores = {
        index: _score(
            tuple(bot_mark if i == index else value for i, value in enumerate(board)),
            "O" if bot_mark == "X" else "X",
            bot_mark,
            1,
        )
        for index in options
    }
    best_score = max(scores.values())
    weaker_options = [index for index in options if scores[index] < best_score]
    chooser = rng or random
    if weaker_options and chooser.random() < mistake_rate:
        return chooser.choice(weaker_options)
    return next(index for index in options if scores[index] == best_score)
