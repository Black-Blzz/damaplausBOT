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

Bots cannot decide this for themselves — sixteen of them all checking "is the
count odd?" at the same instant would all see odd, all join, and pair up. So
every bot asks the console, and three layers enforce the answer.

**1 · One lease per table.** A `(game, stake)` table admits at most one of our
bots into its queue at a time. The holder renews the lease every 5 seconds and
only releases it once it is genuinely in a game, so a bot that waits a long
time can never have the table taken from under it. Stop renewing — crash, hang,
killed process — and the lease dies in 20 seconds, freeing the table.

> This replaced a permit that expired on a fixed 45-second timer while its
> holder was still legitimately queuing, which handed the same table to a second
> bot. That is exactly how two bots end up facing each other.

**2 · An odd waiting count.** A bot enters only when an odd number of players
is waiting, meaning one of them is unpaired and will take our bot as their
opponent. Two details make the count mean something:

- `online` includes our own bots, so queued ones are subtracted first.
- A bot that is *playing* is paired with exactly one human. That pair adds 2 to
  the count and cancels out of the parity, as does every human-vs-human game —
  so both halves are subtracted, leaving a number with the same parity as the
  unpaired players. See `dashboard/coordinator.py::_waiting_humans`.

**3 · Opponent verification.** Once paired, a bot reads who it is facing from
the site's own announcement and reports it. The coordinator knows every bot's
display name and phone last-4, so if the opponent turns out to be one of ours it
**pauses that table** and raises a critical alert naming both accounts. No
further bot enters until an operator resumes it. Prevention you cannot verify
is not worth much; this is how you find out layer 1 failed.

The odd-count rule can be switched off from the console. The lease cannot — bots
take turns whatever else is configured.

### What taking turns looks like

Two bots on `dama-tankegna:5`. The second shows **Waiting its turn** throughout,
which is the system working, not a stuck bot:

```
bot-a  gets the lease, sits in the queue      bot-b  waiting its turn
bot-a  paired with a human, releases lease    bot-b  waiting (queue now even)
                                              bot-b  a human arrives -> takes the table
bot-a  finishes its game, wants to requeue    bot-b  still queuing
bot-a  waiting its turn                       bot-b  paired
```

### Who is who

Each account's identity is written beside its session as
`<account>.meta.json` when it signs in: display name and phone last-4. The
console passes these to the bot at launch, the bot registers them with the
coordinator, and that registry is what makes layer 3 possible.

New accounts are given ordinary names (`Abel`, `Meron`, …) chosen
deterministically per account. Earlier accounts were registered as
`Bot-<account_id>`, which told every opponent exactly what they were playing.

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

**Where balances come from.** The console reads every saved account's wallet
straight from the site with `GET /api/player/session-delta`, using the cookies
and CSRF token already inside that account's Playwright session file. No browser
is involved, so it works whether or not a bot is running for that account, and a
top-up shows up within 90 seconds. The **Wallets** page lists every account with
its balance, bonus, and whether the site still accepts its session; "Re-check
now" forces an immediate sweep.

> This used to be read only by a running bot, at the moment it tried to enter a
> table. Topping an account up showed nothing until that bot next queued, and
> nothing at all if no bot was running for it.

**Spending guard.** Before every join a bot reads its own wallet from the page
and refuses to enter if the balance is under the floor (10 birr by default) or
under the stake itself. It says so once in the log, reports `Out of funds` to
the console, and re-checks every minute — so topping the account up resumes it
without a restart.

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
