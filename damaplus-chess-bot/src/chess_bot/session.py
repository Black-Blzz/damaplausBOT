from __future__ import annotations

from pathlib import Path
from playwright.async_api import Browser, BrowserContext, Page, TimeoutError as PlaywrightTimeoutError
from .settings import Account, Settings


class SessionInvalid(RuntimeError):
    pass


async def open_authenticated_context(browser: Browser, account: Account, settings: Settings) -> tuple[BrowserContext, Page]:
    if not account.storage_state.is_file():
        raise SessionInvalid(f"no saved storage state at {account.storage_state}")
    context = await browser.new_context(storage_state=str(account.storage_state))
    page = await context.new_page()
    await page.goto(settings.base_url, wait_until="domcontentloaded")
    try:
        await page.locator(settings.selectors["logged_out"]).wait_for(state="visible", timeout=1_500)
        raise SessionInvalid("session is logged out; capture a new session")
    except PlaywrightTimeoutError:
        pass
    try:
        await page.locator(settings.selectors["authenticated"]).wait_for(state="visible", timeout=10_000)
    except PlaywrightTimeoutError as error:
        raise SessionInvalid("could not verify the saved session") from error
    return context, page


async def save_state(context: BrowserContext, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    await context.storage_state(path=str(destination))
