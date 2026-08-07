from dama_bot.engine import StrongEngine, apply_move, legal_moves
from dama_bot.model import Color, Piece, Position


def position(items, turn=Color.BLACK):
    return Position.from_mapping(dict(items), turn)


def test_capture_is_mandatory():
    state = position([((5, 0), Piece(Color.BLACK)), ((4, 1), Piece(Color.WHITE)), ((5, 4), Piece(Color.BLACK))])
    moves = legal_moves(state)
    assert len(moves) == 1
    assert moves[0].path == ((5, 0), (3, 2))
    assert moves[0].captures == ((4, 1),)


def test_only_longest_capture_sequences_are_legal():
    state = position([
        ((5, 0), Piece(Color.BLACK)), ((4, 1), Piece(Color.WHITE)), ((2, 3), Piece(Color.WHITE)),
        ((5, 4), Piece(Color.BLACK)), ((4, 5), Piece(Color.WHITE)),
    ])
    moves = legal_moves(state)
    assert moves
    assert all(len(move.captures) == 2 for move in moves)
    assert moves[0].path == ((5, 0), (3, 2), (1, 4))


def test_flying_king_can_land_beyond_captured_piece():
    state = position([((6, 1), Piece(Color.BLACK, True)), ((4, 3), Piece(Color.WHITE))])
    moves = legal_moves(state)
    landings = {move.path[-1] for move in moves}
    assert landings == {(3, 4), (2, 5), (1, 6), (0, 7)}


def test_promotion_after_move():
    state = position([((1, 2), Piece(Color.BLACK))])
    move = next(move for move in legal_moves(state) if move.path[-1] == (0, 1))
    result = apply_move(state, move)
    assert result.mapping()[(0, 1)] == Piece(Color.BLACK, True)


def test_hard_engine_selects_a_legal_move():
    state = position([((5, 0), Piece(Color.BLACK)), ((4, 1), Piece(Color.WHITE))])
    move = StrongEngine(depth=3, time_limit_seconds=1).choose(state)
    assert move in legal_moves(state)


def test_beginner_engine_selects_a_legal_move():
    state = position([((5, 0), Piece(Color.BLACK)), ((5, 2), Piece(Color.BLACK))])
    move = StrongEngine().choose(state, "beginner")
    assert move in legal_moves(state)
