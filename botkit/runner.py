"""The lifecycle every bot shares.

All four bots do the same thing around their engine: authenticate, ask to enter
a table, wait to be paired, play until the game ends, go home, repeat -- and
survive the site misbehaving at any point.  Only the engine differs, so that is
all a bot module has to supply.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import logging
from pathlib import Path

from playwright.async_api import Error as PlaywrightError, async_playwright

from .control import make_client
from .matchmaking import Entry, read_opponent, wait_until_paired

# Backoff per outcome, in seconds.  A stake the site is not offering is worth
# waiting on properly rather than re-checking in a tight loop -- that spin is
# what hammered the site when the stake list changed under the old code.
BACKOFF = {
    Entry.STAKE_UNAVAILABLE: 30.0,
    Entry.NOT_PERMITTED: 5.0,
    # Another of our bots has this table; check back soon so we take our turn
    # promptly once it is playing.
    Entry.WAITING_TURN: 4.0,
    Entry.UI_UNAVAILABLE: 10.0,
    # A wallet only refills when a human tops it up, so re-check slowly and let
    # the bot resume on its own rather than making the operator restart it.
    Entry.LOW_BALANCE: 60.0,
}
RESTART_DELAY_SECONDS = 5.0
SESSION_RETRY_SECONDS = 30.0
MATCH_WAIT_SECONDS = 60.0


def build_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--check-session", action="store_true",
                        help="verify the saved session and exit")
    parser.add_argument("--stake", type=int, default=0,
                        help="stake to play; 0 uses the first the site offers")
    parser.add_argument("--no-odd-only", dest="odd_only", action="store_false",
                        help="join even when the waiting count is even (risks bot-vs-bot)")
    parser.add_argument("--min-balance", type=float, default=10.0,
                        help="stop entering games when the wallet falls below this (birr)")
    parser.add_argument("--session-file", default=None,
                        help="storage state to run as, overriding the config")
    parser.add_argument("--control-url", default=None,
                        help="dashboard base URL for permits and telemetry")
    parser.add_argument("--bot-id", default=None,
                        help="identity reported to the dashboard")
    parser.add_argument("--control-token", default=None,
                        help="shared secret for the dashboard, when it requires one")
    parser.add_argument("--phone-tail", default="",
                        help="last 4 digits of this account's phone, used to "
                             "recognise it if another of our bots faces it")
    parser.add_argument("--display-name", default="",
                        help="name this account shows to opponents")
    parser.add_argument("--headless", dest="headless", action="store_true", default=None,
                        help="run the browser with no window (required on a headless server)")
    parser.add_argument("--headed", dest="headless", action="store_false",
                        help="show the browser window, overriding the config")
    parser.set_defaults(odd_only=True)
    return parser


def configure_logging(name: str) -> logging.Logger:
    Path("logs").mkdir(exist_ok=True)
    log = logging.getLogger(name)
    log.setLevel(logging.INFO)
    log.handlers.clear()
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    for handler in (logging.StreamHandler(),
                    logging.FileHandler(Path("logs") / f"{name}.log", encoding="utf-8")):
        handler.setFormatter(formatter)
        log.addHandler(handler)
    return log


class MatchReporter:
    """Passed into a bot's play loop so the dashboard can follow the game."""

    def __init__(self, control, game: str, stake: int):
        self._control, self._game, self._stake = control, game, stake
        self.moves = 0

    async def state(self, state: str, **fields) -> None:
        await self._control.report(state, game=self._game, stake=self._stake, **fields)

    async def move(self, detail: str = "") -> None:
        self.moves += 1
        await self.state("playing", moves=self.moves, detail=detail)

    async def note(self, detail: str) -> None:
        await self.state("playing", moves=self.moves, detail=detail)


