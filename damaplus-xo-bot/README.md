# DamaPlus XO bot

Separate Playwright bot for a disclosed XO account. It plays XO using minimax and automatically requeues after a win, loss, or draw.

By default it makes a deliberately weaker move 35% of the time when one is
available, so opponents can win. Set `difficulty_weights.mistake_rate` in
`config.json` between `0.0` (perfect play) and `1.0` (always choose a weaker
move when possible).

1. Copy `config.example.json` to `config.json`.
2. Run `python -m pip install -e .`.
3. Run `python -m xo_bot.capture_session --config config.json` and complete the normal sign-in.
4. Run `python -m xo_bot.main --config config.json`.

Use a separate disclosed session if the other bots will run at the same time.
