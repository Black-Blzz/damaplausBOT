from __future__ import annotations

from pathlib import Path

from playwright.async_api import Browser, BrowserContext, Page, TimeoutError as PlaywrightTimeoutError

from .settings import Account, Settings


class SessionInvalid(RuntimeError):
    """Requires a human to authenticate normally and save a new storage state."""


async def open_authenticated_context(browser: Browser, account: Account, settings: Settings) -> tuple[BrowserContext, Page]:
    if not account.storage_state.is_file():
        raise SessionInvalid(f"{account.id}: no saved storage state at {account.storage_state}")
    context = await browser.new_context(storage_state=str(account.storage_state))
    page = await context.new_page()
    await page.goto(settings.base_url, wait_until="domcontentloaded")
    try:
        await page.locator(settings.selectors["logged_out"]).wait_for(state="visible", timeout=1_500)
        await context.close()
        raise SessionInvalid(f"{account.id}: session is logged out; manually reauthenticate and replace its storage state")
    except PlaywrightTimeoutError:
        pass
    try:
        await page.locator(settings.selectors["authenticated"]).wait_for(state="visible", timeout=10_000)
    except PlaywrightTimeoutError as error:
        await context.close()
        raise SessionInvalid(f"{account.id}: could not verify saved session; selector changed or session expired") from error
    return context, page


async def save_state(context: BrowserContext, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    await context.storage_state(path=str(destination))
