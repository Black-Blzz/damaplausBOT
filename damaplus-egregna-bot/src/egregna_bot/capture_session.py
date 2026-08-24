"""One-time, human-operated storage-state capture for a disclosed bot account."""
from __future__ import annotations

import argparse
import asyncio

from playwright.async_api import async_playwright

from .session import save_state
from .settings import Settings


async def capture(settings: Settings) -> None:
    account = settings.accounts[0]
    async with async_playwright() as playwright:
        # Headed on purpose: a human signs in through this window.  Every
        # automated path runs headless -- see botkit.runner and dashboard.auth.
        browser = await playwright.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(settings.base_url, wait_until="domcontentloaded")
        print("Complete the normal sign-in in the browser using the disclosed bot account.")
        await asyncio.to_thread(input, "When the app home screen is visible, press Enter to save this session: ")
        await save_state(context, account.storage_state)
        print(f"Saved session to {account.storage_state}")
        await browser.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.json")
    args = parser.parse_args()
    asyncio.run(capture(Settings.load(args.config)))


if __name__ == "__main__":
    main()
