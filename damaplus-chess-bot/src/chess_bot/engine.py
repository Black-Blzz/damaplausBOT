from __future__ import annotations

import atexit
import random
import threading
from pathlib import Path

import chess
import chess.engine

VALUES = {chess.PAWN: 100, chess.KNIGHT: 320, chess.BISHOP: 330, chess.ROOK: 500, chess.QUEEN: 900, chess.KING: 0}


def legal_moves(board: chess.Board) -> list[chess.Move]:
    """Return every legal move, including en passant and castling."""
    return list(board.legal_moves)


def evaluate(board: chess.Board, perspective: chess.Color) -> int:
    if board.is_checkmate():
        return -100_000 if board.turn == perspective else 100_000
    score = sum((1 if piece.color == perspective else -1) * VALUES[piece.piece_type]
                for piece in board.piece_map().values())
    return score + (12 if board.is_check() and board.turn != perspective else 0)


def choose_move(board: chess.Board, depth: int = 3, elo: int = 1000) -> chess.Move:
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

    # Elo is not an exact property of a standalone engine. Below 400, inject
    # frequent blunders; at the configured 1000 level, use the full search.
    # This keeps weaker presets distinct without slowing the browser loop.
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


class _StockfishSession:
    """One long-lived Stockfish process, shared by every move of a game.

    Starting a fresh engine per move cost a process launch and threw away the
    transposition table each time -- slow, and weaker for the same time budget.
    """

    def __init__(self) -> None:
        self._engine: chess.engine.SimpleEngine | None = None
        self._settings: tuple | None = None
        self._lock = threading.Lock()

    def engine(self, executable: Path, threads: int, hash_mb: int,
               skill_level: int) -> chess.engine.SimpleEngine:
        wanted = (str(executable), threads, hash_mb, skill_level)
        if self._engine is not None and self._settings == wanted:
            return self._engine
        self.close()
        if not executable.is_file():
            raise FileNotFoundError(f"Stockfish executable not found: {executable}")
        engine = chess.engine.SimpleEngine.popen_uci(str(executable))
        options = {"Threads": max(1, threads), "Hash": max(16, hash_mb)}
        # Skill Level 20 is Stockfish's full strength; anything lower makes it
        # play deliberately worse moves.
        if 0 <= skill_level < 20:
            options["UCI_LimitStrength"] = False
            options["Skill Level"] = skill_level
        engine.configure(options)
        self._engine, self._settings = engine, wanted
        return engine

    def close(self) -> None:
        if self._engine is not None:
            try:
                self._engine.quit()
            except Exception:
                pass
        self._engine, self._settings = None, None


_SESSION = _StockfishSession()


def close_engine() -> None:
    """Shut the shared Stockfish process down."""
    _SESSION.close()


# Stockfish exits when its stdin closes, but shutting it down explicitly keeps
# a clean exit clean -- worth having with sixteen bots on one host.
atexit.register(close_engine)


def choose_stockfish_move(
    board: chess.Board, executable: Path, move_time_seconds: float, threads: int, hash_mb: int,
    skill_level: int = 20,
) -> chess.Move:
    """Choose a legal move with a locally installed Stockfish UCI engine."""
    with _SESSION._lock:
        try:
            engine = _SESSION.engine(executable, threads, hash_mb, skill_level)
            result = engine.play(board, chess.engine.Limit(time=max(0.05, move_time_seconds)))
        except (chess.engine.EngineError, chess.engine.EngineTerminatedError, BrokenPipeError):
            # A crashed engine must not take the bot down with it.
            _SESSION.close()
            engine = _SESSION.engine(executable, threads, hash_mb, skill_level)
            result = engine.play(board, chess.engine.Limit(time=max(0.05, move_time_seconds)))
    if result.move is None:
        raise ValueError("Stockfish returned no legal move")
    return result.move
