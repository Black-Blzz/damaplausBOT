# DamaPlus bot fleet

Four game bots for `damaplus.online` and a console that runs them.

```
botkit/          shared by every bot: lobby API, coordinator client, matchmaking, runner
dashboard/       the console — HTTP server, coordinator, account enrolment, web UI
damaplaus-BOT/          Dama tankegna   (dama_bot)
damaplus-egregna-bot/   Dama egregna    (egregna_bot)
damaplus-xo-bot/        XO              (xo_bot)
damaplus-chess-bot/     Chess           (chess_bot, Stockfish)
tests/           coordinator rules
```

## Running the console

```bash
python -m dashboard --port 8080
```

Open <http://127.0.0.1:8080>. It binds to loopback by default.

### Signing in

Every page and every API call needs an operator session. Three accounts exist:
**kaliesh**, **dagi** and **bass**. Passwords are stored only as salted
PBKDF2-HMAC-SHA256 digests, so nothing in this repository can be read back as a
login.

Change one with:

```bash
python -m dashboard --set-password kaliesh
```

That writes `dashboard-users.json`, which replaces the built-in accounts
entirely and is git-ignored.

Sessions last 12 hours, or 4 hours idle. Five failed attempts pause that
username for two minutes, and every sign-in, sign-out and failure is written to
the Activity log.

Bots cannot sign in, so they carry a separate secret that the console generates
per run and passes on the command line. It opens only `/api/coord/*` — a bot
token cannot start, stop or reconfigure anything.

### Hosting it on a server

```bash
python -m dashboard --host 0.0.0.0 --port 8080
```

**Put TLS in front of it.** Session cookies and passwords travel in the clear
over plain HTTP, so a public bind without a reverse proxy hands anyone on the
path a working login. The console logs a warning when it binds publicly.

Browsers run headless everywhere (`headless: true` in every bot config, and the
console passes `--headless` regardless), which a server with no display needs.
The one exception is `capture_session.py`, which is headed on purpose because a
human signs in through its window.

Everything is driven from that page: add accounts, pick a game and stake, choose
how many bots, and watch them play. Bots are launched as subprocesses and report
their state back, so the fleet table shows what each one is actually doing.

## How the bots avoid playing each other

The site publishes live headcounts at `GET /api/lobby/public`:

```json
{"stakeOptions": [5, 30], "online": {"chess:5": 1, "dama-tankegna:5": 2, "xo:5": 3}}
```

A bot should only join when somebody is sitting unpaired in the queue — that is,
when the number waiting is **odd**. Left to themselves, five bots all check at
the same instant, all see odd, all join, and pair with each other. So they don't
decide: they ask the console, which grants **one entry permit per table at a
time** and only when the parity is right. A permit expires on its own, so a bot
that crashes mid-join cannot wedge a table.

Two details matter for the count to mean anything:

- `online` includes our own bots, so queued bots are subtracted before the
  parity test.
- A bot that is *playing* is paired with exactly one human. That pair adds 2 to
  the count and cancels out of the parity, as does every human-vs-human game —
  so both halves are subtracted, leaving a number with the same parity as the
  unpaired players. See `dashboard/coordinator.py::_waiting_humans`.

The rule can be switched off from the console, which is the only way bots will
knowingly face each other.

## Stakes

Stakes come from the site, not from config. They are **5 and 30** at the time of
writing and have changed before — a bot asking for a stake the site is not
offering backs off and says so in the console rather than spinning. Leave the
stake unset (`--stake 0`) to take whichever the site lists first.

## Running one bot by hand

```bash
cd damaplus-xo-bot
PYTHONPATH=src python -m xo_bot.main --stake 5 --session-file sessions/xo-bot-1.storage.json
```

| Flag | Meaning |
| --- | --- |
| `--stake N` | Stake to play. `0` (default) takes the site's first. |
| `--session-file PATH` | Account to sign in as, overriding `config.json`. |
| `--control-url URL` | Console to take permits from and report to. Omit to run solo. |
| `--bot-id NAME` | Identity shown in the console. |
| `--no-odd-only` | Join regardless of parity. Risks bot-vs-bot. |
| `--min-balance N` | Stop entering games below this balance. Default 10 birr. |
| `--headless` / `--headed` | Override the config's browser visibility. |
| `--control-token S` | Shared secret, when the console requires one. |
| `--check-session` | Verify the saved session and exit. |

Without `--control-url` a bot falls back to reading the headcount printed on the
stake button (`3 online at 5 birr`). That is enough for a single bot; it is not
enough for several, which is what the console is for.

## Money

Before every join a bot reads its wallet from the lobby header and refuses to
enter if the balance is under the floor (10 birr by default) or under the stake
itself. It says so once in the log, reports `Out of funds` to the console, and
re-checks every minute — so topping the account up resumes it without a restart.
The console shows each bot's balance, turns it red under the floor, and raises a
banner naming the account.

A balance that cannot be read does **not** stop a bot: the site refuses an
unfunded join anyway, and halting on a failed read would be the worse error.

## Results

The site announces an opponent's victory as `"<their name> won"`, so matching on
the word alone scores losses as wins. `botkit/results.py` decides from the
subject of the sentence instead, and anything it cannot read confidently is
counted as `unknown` rather than guessed at.

## Chess

The bot keeps a real `chess.Board` with move history, so castling and en passant
work normally. History is lost on a page reload, and a position rebuilt from
piece placement alone cannot show either right. The board marks the opponent's
last move (`opponent-last-from` / `opponent-last-to`), which is enough to restore
the en-passant square — see `ChessPage._apply_en_passant`. Castling rights are
*not* guessed after a reload: a king and rook sitting on their home squares does
not prove neither has moved, and claiming the right would have Stockfish offer a
move the site rejects.

## Accounts

Each bot needs its own account — two bots sharing one login fight over the same
session. The console refuses to launch more bots than there are free accounts
and says how many more are needed.

Add one from **Accounts → Add an account**: enter a phone number, receive the
SMS code, enter it. The signed-in session is saved to
`<bot>/sessions/<name>.storage.json`. If the site rate-limits your IP, the same
form takes a proxy.

> Session files hold live credentials. They are git-ignored, but
> `damaplus-chess-bot/sessions/disclosed-chess-bot-1.storage.json` was committed
> before that rule existed — worth removing from history.

## Tests

```bash
python -m pytest tests/                              # coordinator rules
cd damaplus-xo-bot && PYTHONPATH=src python -m pytest # and likewise per bot
```
