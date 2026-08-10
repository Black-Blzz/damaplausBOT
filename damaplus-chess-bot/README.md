# DamaPlus Chess bot

Playwright bot for one disclosed DamaPlus Chess account. It reads the live
64-square board, handles black-board reversal, and keeps game state so
castling and en passant are available. It automatically returns home and queues
another game after a win, loss, draw, or resignation.

Each attempted move is confirmed against the expected live board position
before the bot updates its local state, preventing unrelated UI updates from
desynchronising the game state or replaying a stale move forever.
`config.json` defaults to `"bot_elo": 1000` with a two-ply search. This is an
approximate strength target, not an official Elo rating; the bot plays all
legal moves and uses material search rather than an externally calibrated
chess engine.

1. `cd chess_bot`
2. `Copy-Item config.example.json config.json`
3. `python -m pip install -e .`
4. `python -m chess_bot.capture_session --config config.json`, sign in normally,
   then press Enter in the terminal when the home screen is visible.
5. `python -m chess_bot.main --config config.json`

Keep `headless` set to `false` for the first run. A separate session/account is
recommended if another bot will run simultaneously.