def resolve_account(account, session_file: str | None):
    """Point an account at a different saved session.

    The id is taken from the session filename too, so bots sharing a config
    still get separate identities -- and separate log files, rather than several
    processes interleaving into one.
    """
    if not session_file:
        return account
    path = Path(session_file).resolve()
    name = path.name
    for suffix in (".storage.json", ".json"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return dataclasses.replace(account, id=name or account.id, storage_state=path)


def resolve_stake(requested: int, offered: tuple[int, ...]) -> int:
    """Fall back to the site's first stake when none was asked for."""
    if requested:
        return requested
    return offered[0] if offered else 0


async def run_bot(
    *,
    args,
    settings,
    account,
    game: str,
    make_bot,
    play_match,
    open_context,
    session_invalid,
) -> None:
    """Own one bot from launch to shutdown.

    ``make_bot(page, settings)`` builds the page driver, and
    ``play_match(bot, settings, log, reporter)`` plays a single game to its end
    and returns the result text.
    """
    account = resolve_account(account, args.session_file)
    bot_id = args.bot_id or account.id
    log = configure_logging(account.id)
    control = make_client(args.control_url, bot_id, token=args.control_token or "")
    # A server has no display, so the command line has to be able to override
    # whatever the config file says.
    headless = settings.headless if args.headless is None else args.headless

    stake = args.stake
    if not stake:
        from .lobby import fetch_lobby
        try:
            stake = resolve_stake(0, fetch_lobby().stake_options)
            log.info("no stake given; using %s, the first the site offers", stake)
        except Exception:
            log.error("could not read the site's stake list and none was given; pass --stake")
            return

    await control.register(
        account_id=account.id,
        display_name=args.display_name or "",
        phone_tail=args.phone_tail or "",
    )
    await control.report("starting", game=game, stake=stake)

    async with async_playwright() as playwright:
        while True:
            browser = context = None
            try:
                await control.report("authenticating", game=game, stake=stake)
                browser = await playwright.chromium.launch(headless=headless)
                context, page = await open_context(browser, account, settings)
                if args.check_session:
                    log.info("session verified for %s", account.id)
                    return
                await _play_forever(
                    make_bot(page, settings), settings, log, control, game, stake,
                    args.odd_only, play_match, args.min_balance,
                )
            except session_invalid as error:
                log.error("session unusable: %s", error)
                await control.report("session_invalid", game=game, stake=stake, detail=str(error))
                if args.check_session:
                    raise
                await asyncio.sleep(SESSION_RETRY_SECONDS)
            except (PlaywrightError, ValueError) as error:
                log.exception("page failed; restarting the browser")
                await control.report("error", game=game, stake=stake, detail=str(error))
                await asyncio.sleep(RESTART_DELAY_SECONDS)
            except asyncio.CancelledError:
                await control.report("stopped", game=game, stake=stake)
                raise
            except Exception as error:
                log.exception("unexpected failure; restarting the browser")
                await control.report("error", game=game, stake=stake, detail=str(error))
                await asyncio.sleep(RESTART_DELAY_SECONDS)
            finally:
                for resource in (context, browser):
                    if resource is not None:
                        try:
                            await resource.close()
                        except Exception:
                            pass


# What each non-entry outcome should look like on the dashboard.
ENTRY_STATES = {
    Entry.STAKE_UNAVAILABLE: "stake_unavailable",
    Entry.LOW_BALANCE: "low_balance",
    Entry.WAITING_TURN: "waiting_turn",
}


async def _play_forever(bot, settings, log, control, game, stake, odd_only,
                        play_match, min_balance) -> None:
    reporter = MatchReporter(control, game, stake)
    warned_broke = False
    while True:
        await control.report("idle", game=game, stake=stake)
        result = await bot_queue(bot, stake, odd_only, control, log, min_balance)

        if not result.joined:
            state = ENTRY_STATES.get(result.entry, "idle")
            if result.entry is Entry.WAITING_TURN:
                log.info("holding: %s", result.detail)
            if result.entry is Entry.LOW_BALANCE:
                # Say it once at warning level, then stop repeating every minute.
                if not warned_broke:
                    log.warning("OUT OF FUNDS: %s - not entering any more games", result.detail)
                    warned_broke = True
            else:
                log.info("not entering: %s", result.detail)
            await control.report(state, game=game, stake=stake,
                                 detail=result.detail, balance=result.balance)
            await asyncio.sleep(BACKOFF.get(result.entry, 5.0))
            continue

        if warned_broke:
            log.info("wallet is funded again (%s birr); resuming", result.balance)
            warned_broke = False

        await control.report("queued", game=game, stake=stake,
                             detail=result.detail, balance=result.balance)

        # Hold the table lease for the whole wait, renewing as we go, so no
        # second bot of ours can be let into this queue behind us.
        matched = await wait_until_paired(
            bot.page,
            start_notice_selector=settings.selectors.get("start_notice", ""),
            turn_selector=settings.selectors["turn_indicator"],
            control=control,
            token=result.token,
            timeout_seconds=MATCH_WAIT_SECONDS,
            log=log,
        )
        # Only now is the table free for the next bot.
        await control.release(result.token, "matched" if matched else "left")

        if not matched:
            await bot.return_home()
            continue

        reporter.moves = 0
        await control.report("matched", game=game, stake=stake)

        # Confirm we are not facing one of our own accounts.  The lease should
        # make this impossible; checking is how we find out if it did not.
        name, tail = await read_opponent(bot.page)
        if name:
            log.info("paired against %s", name)
            verdict = await control.check_opponent(name, tail)
            if verdict.get("ours"):
                log.error("SELF-PAIR: %s. Entry to this table is now paused.",
                          verdict.get("reason", "opponent is one of ours"))
        await control.report("matched", game=game, stake=stake, opponent=name)
        log.info("playing")
        outcome = await play_match(bot, settings, log, reporter)
        log.info("match finished: %s", outcome or "unknown")
        await control.report(
            "finished", game=game, stake=stake, result=outcome or "unknown", moves=reporter.moves
        )
        await bot.return_home()


async def bot_queue(bot, stake, odd_only, control, log, min_balance):
    """Call whichever ``queue*`` method this bot exposes."""
    for name in ("queue", "queue_tankegna", "queue_egregna"):
        method = getattr(bot, name, None)
        if method is not None:
            return await method(stake, odd_only, control, log, min_balance)
    raise AttributeError(f"{type(bot).__name__} has no queue method")
