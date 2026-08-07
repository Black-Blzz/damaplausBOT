# DamaPlus disclosed bot pilot

This is a one-account **Tankegna / Brazilian draughts** Playwright bot. It only
loads an already-authenticated Playwright storage-state file. It never accepts,
requests, stores, or enters phone numbers or OTPs. Configure only an account
already shown in your product's bot disclosure list.

## Setup

1. Create a virtual environment and install dependencies:
   `python -m pip install -e .[dev]` then `python -m playwright install chromium`.
2. Copy `config.example.json` to `config.json`. Its DamaPlus selectors match
   the supplied player frontend (`#playerApp`, `#damaBoard`, and its square
   buttons). Update them only if the deployed frontend differs.
3. On the automation machine, use the normal UI to sign in to the disclosed bot
   account once: `python -m dama_bot.capture_session --config config.json`.
   This opens a visible browser and waits for you to complete the normal login;
   it does not fill phone fields or read OTPs. Do not use a developer's personal
   Chrome profile as a bot session.
4. Check the session without joining a match:
   `python -m dama_bot.main --config config.json --check-session`
5. Run the pilot: `python -m dama_bot.main --config config.json`.

Logs go to `logs/<account>.log`. A missing or expired session is a terminal,
high-visibility error; reauthenticate manually and replace only that account's
storage-state file.

## Rules engine

The engine supports 8x8 Brazilian draughts: mandatory maximum captures,
forward-moving men, four-direction captures, promotion during capture, and
flying kings. Hard uses iterative-deepening negamax with alpha-beta pruning,
transposition caching, capture ordering, and configurable depth. Its rules are
independent of UI code so browser selector changes cannot silently alter play.
