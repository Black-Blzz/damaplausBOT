# DamaPlus Chess bot

Playwright bot for one disclosed DamaPlus Chess account. It reads the live
64-square board, handles black-board reversal, keeps game state so castling is
available, and deliberately excludes en passant because the supplied frontend
does not implement it. It automatically returns home and queues another game
after a win, loss, draw, or resignation.

1. `cd chess_bot`
2. `Copy-Item config.example.json config.json`
3. `python -m pip install -e .`
4. `python -m chess_bot.capture_session --config config.json`, sign in normally,
   then press Enter in the terminal when the home screen is visible.
5. `python -m chess_bot.main --config config.json`

Keep `headless` set to `false` for the first run. A separate session/account is
recommended if another bot will run simultaneously.
