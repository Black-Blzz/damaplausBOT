from __future__ import annotations

import asyncio

from botkit.runner import build_parser, run_bot

from .engine import choose_stockfish_move
from .session import SessionInvalid, open_authenticated_context
from .settings import Settings
from .ui import GAME, ChessPage, UIChanged


async def play_match(bot: ChessPage, settings: Settings, log, reporter) -> str | None:
    """Play one chess game through to its result.

    The live board is the only source of truth, so anything unreadable -- a
    half-rendered board, a position Stockfish rejects, a move that did not take
    -- is handled by reloading the game page rather than by guessing.
    """
    last_status = None
    while True:
        try:
            state = await bot.status()
        except UIChanged as error:
            log.warning("could not read the board; reloading: %s", error)
            await reporter.note("reloading the board")
            await bot.reload()
            await asyncio.sleep(settings.poll_interval_seconds)
            continue

        if bot.last_status_text != last_status:
            last_status = bot.last_status_text
            log.info("board status: %s", last_status or "(empty)")

        if state.finished:
            return state.result

        if state.board is not None and state.bot_turn:
            try:
                move = await asyncio.to_thread(
                    choose_stockfish_move,
                    state.board,
                    settings.stockfish_path,
                    settings.stockfish_move_time_seconds,
                    settings.stockfish_threads,
                    settings.stockfish_hash_mb,
                )
            except ValueError as error:
                log.warning("no playable move in this position; reloading: %s", error)
                await bot.reload()
                await asyncio.sleep(settings.poll_interval_seconds)
                continue
            try:
                await bot.play(move)
            except UIChanged as error:
                log.warning("move was not applied; reloading: %s", error)
                await bot.reload()
                continue
            log.info("move %s", move.uci())
            await reporter.move(move.uci())

        await asyncio.sleep(settings.poll_interval_seconds)


def main() -> None:
    args = build_parser("DamaPlus Chess bot").parse_args()
    settings = Settings.load(args.config)
    asyncio.run(run_bot(
        args=args,
        settings=settings,
        account=settings.account,
        game=GAME,
        make_bot=ChessPage,
        play_match=play_match,
        open_context=open_authenticated_context,
        session_invalid=SessionInvalid,
    ))


if __name__ == "__main__":
    main()
