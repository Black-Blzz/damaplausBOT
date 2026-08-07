from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field

from .model import Color, Move, Piece, Position, Square

DIRS = ((-1, -1), (-1, 1), (1, -1), (1, 1))


def inside(s: Square) -> bool:
    return 0 <= s[0] < 8 and 0 <= s[1] < 8


def advance(square: Square, direction: tuple[int, int]) -> Square:
    return square[0] + direction[0], square[1] + direction[1]


def promotes(color: Color, square: Square) -> bool:
    return square[0] == (0 if color is Color.BLACK else 7)


def legal_moves(position: Position) -> list[Move]:
    """Dama Egregna: compulsory captures, forward men, and short kings."""
    board = position.mapping()
    captures: list[Move] = []
    for square, piece in board.items():
        if piece.color is position.turn:
            search_board = board.copy()
            del search_board[square]
            captures.extend(_capture_moves(search_board, square, piece, (square,), (), frozenset()))
    captures = [m for m in captures if m.captures]
    if captures:
        return captures
    return [m for square, piece in board.items() if piece.color is position.turn
            for m in _quiet_moves(board, square, piece)]


def _quiet_moves(board: dict[Square, Piece], start: Square, piece: Piece) -> list[Move]:
    directions = DIRS if piece.king else ((-1, -1), (-1, 1)) if piece.color is Color.BLACK else ((1, -1), (1, 1))
    moves: list[Move] = []
    for direction in directions:
        target = advance(start, direction)
        while inside(target) and target not in board:
            moves.append(Move((start, target)))
            break
    return moves


def _capture_moves(board: dict[Square, Piece], current: Square, piece: Piece,
                   path: tuple[Square, ...], captures: tuple[Square, ...],
                   previously_captured: frozenset[Square]) -> list[Move]:
    """Egregna captures are adjacent jumps; men only capture forward."""
    options: list[tuple[Square, Square]] = []
    directions = DIRS if piece.king else ((-1, -1), (-1, 1)) if piece.color is Color.BLACK else ((1, -1), (1, 1))
    for direction in directions:
        cursor = advance(current, direction)
        if not inside(cursor):
            continue
        victim = cursor
        victim_piece = board.get(victim)
        if (not victim_piece or victim_piece.color is piece.color or victim_piece.king
                or victim in previously_captured):
            continue
        landing = advance(victim, direction)
        if inside(landing) and (landing not in board or landing in previously_captured):
            options.append((victim, landing))
    if not options:
        return [Move(path, captures)] if captures else []
    result: list[Move] = []
    for victim, landing in options:
        next_piece = Piece(piece.color, piece.king or promotes(piece.color, landing))
        result.extend(_capture_moves(board, landing, next_piece, path + (landing,),
                                     captures + (victim,), previously_captured | {victim}))
    return result


def apply_move(position: Position, move: Move) -> Position:
    board = position.mapping()
    piece = board.pop(move.path[0])
    for captured in move.captures:
        board.pop(captured, None)
    end = move.path[-1]
    board[end] = Piece(piece.color, piece.king or promotes(piece.color, end))
    return Position.from_mapping(board, position.turn.other())


def evaluate(position: Position, perspective: Color) -> int:
    score = 0
    for (row, _), piece in position.pieces:
        value = 175 if piece.king else 100
        if not piece.king:
            # Advancement towards crowning adds a small, stable positional term.
            value += (7 - row if piece.color is Color.BLACK else row) * 5
        score += value if piece.color is perspective else -value
    return score


@dataclass
class StrongEngine:
    depth: int = 7
    time_limit_seconds: float = 2.5
    _table: dict[tuple[Position, int], int] = field(default_factory=dict, init=False)

    def choose(self, position: Position, tier: str = "hard") -> Move:
        moves = legal_moves(position)
        if not moves:
            raise ValueError("cannot select a move from a finished position")
        if tier == "easy":
            return self._easy(position, moves)
        if tier == "medium":
            return self._medium(position, moves)
        return self._hard(position, moves)

    def _easy(self, position: Position, moves: list[Move]) -> Move:
        # Beginner level: make no attempt to evaluate or protect pieces.  The
        # rules engine still supplies only legal moves (including compulsory
        # captures), but every available choice is equally likely.
        return random.choice(moves)

    def _medium(self, position: Position, moves: list[Move]) -> Move:
        def score(move: Move) -> int:
            after = apply_move(position, move)
            return (len(move.captures) * 120 + self._one_ply_safety(position, move)
                    + evaluate(after, position.turn) // 8)
        ranked = sorted(((score(m), m) for m in moves), reverse=True, key=lambda x: x[0])
        # Small variety among close good choices makes the bot less mechanical.
        return random.choice([m for s, m in ranked if s >= ranked[0][0] - 18])

    def _one_ply_safety(self, position: Position, move: Move) -> int:
        after = apply_move(position, move)
        replies = legal_moves(after)
        if not replies:
            return 10_000
        worst = min(evaluate(apply_move(after, reply), position.turn) for reply in replies)
        return worst

    def _hard(self, position: Position, moves: list[Move]) -> Move:
        deadline = time.monotonic() + self.time_limit_seconds
        best = moves[0]
        self._table.clear()
        for depth in range(1, self.depth + 1):
            try:
                ordered = self._ordered(position, moves)
                candidate, _ = self._search_root(position, ordered, depth, deadline)
                best = candidate
            except TimeoutError:
                break
        return best

    def _search_root(self, position: Position, moves: list[Move], depth: int, deadline: float) -> tuple[Move, int]:
        best_move, best_score, alpha, beta = moves[0], -math.inf, -math.inf, math.inf
        for move in moves:
            score = -self._negamax(apply_move(position, move), depth - 1, -beta, -alpha, deadline)
            if score > best_score:
                best_move, best_score = move, score
            alpha = max(alpha, score)
        return best_move, int(best_score)

    def _negamax(self, position: Position, depth: int, alpha: float, beta: float, deadline: float) -> int:
        if time.monotonic() >= deadline:
            raise TimeoutError
        key = (position, depth)
        if key in self._table:
            return self._table[key]
        moves = legal_moves(position)
        if not moves:
            return -50_000 - depth
        if depth == 0:
            return evaluate(position, position.turn)
        value = -math.inf
        for move in self._ordered(position, moves):
            value = max(value, -self._negamax(apply_move(position, move), depth - 1, -beta, -alpha, deadline))
            alpha = max(alpha, value)
            if alpha >= beta:
                break
        self._table[key] = int(value)
        return int(value)

    @staticmethod
    def _ordered(position: Position, moves: list[Move]) -> list[Move]:
        return sorted(moves, key=lambda m: (len(m.captures), evaluate(apply_move(position, m), position.turn)), reverse=True)
