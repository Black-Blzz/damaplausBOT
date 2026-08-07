from __future__ import annotations

import random

import chess

VALUES = {chess.PAWN: 100, chess.KNIGHT: 320, chess.BISHOP: 330, chess.ROOK: 500, chess.QUEEN: 900, chess.KING: 0}


def legal_moves(board: chess.Board) -> list[chess.Move]:
    """The frontend has no en-passant support, so never offer that move."""
    return [move for move in board.legal_moves if not board.is_en_passant(move)]


def evaluate(board: chess.Board, perspective: chess.Color) -> int:
    if board.is_checkmate():
        return -100_000 if board.turn == perspective else 100_000
    score = sum((1 if piece.color == perspective else -1) * VALUES[piece.piece_type]
                for piece in board.piece_map().values())
    return score + (12 if board.is_check() and board.turn != perspective else 0)


def choose_move(board: chess.Board, depth: int = 3, elo: int = 300) -> chess.Move:
    moves = legal_moves(board)
    if not moves:
        raise ValueError("no legal Chess moves")

    # Even a beginner can finish a mate that is already on the board.
    for move in moves:
        board.push(move)
        is_mate = board.is_checkmate()
        board.pop()
        if is_mate:
            return move

    # Elo is not an exact property of a standalone engine, but a 300-rated
    # player blunders very frequently.  Make most moves uniformly random and
    # only occasionally use the material search.  This also keeps decisions
    # instantaneous instead of stalling the browser game loop.
    if elo <= 400 and random.random() < 0.85:
        return random.choice(moves)
    bot = board.turn

    def search(level: int, alpha: int, beta: int) -> int:
        if level == 0 or board.is_game_over():
            return evaluate(board, bot)
        maximizing = board.turn == bot
        value = -1_000_000 if maximizing else 1_000_000
        for move in legal_moves(board):
            board.push(move)
            child = search(level - 1, alpha, beta)
            board.pop()
            if maximizing:
                value, alpha = max(value, child), max(alpha, value)
            else:
                value, beta = min(value, child), min(beta, value)
            if beta <= alpha:
                break
        return value

    best, score = moves[0], -1_000_000
    for move in moves:
        board.push(move)
        value = search(max(0, depth - 1), -1_000_000, 1_000_000)
        board.pop()
        if value > score:
            best, score = move, value
    return best
