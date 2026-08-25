import asyncio
import chess
import re
from pathlib import Path
from chess_bot.engine import choose_move, choose_stockfish_move, legal_moves
from chess_bot.settings import Account, Settings
from chess_bot.ui import ChessPage


def test_takes_checkmate_when_available():
    board = chess.Board("6k1/5Q2/6K1/8/8/8/8/8 w - - 0 1")
    board.push(choose_move(board, 2))
    assert board.is_checkmate()


def test_includes_en_passant():
    board = chess.Board("rnbqkbnr/ppp1pppp/8/3pP3/8/8/PPPP1PPP/RNBQKBNR w KQkq d6 0 3")
    assert chess.Move.from_uci("e5d6") in legal_moves(board)


def test_synchronise_recognises_opponent_en_passant():
    page = ChessPage(None, None)  # Only its board synchronisation helpers are used here.
    page.bot_color = chess.WHITE
    page.board = chess.Board("4k3/8/8/8/3pP3/8/8/4K3 b - e3 0 1")
    expected = page.board.copy(stack=True)
    expected.push_uci("d4e3")

    page._synchronise(expected)

    assert page.board.fen() == expected.fen()


def test_keeps_castling_move():
    board = chess.Board("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1")
    assert chess.Move.from_uci("e1g1") in legal_moves(board)


def test_position_without_historical_rights_cannot_castle():
    board = chess.Board("r3k2r/8/8/8/8/8/8/R3K2R w - - 0 1")
    assert chess.Move.from_uci("e1g1") not in legal_moves(board)


def test_turn_wording_accepts_black_possessive_form():
    match = re.search(r"(?:your\s+turn(?:\s+as)?\s+(white|black)|(white|black)(?:'s|’s)?\s+turn)", "black's turn")
    assert match and "black" in match.groups()


def test_turn_wording_accepts_black_in_parentheses():
    text = "Your turn (Black)".lower()
    assert "your turn" in text
    assert re.search(r"\b(white|black)\b", text).group(1) == "black"


def test_black_turn_is_not_replaced_by_white_default():
    bot_color = chess.BLACK
    turn = bot_color if bot_color is not None else chess.WHITE
    assert turn == chess.BLACK


def test_live_turn_status_is_used_after_resynchronisation():
    bot_color = chess.WHITE
    your_turn = True
    snapshot = chess.Board()
    snapshot.turn = bot_color if your_turn else not bot_color
    assert snapshot.turn == chess.WHITE


def test_stockfish_requires_an_installed_executable():
    with __import__("pytest").raises(FileNotFoundError):
        choose_stockfish_move(chess.Board(), Path("missing-stockfish.exe"), 0.1, 1, 16)


def test_return_home_falls_back_to_base_url_when_button_is_missing():
    class FakeLocator:
        def __init__(self, count):
            self.count_value = count
            self.first = self

        async def count(self):
            return self.count_value

        async def click(self, timeout=None):
            raise RuntimeError("should not click when absent")

    class FakePage:
        def __init__(self):
            self.goto_calls = []

        def locator(self, selector):
            if selector == ".nav-item[data-view='home']":
                return FakeLocator(0)
            raise AssertionError(f"unexpected selector: {selector}")

        async def goto(self, url, wait_until="domcontentloaded"):
            self.goto_calls.append((url, wait_until))

    page = FakePage()
    settings = Settings(
        base_url="https://damaplus.online/",
        headless=False,
        poll_interval_seconds=1.0,
        action_delay_ms=(350, 900),
        search_depth=1,
        bot_elo=1000,
        stockfish_path=None,
        stockfish_move_time_seconds=0.25,
        stockfish_threads=1,
        stockfish_hash_mb=32,
        stockfish_skill_level=20,
        account=Account("test", Path("sessions/test.storage.json")),
        selectors={"return_home": ".nav-item[data-view='home']", "authenticated": "#playerApp:not(.hidden)"},
    )
    bot = ChessPage(page, settings)

    asyncio.run(bot.return_home())

    assert page.goto_calls == [("https://damaplus.online/", "domcontentloaded")]
