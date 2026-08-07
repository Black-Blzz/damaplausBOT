from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from playwright.async_api import async_playwright

from .engine import choose_move
from .session import SessionInvalid, open_authenticated_context
from .settings import Settings
from .ui import ChessPage, UIChanged


def configure_logging(account_id: str) -> logging.Logger:
    Path("logs").mkdir(exist_ok=True)
    log = logging.getLogger(account_id); log.setLevel(logging.INFO); log.handlers.clear()
    log.addHandler(logging.StreamHandler())
    log.addHandler(logging.FileHandler(Path("logs") / f"{account_id}.log", encoding="utf-8"))
    return log


async def run(settings: Settings, check_only: bool) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=settings.headless)
        try:
            context, page = await open_authenticated_context(browser, settings.account, settings)
            log = configure_logging(settings.account.id)
            if check_only:
                log.info("session verified for %s", settings.account.id); return
            bot = ChessPage(page, settings)
            while True:
                await bot.queue()
                if not await bot.wait_for_match():
                    log.info("no match within queue window; requeueing"); continue
                while True:
                    try:
                        state = await bot.status()
                    except UIChanged as error:
                        log.warning("could not read game state; reloading game page: %s", error)
                        await bot.reload()
                        await asyncio.sleep(settings.poll_interval_seconds)
                        continue
                    if state.finished:
                        log.info("match finished: %s", state.result or "unknown")
                        await bot.return_home(); break
                    if state.board is not None and state.bot_turn:
                        move = choose_move(state.board, settings.search_depth, settings.bot_elo)
                        log.info("move %s", move.uci())
                        try:
                            await bot.play(move)
                        except UIChanged as error:
                            log.warning("move was not applied; reloading game page: %s", error)
                            await bot.reload()
                    await asyncio.sleep(settings.poll_interval_seconds)
        finally:
            await browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--config", default="config.json"); parser.add_argument("--check-session", action="store_true")
    args = parser.parse_args(); asyncio.run(run(Settings.load(args.config), args.check_session))


if __name__ == "__main__":
    main()
