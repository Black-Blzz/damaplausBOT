from __future__ import annotations

import asyncio
import random
import re
from dataclasses import dataclass

import chess
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from botkit.matchmaking import EntryResult, join_match

from .settings import Settings


# The identifier the site uses for this game in its lobby API.
GAME = "chess"


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
        self.last_status_text = ""
        self.last_move: tuple[int, int] | None = None

    async def queue(self, stake: int, odd_only: bool, control, log, min_balance: float = 10.0) -> EntryResult:
        """Join matchmaking at ``stake``.

        A fresh match can hand us a different side, so nothing about the
        previous game is carried over.
        """
        self.bot_color = None
        self.board = None
        return await join_match(
            self.page,
            base_url=self.settings.base_url,
            matchmaking_selector=self.settings.selectors["chess_view"],
            game=GAME,
            stake=stake,
            control=control,
            odd_only=odd_only,
            action_delay_ms=self.settings.action_delay_ms,
            log=log,
            min_balance=min_balance,
        )

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
        self.last_status_text = " ".join(normalized.split())
        if any(word in normalized for word in ("won", "lost", "draw", "resigned")):
            return GameStatus(None, False, True, text)

        # The live UI varies punctuation and layout (for example, "Your turn
        # (Black)"). Identify the turn owner and colour independently instead
        # of requiring one exact sentence.
        your_turn = "your turn" in normalized
        opponent_turn = "opponent turn" in normalized or "opponent's turn" in normalized or "opponent’s turn" in normalized
        colour = re.search(r"\b(white|black)\b", normalized)
        if colour and (your_turn or opponent_turn):
            colour_name = colour.group(1)
            self.bot_color = chess.WHITE if colour_name == "white" else chess.BLACK
        if self.bot_color is None:
            return GameStatus(None, False, False)

        snapshot = await self._read_board()
        # The UI status is authoritative for whose turn it is. A board snapshot
        # carries piece locations, not a turn marker.
        snapshot.turn = self.bot_color if your_turn else not self.bot_color
        if self.board is None:
            self.board = chess.Board()
            self.board.turn = self.bot_color if your_turn else not self.bot_color
            if self._placement(self.board) != self._placement(snapshot):
                # A reload loses history. Do not infer castling from the king and
                # rook still occupying their original squares.
                snapshot.castling_rights = 0
                self.board = snapshot
        else:
            self._synchronise(snapshot)
        return GameStatus(self.board.copy(stack=True), your_turn, False)

    async def play(self, move: chess.Move) -> None:
        if self.board is None:
            raise UIChanged("cannot play before reading a Chess position")
        expected = self.board.copy(stack=True)
        expected.push(move)
        expected_placement = self._placement(expected)
        await self._click_square(move.from_square)
        await self._click_square(move.to_square)
        # A click can be ignored by the frontend (for example while it is
        # rendering the previous move).  Do not advance local state until the
        # live board proves that the move was accepted; otherwise the main loop
        # will endlessly click the same stale move.
        deadline = asyncio.get_running_loop().time() + 5
        while asyncio.get_running_loop().time() < deadline:
            if self._placement(await self._read_board()) == expected_placement:
                self.board.push(move)
                return
            await asyncio.sleep(0.2)
        raise UIChanged(f"Chess move {move.uci()} was not confirmed by the page")

    async def reload(self) -> None:
        await self.page.reload(wait_until="domcontentloaded")
        self.bot_color, self.board = None, None

    async def return_home(self) -> None:
        home = self.page.locator(self.settings.selectors["return_home"]).first
        try:
            if await home.count():
                await home.click(timeout=5_000)
                await self._human_delay()
                return
        except PlaywrightTimeoutError:
            pass
        await self.page.goto(self.settings.base_url, wait_until="domcontentloaded")
        await self._human_delay()

    async def _read_board(self) -> chess.Board:
        """Rebuild the live position, and note the opponent's last move.

        Read in one page evaluation rather than 64 locator round-trips, which
        matters when several bots poll a couple of times a second.
        """
        cells = await self.page.locator(self.settings.selectors["board_square"]).evaluate_all(
            """(nodes, pieceSelector) => nodes.map((node) => {
                 const piece = node.querySelector(pieceSelector);
                 return [node.className || "", piece ? (piece.className || "") : null];
               })""",
            self.settings.selectors["piece"],
        )
        if len(cells) != 64:
            raise UIChanged("Chess board does not contain 64 squares")

        board = chess.Board(None)
        last_from = last_to = None
        for visual, (square_classes, piece_classes) in enumerate(cells):
            square = self._square_from_visual(visual)
            marks = square_classes.lower().split()
            if "opponent-last-from" in marks:
                last_from = square
            if "opponent-last-to" in marks:
                last_to = square
            if piece_classes is None:
                continue
            classes = piece_classes.lower().split()
            color = chess.WHITE if "white" in classes else chess.BLACK if "black" in classes else None
            kind = next((PIECES[name] for name in PIECES if f"type-{name}" in classes), None)
            if color is None or kind is None:
                raise UIChanged(f"unrecognised Chess piece classes: {classes}")
            board.set_piece_at(square, chess.Piece(kind, color))

        # chess.BLACK is the boolean value False, so ``or chess.WHITE`` would
        # silently turn every reconstructed Black position into White-to-move.
        board.turn = self.bot_color if self.bot_color is not None else chess.WHITE
        # Square contents cannot prove that the king and rook have never moved.
        # Rights are retained only while the bot's move history is intact.
        board.castling_rights = 0
        self.last_move = (last_from, last_to) if last_from is not None and last_to is not None else None
        self._apply_en_passant(board)
        return board

    def _square_from_visual(self, visual: int) -> int:
        """Map a rendered cell index to a board square.

        The frontend draws rank 8 to 1 for White and reverses all 64 cells for
        Black.
        """
        app_index = 63 - visual if self.bot_color == chess.BLACK else visual
        return 56 - 8 * (app_index // 8) + app_index % 8

    def _apply_en_passant(self, board: chess.Board) -> None:
        """Restore the en-passant square from the opponent's last move.

        The site plays full rules including en passant, but a position rebuilt
        from piece placement alone cannot show it -- the capture is only legal
        on the move straight after a two-square pawn advance.  The board marks
        that advance, so read it back rather than losing the right.
        """
        if self.last_move is None:
            return
        origin, target = self.last_move
        piece = board.piece_at(target)
        if piece is None or piece.piece_type != chess.PAWN:
            return
        if chess.square_file(origin) != chess.square_file(target):
            return
        if abs(chess.square_rank(target) - chess.square_rank(origin)) != 2:
            return
        skipped = (origin + target) // 2
        # python-chess only offers the capture when a pawn can actually take.
        board.ep_square = skipped

    def _synchronise(self, snapshot: chess.Board) -> None:
        assert self.board is not None
        if self._placement(self.board) == self._placement(snapshot):
            return
        candidates = []
        for move in self.board.legal_moves:
            self.board.push(move)
            matched = self._placement(self.board) == self._placement(snapshot)
            self.board.pop()
            if matched:
                candidates.append(move)

        # The board marks which squares the opponent just moved between, which
        # settles the cases placement alone cannot -- most importantly a capture
        # en passant, where the pawn that disappears is not on the target square.
        if len(candidates) > 1 and self.last_move is not None:
            origin, target = self.last_move
            exact = [m for m in candidates if m.from_square == origin and m.to_square == target]
            if exact:
                candidates = exact

        if len(candidates) == 1:
            self.board.push(candidates[0])
            return
        # A reload or an unexpected UI update lost move history; keep the exact
        # pieces and drop unknown historical rights rather than clicking stale
        # moves.  The snapshot already carries any en-passant square it can see.
        self.board = snapshot

    @staticmethod
    def _placement(board: chess.Board) -> str:
        return board.board_fen()

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
