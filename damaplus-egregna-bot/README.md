# DamaPlus Egregna bot

One-account Playwright bot for disclosed Dama Egregna accounts. It uses a
normal, manually-created Playwright storage state; no phone numbers or OTPs
are read, saved, or entered by the bot.

## Start

1. Copy `config.example.json` to `config.json` and set the deployed app URL if
   it differs from the default.
2. Install the local project: `python -m pip install -e .`
3. Capture a separately authenticated Egregna bot session:
   `python -m egregna_bot.capture_session --config config.json`.
4. Verify: `python -m egregna_bot.main --config config.json --check-session`.
5. Run: `python -m egregna_bot.main --config config.json`.

## Rules

The engine applies the supplied Egregna rules: mandatory captures, forward
men, adjacent-jump kings (not flying), promotion during a capture chain, and
the restriction that king pieces cannot be captured in Egregna.
