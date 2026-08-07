import random

from xo_bot.engine import choose_move


def test_takes_a_winning_move():
    assert choose_move(("X", "X", "", "O", "O", "", "", "", ""), "X") == 2


def test_blocks_an_immediate_loss():
    assert choose_move(("X", "X", "", "", "O", "", "", "", ""), "O") == 2


def test_chooses_the_centre_on_an_empty_board():
    assert choose_move(("",) * 9, "X") == 4


def test_can_intentionally_choose_a_weaker_move():
    board = ("X", "O", "", "", "", "", "", "", "")
    optimal = choose_move(board, "X")
    weaker = choose_move(board, "X", mistake_rate=1.0, rng=random.Random(0))

    assert weaker != optimal


def test_rejects_an_invalid_mistake_rate():
    try:
        choose_move(("",) * 9, "X", mistake_rate=1.1)
    except ValueError as error:
        assert "mistake_rate" in str(error)
    else:
        raise AssertionError("expected invalid mistake rate to be rejected")
