from __future__ import annotations

import asyncio
import random
import re
from dataclasses import dataclass

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from .settings import Settings


class UIChanged(RuntimeError):
    pass


@dataclass(frozen=True)
class GameStatus:
    board: tuple[str, ...] | None
    bot_mark: str | None
    finished: bool
    result: str | None = None


class XoPage:
    def __init__(self, page: Page, settings: Settings):
        self.page, self.settings, self.bot_mark = page, settings, None
        self.last_played_board: tuple[str, ...] | None = None

    async def queue(self) -> None:
        # A new match may assign a different mark, so never carry it over.
        self.bot_mark = None
        self.last_played_board = None
        await self.page.goto(self.settings.base_url, wait_until="domcontentloaded")
        await self._click("xo_matchmaking", 12_000)

    async def wait_for_match(self, timeout_seconds: float = 60.0) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        status = self.page.locator(self.settings.selectors["turn_indicator"]).first
        while asyncio.get_running_loop().time() < deadline:
            text = (await status.text_content() or "").lower()
            if "your turn" in text or "opponent turn" in text:
                return True
            if any(word in text for word in ("won", "lost", "draw")):
                return False
            await asyncio.sleep(0.5)
        return False

    async def status(self) -> GameStatus:
        text = (await self.page.locator(self.settings.selectors["turn_indicator"]).first.text_content() or "").strip()
        normalized = text.lower()
        if any(word in normalized for word in ("won", "lost", "draw", "tie", "game over")):
            return GameStatus(None, None, True, text)
        if "your turn" not in normalized and "your move" not in normalized:
            return GameStatus(None, None, False)

        cells = self.page.locator(self.settings.selectors["cell"])
        if await cells.count() != 9:
            raise UIChanged("XO board does not contain nine cells")
        board = tuple((await cells.nth(i).text_content() or "").strip().upper() for i in range(9))
        if any(value not in {"", "X", "O"} for value in board):
            raise UIChanged(f"unexpected XO board values: {board}")

        # DamaPlus normally says "Your turn as X".  Keep working if it omits
        # the mark after the first move by retaining it, or infer it from a
        # valid board when this is the first observed turn.
        mark = re.search(r"\bas\s+([xo])\b", normalized)
        if mark:
            self.bot_mark = mark.group(1).upper()
        elif self.bot_mark is None:
            self.bot_mark = "X" if board.count("X") == board.count("O") else "O"
        return GameStatus(board, self.bot_mark, False)

    async def play(self, index: int) -> None:
        try:
            await self.page.locator(self.settings.selectors["cell"]).nth(index).click(timeout=7_000)
            low, high = self.settings.action_delay_ms
            await asyncio.sleep(random.randint(low, high) / 1000)
        except PlaywrightTimeoutError as error:
            raise UIChanged(f"XO cell {index + 1} was not clickable") from error

    def has_played(self, board: tuple[str, ...]) -> bool:
        return self.last_played_board == board

    def record_play(self, board: tuple[str, ...]) -> None:
        self.last_played_board = board

    async def return_home(self) -> None:
        if await self.page.locator(self.settings.selectors["return_home"]).count():
            await self._click("return_home", 5_000)

    async def _click(self, selector: str, timeout: int) -> None:
        try:
            await self.page.locator(self.settings.selectors[selector]).first.click(timeout=timeout)
        except PlaywrightTimeoutError as error:
            raise UIChanged(f"expected {selector} selector was not clickable") from error
