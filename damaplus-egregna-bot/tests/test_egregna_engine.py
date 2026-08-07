from egregna_bot.engine import StrongEngine, apply_move, legal_moves
from egregna_bot.model import Color, Piece, Position


def position(items, turn=Color.BLACK):
    return Position.from_mapping(dict(items), turn)


def test_capture_is_mandatory():
    state = position([((5, 0), Piece(Color.BLACK)), ((4, 1), Piece(Color.WHITE)), ((5, 4), Piece(Color.BLACK))])
    moves = legal_moves(state)
    assert len(moves) == 1
    assert moves[0].path == ((5, 0), (3, 2))
    assert moves[0].captures == ((4, 1),)


def test_capture_chains_are_generated_without_a_maximum_capture_requirement():
    state = position([
        ((5, 0), Piece(Color.BLACK)), ((4, 1), Piece(Color.WHITE)), ((2, 3), Piece(Color.WHITE)),
        ((5, 4), Piece(Color.BLACK)), ((4, 5), Piece(Color.WHITE)),
    ])
    moves = legal_moves(state)
    assert moves
    assert {len(move.captures) for move in moves} == {1, 2}
    assert any(move.path == ((5, 0), (3, 2), (1, 4)) for move in moves)


def test_king_uses_one_square_steps_and_adjacent_jumps():
    state = position([((6, 1), Piece(Color.BLACK, True)), ((5, 2), Piece(Color.WHITE))])
    moves = legal_moves(state)
    landings = {move.path[-1] for move in moves}
    assert landings == {(4, 3)}


def test_egregna_cannot_capture_a_king():
    state = position([((5, 0), Piece(Color.BLACK)), ((4, 1), Piece(Color.WHITE, True))])
    moves = legal_moves(state)
    assert all(not move.captures for move in moves)


def test_promotion_after_move():
    state = position([((1, 2), Piece(Color.BLACK))])
    move = next(move for move in legal_moves(state) if move.path[-1] == (0, 1))
    result = apply_move(state, move)
    assert result.mapping()[(0, 1)] == Piece(Color.BLACK, True)


def test_hard_engine_selects_a_legal_move():
    state = position([((5, 0), Piece(Color.BLACK)), ((4, 1), Piece(Color.WHITE))])
    move = StrongEngine(depth=3, time_limit_seconds=1).choose(state)
    assert move in legal_moves(state)


def test_easy_engine_selects_a_legal_move():
    state = position([((5, 0), Piece(Color.BLACK)), ((5, 2), Piece(Color.BLACK))])
    move = StrongEngine().choose(state, "easy")
    assert move in legal_moves(state)
