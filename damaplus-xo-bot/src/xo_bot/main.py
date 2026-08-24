from __future__ import annotations

import asyncio

from botkit.runner import build_parser, run_bot

from .engine import choose_move
from .session import SessionInvalid, open_authenticated_context
from .settings import Settings
from .ui import GAME, XoPage


async def play_match(bot: XoPage, settings: Settings, log, reporter) -> str | None:
    """Play one XO game through to its result."""
    mistake_rate = settings.difficulty_weights.get("mistake_rate", 0.35)
    while True:
        state = await bot.status()
        if state.finished:
            return state.result
        # The board only changes once the opponent has replied, so re-playing
        # the same position would mean clicking an already-taken cell.
        if state.board is not None and state.bot_mark and not bot.has_played(state.board):
            index = choose_move(state.board, state.bot_mark, mistake_rate=mistake_rate)
            await bot.play(index)
            bot.record_play(state.board)
            log.info("played cell %d as %s", index + 1, state.bot_mark)
            await reporter.move(f"cell {index + 1} as {state.bot_mark}")
        await asyncio.sleep(settings.poll_interval_seconds)


def main() -> None:
    args = build_parser("DamaPlus XO bot").parse_args()
    settings = Settings.load(args.config)
    asyncio.run(run_bot(
        args=args,
        settings=settings,
        account=settings.accounts[0],
        game=GAME,
        make_bot=XoPage,
        play_match=play_match,
        open_context=open_authenticated_context,
        session_invalid=SessionInvalid,
    ))


if __name__ == "__main__":
    main()
