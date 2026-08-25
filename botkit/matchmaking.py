"""The shared "join a match" flow, used by all four bots.

The site changed its matchmaking to go through a stake chooser::

    click [data-find-player='<game>']   ->  #matchStakeModal opens
    click #matchStakeChoices button[data-match-stake='<stake>']
                                       ->  POST /api/room/auto-match

Each stake button carries its own live headcount, rendered as
``<span>3 online at 5 birr</span>``, which is the fallback source for the
odd/even rule when the dashboard coordinator is not reachable.
"""

from __future__ import annotations

import asyncio
import random
import re
from enum import Enum

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from .wallet import read_balance, shortfall

ONLINE_COUNT_PATTERN = re.compile(r"(\d+)\s+online", re.IGNORECASE)

STAKE_MODAL = "#matchStakeModal"
STAKE_CHOICES = "#matchStakeChoices"
SWITCH_OVERLAY = "#gameSwitchOverlay"

# How long to keep asking the coordinator before giving up on this attempt.
PERMIT_WAIT_SECONDS = 120.0
# Heartbeat cadence while queuing.  Comfortably inside the coordinator's
# LEASE_TIMEOUT_SECONDS so an ordinary slow poll never drops the lease.
RENEW_SECONDS = 5.0
# The site prints this in the start-notice element while nobody has joined yet.
WAITING_NOTICE = "waiting for opponent"
# Toast the site shows on pairing: "XO started vs <name>".
STARTED_VS = re.compile(r"started vs\s+(.+?)\s*$", re.IGNORECASE)
PHONE_TAIL = re.compile(r"\*+(\d{2,4})")


class Entry(Enum):
    """Why an attempt to join matchmaking ended."""

    JOINED = "joined"                        # we are in the queue
    STAKE_UNAVAILABLE = "stake_unavailable"  # site is not offering this stake
    NOT_PERMITTED = "not_permitted"          # coordinator said no within the budget
    UI_UNAVAILABLE = "ui_unavailable"        # chooser never appeared
    LOW_BALANCE = "low_balance"              # not enough money to stake
    WAITING_TURN = "waiting_turn"            # another of our bots holds this table


class EntryResult:
    def __init__(self, entry: Entry, token: str = "", detail: str = "",
                 stakes: tuple[int, ...] = (), balance: float | None = None):
        self.entry, self.token, self.detail = entry, token, detail
        self.stakes, self.balance = stakes, balance

    @property
    def joined(self) -> bool:
        return self.entry is Entry.JOINED

    def __repr__(self) -> str:
        return f"EntryResult({self.entry.value}, {self.detail!r})"


async def dismiss_switch_overlay(page) -> str:
    """Clear the "Leave your previous game?" dialog if the site raised one.

    Answering wrongly costs money: leaving a game in progress forfeits the stake
    to the opponent, while closing a stale *waiting* room refunds it.  So a live
    game is always kept, and only an abandoned waiting room is closed.
    """
    overlay = page.locator(SWITCH_OVERLAY)
    try:
        if not await overlay.count():
            return ""
        text = (await overlay.text_content() or "").lower()
    except Exception:
        return ""

    playing = "already playing" in text
    # .secondary keeps the previous game, .danger abandons it.
    button = ".game-switch-actions button.secondary" if playing else ".game-switch-actions button.danger"
    try:
        await overlay.locator(button).first.click(timeout=4_000)
    except Exception:
        return ""
    return "resumed the previous game" if playing else "closed a stale waiting room"


async def offered_stakes(page) -> tuple[int, ...]:
    """Stakes the chooser is currently showing."""
    values = await page.locator(f"{STAKE_CHOICES} button[data-match-stake]").evaluate_all(
        "nodes => nodes.map(n => n.getAttribute('data-match-stake'))"
    )
    found = []
    for value in values or ():
        try:
            found.append(int(value))
        except (TypeError, ValueError):
            continue
    return tuple(sorted(set(found)))


async def online_at_stake(page, stake: int) -> int | None:
    """Headcount printed on one stake button, or None if it is not shown."""
    try:
        button = page.locator(f"{STAKE_CHOICES} button[data-match-stake='{int(stake)}']").first
        if not await button.count():
            return None
        match = ONLINE_COUNT_PATTERN.search(await button.text_content() or "")
        return int(match.group(1)) if match else None
    except Exception:
        return None


async def open_stake_chooser(page, base_url: str, matchmaking_selector: str,
                             log=None, navigate: bool = True) -> bool:
    """Open the stake chooser for one game, landing on the lobby first if asked."""
    if navigate:
        await page.goto(base_url, wait_until="domcontentloaded")
        note = await dismiss_switch_overlay(page)
        if note and log:
            log.info("previous-game dialog: %s", note)
    try:
        await page.locator(matchmaking_selector).first.click(timeout=12_000)
    except PlaywrightTimeoutError:
        return False
    try:
        await page.locator(STAKE_MODAL).wait_for(state="visible", timeout=6_000)
    except PlaywrightTimeoutError:
        return False
    return True


async def close_stake_chooser(page) -> None:
    try:
        await page.locator(f"{STAKE_MODAL} [data-match-stake-close]").first.click(timeout=3_000)
    except Exception:
        pass


