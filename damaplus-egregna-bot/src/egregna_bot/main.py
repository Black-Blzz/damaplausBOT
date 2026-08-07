from __future__ import annotations

import argparse
import asyncio
import logging
import random
from pathlib import Path

from playwright.async_api import async_playwright

from .engine import StrongEngine
from .session import SessionInvalid, open_authenticated_context
from .settings import Settings
from .ui import EgregnaPage, UIChanged


def configure_logging(account_id: str) -> logging.Logger:
    Path("logs").mkdir(exist_ok=True)
    logger = logging.getLogger(account_id)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    handler = logging.FileHandler(Path("logs") / f"{account_id}.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.addHandler(logging.StreamHandler())
    return logger


async def run(settings: Settings, check_only: bool) -> None:
    account = settings.accounts[0]
    log = configure_logging(account.id)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=settings.headless)
        try:
            context, page = await open_authenticated_context(browser, account, settings)
            if check_only:
                log.info("session verified for %s", account.id)
                await context.close()
                return
            bot = EgregnaPage(page, settings)
            engine = StrongEngine(depth=settings.hard_search_depth)
            while True:
                await bot.queue_egregna()
                if not await bot.wait_for_match():
                    log.info("no match within queue window; requeueing")
                    continue
                tier = random.choices(list(settings.difficulty_weights), weights=list(settings.difficulty_weights.values()))[0]
                log.info("match found; selected tier=%s", tier)
                while True:
                    state = await bot.status()
                    if state.finished:
                        log.info("match finished: %s", state.result or "unknown")
                        await bot.return_home()
                        break
                    if state.position is not None and state.bot_turn:
                        move = engine.choose(state.position, tier)
                        log.info("move %s captures=%d", move.path, len(move.captures))
                        await bot.play_path(move.path)
                    await asyncio.sleep(settings.poll_interval_seconds)
        except (SessionInvalid, UIChanged) as error:
            log.exception("ACTION REQUIRED: %s", error)
            raise
        finally:
            await browser.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--check-session", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(Settings.load(args.config), args.check_session))


if __name__ == "__main__":
    main()
