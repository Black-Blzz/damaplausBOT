import chess
from chess_bot.engine import choose_move, legal_moves


def test_takes_checkmate_when_available():
    board = chess.Board("6k1/5Q2/6K1/8/8/8/8/8 w - - 0 1")
    board.push(choose_move(board, 2))
    assert board.is_checkmate()


def test_excludes_en_passant():
    board = chess.Board("rnbqkbnr/ppp1pppp/8/3pP3/8/8/PPPP1PPP/RNBQKBNR w KQkq d6 0 3")
    assert all(not board.is_en_passant(move) for move in legal_moves(board))


def test_keeps_castling_move():
    board = chess.Board("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1")
    assert chess.Move.from_uci("e1g1") in legal_moves(board)