async def join_match(
    page,
    *,
    base_url: str,
    matchmaking_selector: str,
    game: str,
    stake: int,
    control,
    odd_only: bool,
    action_delay_ms: tuple[int, int],
    log,
    min_balance: float = 10.0,
) -> EntryResult:
    """Open the chooser, get clearance, and join the queue at ``stake``."""
    await page.goto(base_url, wait_until="domcontentloaded")
    note = await dismiss_switch_overlay(page)
    if note:
        log.info("previous-game dialog: %s", note)

    # Joining costs the stake, so check the wallet before opening the chooser.
    balance = await read_balance(page)
    problem = shortfall(balance, stake, min_balance)
    if problem:
        return EntryResult(Entry.LOW_BALANCE, detail=problem, balance=balance)

    if not await open_stake_chooser(page, base_url, matchmaking_selector, log, navigate=False):
        return EntryResult(Entry.UI_UNAVAILABLE, detail="stake chooser did not open", balance=balance)

    stakes = await offered_stakes(page)
    if stakes and int(stake) not in stakes:
        await close_stake_chooser(page)
        offered = ", ".join(str(value) for value in stakes)
        return EntryResult(
            Entry.STAKE_UNAVAILABLE,
            detail=f"site is offering {offered}, not {stake}",
            stakes=stakes,
            balance=balance,
        )

    if odd_only:
        verdict = await _clear_to_enter(page, game, stake, control, log)
        if verdict is not None and not verdict.joined:
            await close_stake_chooser(page)
            verdict.balance = balance
            return verdict
        token = verdict.token if verdict else ""
    else:
        token = ""

    button = page.locator(f"{STAKE_CHOICES} button[data-match-stake='{int(stake)}']").first
    try:
        await button.click(timeout=6_000)
    except PlaywrightTimeoutError:
        await close_stake_chooser(page)
        return EntryResult(Entry.UI_UNAVAILABLE, token, "stake button was not clickable", balance=balance)

    low, high = action_delay_ms
    await asyncio.sleep(random.randint(low, high) / 1000)
    return EntryResult(Entry.JOINED, token, f"queued at stake {stake}", balance=balance)


async def _clear_to_enter(page, game, stake, control, log) -> EntryResult | None:
    """Ask the coordinator for a permit, falling back to a local parity check."""
    await control.report("waiting_permit", game=game, stake=stake)
    permit = await control.acquire(game, stake, wait_seconds=PERMIT_WAIT_SECONDS)

    if permit is None:  # coordinator unreachable -- decide from the modal
        count = await online_at_stake(page, stake)
        if count is None:
            log.info("no coordinator and no headcount on the page; entering anyway")
            return None
        if count % 2 == 0:
            return EntryResult(
                Entry.NOT_PERMITTED,
                detail=f"{count} online (even) and no coordinator to arbitrate",
            )
        log.info("no coordinator; %s online (odd) so entering", count)
        return None

    if not permit.granted:
        kind = Entry.WAITING_TURN if permit.waiting_turn else Entry.NOT_PERMITTED
        return EntryResult(kind, detail=permit.reason)
    log.info("entry permitted: %s", permit.reason)
    return EntryResult(Entry.JOINED, permit.token, permit.reason)

async def queue_state(page, start_notice_selector: str, turn_selector: str) -> str:
    """Where this bot stands right now: waiting, playing, or finished.

    Read rather than inferred.  While nobody has joined, the site writes
    "Waiting for opponent to join." into the start-notice element, which is a
    far more reliable signal than guessing from the turn indicator.
    """
    try:
        notice = ""
        if start_notice_selector:
            node = page.locator(start_notice_selector).first
            if await node.count():
                notice = (await node.text_content() or "").strip().lower()
        if WAITING_NOTICE in notice:
            return "waiting"

        status = page.locator(turn_selector).first
        text = (await status.text_content() or "").strip().lower() if await status.count() else ""
    except Exception:
        return "unknown"

    if any(word in text for word in ("won", "lost", "draw", "resigned")):
        return "finished"
    if "your turn" in text or "opponent turn" in text or "opponent's turn" in text:
        return "playing"
    if "waiting" in text:
        return "waiting"
    return "unknown"


async def read_opponent(page) -> tuple[str, str]:
    """Who the site says we are playing: (display name, phone tail).

    The site announces the pairing in a toast -- "XO started vs ****1234" --
    where the name is either the opponent's chosen display name or a masked
    phone.  Either is enough to recognise one of our own accounts.
    """
    try:
        toast = page.locator("#toast").first
        text = (await toast.text_content() or "").strip() if await toast.count() else ""
    except Exception:
        return "", ""
    match = STARTED_VS.search(text)
    if not match:
        return "", ""
    name = match.group(1).strip()
    tail = PHONE_TAIL.search(name)
    return name, (tail.group(1) if tail else "")


async def wait_until_paired(
    page,
    *,
    start_notice_selector: str,
    turn_selector: str,
    control,
    token: str,
    timeout_seconds: float,
    log,
) -> bool:
    """Sit in the queue until someone joins, holding the table lease open.

    Returns True once we are in a game.  Returns False if the queue window runs
    out, the game ended before it began, or the coordinator withdrew the lease --
    in which case we must stop queuing rather than risk a second bot entering.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    next_renew = loop.time()

    while loop.time() < deadline:
        state = await queue_state(page, start_notice_selector, turn_selector)
        if state == "playing":
            return True
        if state == "finished":
            log.info("the game ended before it started")
            return False

        if token and loop.time() >= next_renew:
            if not await control.renew(token):
                log.warning("lost our place on this table; leaving the queue")
                return False
            next_renew = loop.time() + RENEW_SECONDS

        await asyncio.sleep(1.0)

    log.info("nobody joined inside the queue window")
    return False
