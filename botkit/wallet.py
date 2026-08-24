"""Reading the bot's wallet balance off the lobby.

The lobby header carries the playable balance as ``<strong id="homeBalance">``.
It is rendered as a placeholder "0 birr" and only filled in once the app has
hydrated from the API, so a balance read too early looks like an empty wallet.
Every read here therefore waits for a figure that has stopped changing before
believing it.
"""

from __future__ import annotations

import asyncio
import re

BALANCE_SELECTOR = "#homeBalance"

# "1,250 birr", "0 birr", "12.50 birr"
_MONEY = re.compile(r"(-?[\d,]+(?:\.\d+)?)")

READ_TIMEOUT_SECONDS = 10.0
POLL_SECONDS = 0.75
# A zero has to hold this many consecutive reads before it is taken at face
# value, since zero is also what the un-hydrated page shows.
ZERO_CONFIRMATIONS = 3


def parse_money(text: str | None) -> float | None:
    if not text:
        return None
    match = _MONEY.search(text.replace(" ", " "))
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


async def read_balance(page, timeout: float = READ_TIMEOUT_SECONDS) -> float | None:
    """Current playable balance in birr, or None if it could not be read."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    zeros = 0
    last: float | None = None

    while loop.time() < deadline:
        try:
            node = page.locator(BALANCE_SELECTOR).first
            value = parse_money(await node.text_content()) if await node.count() else None
        except Exception:
            value = None

        if value is not None:
            last = value
            if value > 0:
                return value  # a real figure can only come from hydration
            zeros += 1
            if zeros >= ZERO_CONFIRMATIONS:
                return 0.0
        await asyncio.sleep(POLL_SECONDS)

    return last


def shortfall(balance: float | None, stake: int, floor: float) -> str:
    """Explain why this balance cannot play, or return an empty string."""
    if balance is None:
        return ""  # unreadable: let the site decide rather than halting a bot
    if balance < floor:
        return f"balance is {balance:g} birr, below the {floor:g} birr floor"
    if balance < stake:
        return f"balance is {balance:g} birr, less than the {stake} birr stake"
    return ""
