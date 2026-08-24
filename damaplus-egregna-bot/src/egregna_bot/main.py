from __future__ import annotations

import asyncio
import random

from botkit.runner import build_parser, run_bot

from .engine import StrongEngine
from .session import SessionInvalid, open_authenticated_context
from .settings import Settings
from .ui import GAME, EgregnaPage


async def play_match(bot: EgregnaPage, settings: Settings, log, reporter) -> str | None:
    """Play one Dama (egregna) game through to its result."""
    engine = StrongEngine(depth=settings.hard_search_depth)
    # A tier is drawn per match so the bot's strength varies between games
    # rather than within one.
    tier = random.choices(
        list(settings.difficulty_weights),
        weights=list(settings.difficulty_weights.values()),
    )[0]
    log.info("playing this match at tier=%s", tier)
    await reporter.note(f"tier {tier}")

    while True:
        state = await bot.status()
        if state.finished:
            return state.result
        if state.position is not None and state.bot_turn:
            move = engine.choose(state.position, tier)
            await bot.play_path(move.path)
            log.info("move %s captures=%d", move.path, len(move.captures))
            await reporter.move(f"{move.path} ({len(move.captures)} captured)")
        await asyncio.sleep(settings.poll_interval_seconds)


def main() -> None:
    args = build_parser("DamaPlus Dama egregna bot").parse_args()
    settings = Settings.load(args.config)
    asyncio.run(run_bot(
        args=args,
        settings=settings,
        account=settings.accounts[0],
        game=GAME,
        make_bot=EgregnaPage,
        play_match=play_match,
        open_context=open_authenticated_context,
        session_invalid=SessionInvalid,
    ))


if __name__ == "__main__":
    main()
