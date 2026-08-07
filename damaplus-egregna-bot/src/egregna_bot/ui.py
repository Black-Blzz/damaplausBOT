from __future__ import annotations

import asyncio
import random
import re
from dataclasses import dataclass

from playwright.async_api import Locator, Page, TimeoutError as PlaywrightTimeoutError

from .model import Color, Piece, Position, Square
from .settings import Settings


class UIChanged(RuntimeError):
    pass


@dataclass(frozen=True)
class GameStatus:
    position: Position | None
    bot_turn: bool
    finished: bool
    result: str | None = None


class EgregnaPage:
    def __init__(self, page: Page, settings: Settings):
        self.page, self.settings, self.bot_color = page, settings, None

    async def queue_egregna(self) -> None:
        await self.page.goto(self.settings.base_url, wait_until="domcontentloaded")
        await self._click("egregna_matchmaking", 12_000)

    async def status(self) -> GameStatus:
        if self.settings.attributes.get("ui_mode") == "damaplus":
            return await self._damaplus_status()
        if await self.page.locator(self.settings.selectors["game_over"]).count():
            node = self.page.locator(self.settings.selectors["game_over"]).first
            return GameStatus(None, False, True, await node.get_attribute(self.settings.attributes["result"]))
        board = self.page.locator(self.settings.selectors["match_found"]).first
        if not await board.count():
            return GameStatus(None, False, False)
        squares = self.page.locator(self.settings.selectors["board_square"])
        count = await squares.count()
        if count != 32:
            return GameStatus(None, False, False)
        raw_bot_color = await board.get_attribute(self.settings.attributes["bot_color"])
        if raw_bot_color not in {Color.BLACK.value, Color.WHITE.value}:
            raise UIChanged("board has no valid configured player-color attribute")
        self.bot_color = Color(raw_bot_color)
        pieces: dict[Square, Piece] = {}
        for index in range(count):
            square = squares.nth(index)
            raw = await square.get_attribute(self.settings.attributes["square"])
            coord = self._parse_square(raw)
            piece = square.locator(self.settings.selectors["piece"]).first
            if await piece.count():
                color = Color((await piece.get_attribute(self.settings.attributes["piece_color"]) or "").lower())
                king = (await piece.get_attribute(self.settings.attributes["piece_king"]) or "").lower() in {"true", "1", "yes"}
                pieces[coord] = Piece(color, king)
        turn_node = self.page.locator(self.settings.selectors["turn_indicator"]).first
        raw_turn = await turn_node.get_attribute(self.settings.attributes["turn"])
        if raw_turn not in {Color.BLACK.value, Color.WHITE.value}:
            raise UIChanged("turn indicator has no valid configured data-player color")
        turn = Color(raw_turn)
        return GameStatus(Position.from_mapping(pieces, turn), turn is self.bot_color, False)

    async def play_path(self, path: tuple[Square, ...]) -> None:
        # Most Dama UIs retain selection through a capture chain. If your UI requires
        # reselecting the moving piece after each jump, configure that at this boundary.
        for square in path:
            await self._click_square(square)

    async def wait_for_match(self, timeout_seconds: float = 60.0) -> bool:
        if self.settings.attributes.get("ui_mode") == "damaplus":
            deadline = asyncio.get_running_loop().time() + timeout_seconds
            status = self.page.locator(self.settings.selectors["turn_indicator"]).first
            while asyncio.get_running_loop().time() < deadline:
                text = (await status.text_content() or "").lower()
                if "your turn" in text or "opponent turn" in text:
                    return True
                if "won" in text or "lost" in text or "draw" in text:
                    return False
                await asyncio.sleep(0.5)
            return False
        try:
            await self.page.locator(self.settings.selectors["match_found"]).wait_for(state="visible", timeout=timeout_seconds * 1000)
            return True
        except PlaywrightTimeoutError:
            return False

    async def return_home(self) -> None:
        if await self.page.locator(self.settings.selectors["return_home"]).count():
            await self._click("return_home", 5_000)

    async def _click(self, selector_name: str, timeout: int) -> None:
        try:
            await self.page.locator(self.settings.selectors[selector_name]).first.click(timeout=timeout)
            await self._human_delay()
        except PlaywrightTimeoutError as error:
            raise UIChanged(f"expected {selector_name} selector was not clickable") from error

    async def _click_square(self, square: Square) -> None:
        if self.settings.attributes.get("ui_mode") == "damaplus":
            if self.bot_color is None:
                raise UIChanged("cannot select a DamaPlus square before determining the bot color")
            app_color = "red" if self.bot_color is Color.BLACK else "black"
            index = square[0] * 8 + square[1]
            visual_index = index if app_color == "red" else 63 - index
            locator = self.page.locator(self.settings.selectors["board_square"]).nth(visual_index)
            try:
                await locator.click(timeout=7_000)
                await self._human_delay()
            except PlaywrightTimeoutError as error:
                raise UIChanged(f"DamaPlus board square {square} was not clickable") from error
            return
        attr, value = self.settings.attributes["square"], f"{square[0]},{square[1]}"
        locator = self.page.locator(f"{self.settings.selectors['board_square']}[{attr}='{value}']")
        try:
            await locator.click(timeout=7_000)
            await self._human_delay()
        except PlaywrightTimeoutError as error:
            raise UIChanged(f"board square {value} missing or not clickable") from error

    async def _human_delay(self) -> None:
        lo, hi = self.settings.action_delay_ms
        await asyncio.sleep(random.randint(lo, hi) / 1000)

    @staticmethod
    def _parse_square(value: str | None) -> Square:
        if not value:
            raise UIChanged("board square has no configured coordinate attribute")
        try:
            row, column = (int(x) for x in value.replace("-", ",").split(","))
            if 0 <= row < 8 and 0 <= column < 8:
                return row, column
        except ValueError:
            pass
        raise UIChanged(f"invalid board coordinate: {value!r}")

    async def _damaplus_status(self) -> GameStatus:
        """Read the supplied DamaPlus DOM, whose squares have no data attributes."""
        status = self.page.locator(self.settings.selectors["turn_indicator"]).first
        text = (await status.text_content() or "").strip()
        normalized = text.lower()
        if any(word in normalized for word in ("won", "lost", "draw")):
            return GameStatus(None, False, True, text)

        match = re.search(r"your turn as\s+(red|black)", normalized)
        if match:
            app_color = match.group(1)
            self.bot_color = Color.BLACK if app_color == "red" else Color.WHITE
        elif self.bot_color is not None and (
            "capture required" in normalized or normalized.startswith("chain jump")
        ):
            # DamaPlus replaces “Your turn as …” with these messages when a
            # capture is compulsory or another jump is required.  The colour
            # was learned on an earlier normal turn and stays fixed per match.
            app_color = "red" if self.bot_color is Color.BLACK else "black"
        else:
            # The app intentionally only identifies the player's colour on its
            # normal turn. Do not guess while the opponent is moving.
            return GameStatus(None, False, False)

        squares = self.page.locator(self.settings.selectors["board_square"])
        count = await squares.count()
        if count != 64:
            raise UIChanged(f"DamaPlus board has {count} squares; expected 64")

        pieces: dict[Square, Piece] = {}
        for visual_index in range(count):
            square = squares.nth(visual_index)
            piece = square.locator(self.settings.selectors["piece"]).first
            if not await piece.count():
                continue
            classes = (await piece.get_attribute("class") or "").lower().split()
            if "red" in classes:
                color = Color.BLACK
            elif "black" in classes:
                color = Color.WHITE
            else:
                raise UIChanged(f"DamaPlus piece on square {visual_index} has no red/black class")
            index = visual_index if app_color == "red" else 63 - visual_index
            pieces[(index // 8, index % 8)] = Piece(color, "king" in classes)
        return GameStatus(Position.from_mapping(pieces, self.bot_color), True, False)
