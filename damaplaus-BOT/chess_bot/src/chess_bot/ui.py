from __future__ import annotations

import asyncio
import random
import re
from dataclasses import dataclass

import chess
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from .settings import Settings


class UIChanged(RuntimeError):
    pass


PIECES = {"pawn": chess.PAWN, "knight": chess.KNIGHT, "bishop": chess.BISHOP, "rook": chess.ROOK,
          "queen": chess.QUEEN, "king": chess.KING}


@dataclass(frozen=True)
class GameStatus:
    board: chess.Board | None
    bot_turn: bool
    finished: bool
    result: str | None = None


class ChessPage:
    def __init__(self, page: Page, settings: Settings):
        self.page, self.settings = page, settings
        self.bot_color: chess.Color | None = None
        self.board: chess.Board | None = None

    async def queue(self) -> None:
        self.bot_color, self.board = None, None
        await self.page.goto(self.settings.base_url, wait_until="domcontentloaded")
        # The Home button both opens Chess and creates/joins the app's queue.
        # The in-view Create/Join button is hidden after this transition.
        await self._click("chess_view", 12_000)

    async def wait_for_match(self, timeout_seconds: float = 60.0) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        status = self.page.locator(self.settings.selectors["turn_indicator"]).first
        while asyncio.get_running_loop().time() < deadline:
            text = (await status.text_content() or "").lower()
            if "your turn" in text or "opponent turn" in text:
                return True
            if any(word in text for word in ("won", "lost", "draw", "resigned")):
                return False
            await asyncio.sleep(0.5)
        return False

    async def status(self) -> GameStatus:
        text = (await self.page.locator(self.settings.selectors["turn_indicator"]).first.text_content() or "").strip()
        normalized = text.lower()
        if any(word in normalized for word in ("won", "lost", "draw", "resigned")):
            return GameStatus(None, False, True, text)

        your_turn = re.search(r"your turn as\s+(white|black)", normalized)
        opponent_turn = re.search(r"opponent turn(?:\s+as)?\s+(white|black)", normalized)
        colour = (your_turn or opponent_turn)
        if colour:
            self.bot_color = chess.WHITE if colour.group(1) == "white" else chess.BLACK
        if self.bot_color is None:
            return GameStatus(None, False, False)

        snapshot = await self._read_board()
        if self.board is None:
            self.board = chess.Board()
            self.board.turn = self.bot_color if your_turn else not self.bot_color
            if self._placement(self.board) != self._placement(snapshot):
                # Late attachment or a UI reload: safely rebuild the position.
                self.board = snapshot
        else:
            self._synchronise(snapshot)
        return GameStatus(self.board.copy(stack=True), bool(your_turn), False)

    async def play(self, move: chess.Move) -> None:
        if self.board is None:
            raise UIChanged("cannot play before reading a Chess position")
        await self._click_square(move.from_square)
        await self._click_square(move.to_square)
        self.board.push(move)

    async def return_home(self) -> None:
        if await self.page.locator(self.settings.selectors["return_home"]).count():
            await self._click("return_home", 5_000)

    async def _read_board(self) -> chess.Board:
        squares = self.page.locator(self.settings.selectors["board_square"])
        if await squares.count() != 64:
            raise UIChanged("Chess board does not contain 64 squares")
        board = chess.Board(None)
        # The frontend renders rank 8 to 1 when White plays, and reverses all 64
        # cells when Black plays.
        for visual in range(64):
            piece = squares.nth(visual).locator(self.settings.selectors["piece"]).first
            if not await piece.count():
                continue
            classes = (await piece.get_attribute("class") or "").lower().split()
            color = chess.WHITE if "white" in classes else chess.BLACK if "black" in classes else None
            kind = next((PIECES[name] for name in PIECES if f"type-{name}" in classes), None)
            if color is None or kind is None:
                raise UIChanged(f"unrecognised Chess piece classes: {classes}")
            app_index = 63 - visual if self.bot_color == chess.BLACK else visual
            square = 56 - 8 * (app_index // 8) + app_index % 8
            board.set_piece_at(square, chess.Piece(kind, color))
        board.turn = self.bot_color or chess.WHITE
        board.castling_rights = self._possible_castling_rights(board)
        return board

    def _synchronise(self, snapshot: chess.Board) -> None:
        assert self.board is not None
        if self._placement(self.board) == self._placement(snapshot):
            return
        candidates = []
        for move in self.board.legal_moves:
            if self.board.is_en_passant(move):
                continue
            self.board.push(move)
            matched = self._placement(self.board) == self._placement(snapshot)
            self.board.pop()
            if matched:
                candidates.append(move)
        if len(candidates) == 1:
            self.board.push(candidates[0])
            return
        # A reload or an unexpected UI update lost move history; retain the exact
        # pieces and disable unknown historical rights rather than clicking stale moves.
        snapshot.turn = self.bot_color if self.board.turn != self.bot_color else not self.bot_color
        snapshot.castling_rights = 0
        self.board = snapshot

    @staticmethod
    def _placement(board: chess.Board) -> str:
        return board.board_fen()

    @staticmethod
    def _possible_castling_rights(board: chess.Board) -> chess.Bitboard:
        rights = chess.BB_EMPTY
        if board.piece_at(chess.E1) == chess.Piece(chess.KING, chess.WHITE):
            if board.piece_at(chess.H1) == chess.Piece(chess.ROOK, chess.WHITE): rights |= chess.BB_H1
            if board.piece_at(chess.A1) == chess.Piece(chess.ROOK, chess.WHITE): rights |= chess.BB_A1
        if board.piece_at(chess.E8) == chess.Piece(chess.KING, chess.BLACK):
            if board.piece_at(chess.H8) == chess.Piece(chess.ROOK, chess.BLACK): rights |= chess.BB_H8
            if board.piece_at(chess.A8) == chess.Piece(chess.ROOK, chess.BLACK): rights |= chess.BB_A8
        return rights

    async def _click_square(self, square: chess.Square) -> None:
        app_index = (7 - chess.square_rank(square)) * 8 + chess.square_file(square)
        visual = 63 - app_index if self.bot_color == chess.BLACK else app_index
        try:
            await self.page.locator(self.settings.selectors["board_square"]).nth(visual).click(timeout=7_000)
            await self._human_delay()
        except PlaywrightTimeoutError as error:
            raise UIChanged(f"Chess square {chess.square_name(square)} was not clickable") from error

    async def _click(self, selector: str, timeout: int) -> None:
        try:
            await self.page.locator(self.settings.selectors[selector]).first.click(timeout=timeout)
            await self._human_delay()
        except PlaywrightTimeoutError as error:
            raise UIChanged(f"expected {selector} selector was not clickable") from error

    async def _human_delay(self) -> None:
        low, high = self.settings.action_delay_ms
        await asyncio.sleep(random.randint(low, high) / 1000)
