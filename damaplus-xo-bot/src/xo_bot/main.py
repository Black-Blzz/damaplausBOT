from __future__ import annotations

import argparse, asyncio, logging
from pathlib import Path

from playwright.async_api import Error as PlaywrightError, async_playwright

from .engine import choose_move
from .session import SessionInvalid, open_authenticated_context
from .settings import Settings
from .ui import UIChanged, XoPage


def logger(account: str) -> logging.Logger:
    Path("logs").mkdir(exist_ok=True)
    log = logging.getLogger(account); log.setLevel(logging.INFO); log.handlers.clear()
    log.addHandler(logging.StreamHandler()); log.addHandler(logging.FileHandler(Path("logs") / f"{account}.log", encoding="utf-8"))
    return log


async def play_forever(bot: XoPage, settings: Settings, log: logging.Logger) -> None:
    while True:
        await bot.queue()
        if not await bot.wait_for_match():
            log.info("no match within queue window; requeueing")
            continue
        while True:
            state = await bot.status()
            if state.finished:
                log.info("match finished: %s", state.result or "unknown")
                await bot.return_home()
                break
            if state.board is not None and state.bot_mark and not bot.has_played(state.board):
                await bot.play(choose_move(
                    state.board,
                    state.bot_mark,
                    mistake_rate=settings.difficulty_weights.get("mistake_rate", 0.35),
                ))
                bot.record_play(state.board)
            await asyncio.sleep(settings.poll_interval_seconds)


async def run(settings: Settings, check_only: bool) -> None:
    account, log = settings.accounts[0], logger(settings.accounts[0].id)
    async with async_playwright() as playwright:
        while True:
            browser = context = None
            try:
                browser = await playwright.chromium.launch(headless=settings.headless)
                context, page = await open_authenticated_context(browser, account, settings)
                if check_only:
                    log.info("session verified for %s", account.id)
                    return
                await play_forever(XoPage(page, settings), settings, log)
            except SessionInvalid as error:
                log.error("session unavailable: %s; retrying in 30 seconds", error)
                if check_only:
                    raise
                await asyncio.sleep(30)
            except (UIChanged, PlaywrightError, ValueError):
                log.exception("game page failed; restarting the browser in 5 seconds")
                await asyncio.sleep(5)
            except Exception:
                # Keep the service alive for unforeseen transient failures,
                # while preserving the full traceback in the account log.
                log.exception("unexpected bot failure; restarting the browser in 5 seconds")
                await asyncio.sleep(5)
            finally:
                if context is not None:
                    await context.close()
                if browser is not None:
                    await browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--config", default="config.json"); parser.add_argument("--check-session", action="store_true")
    args = parser.parse_args(); asyncio.run(run(Settings.load(args.config), args.check_session))


if __name__ == "__main__": main()
