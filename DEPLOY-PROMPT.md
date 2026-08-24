# Prompt for Claude (web) — server-side deployment

Copy everything below the line into claude.ai. Fill in the four bracketed
answers at the top first; the rest is accurate as of this repo.

---

I need help deploying a Python application to a server. Please ask me for
anything you need rather than assuming.

**My situation**
- Server: [e.g. Ubuntu 24.04 VPS, 2 vCPU / 4 GB RAM, Hetzner]
- Domain / TLS: [e.g. bots.example.com with Cloudflare, or "no domain yet"]
- Who reaches it: [e.g. 3 operators over the public internet, or "only me over Tailscale"]
- My comfort level: [e.g. "I can follow shell commands but I'm not a sysadmin"]

## What the application is

A control console plus four browser-automation bots that play games on a
website (damaplus.online) using saved logins. The console is a web page my
team signs into; from it we start up to 5 bots, watch what they're doing, and
stop them. It currently runs fine on my Windows dev machine and I want it on a
Linux server running 24/7.

## Architecture

```
repo root/
  botkit/                shared library (stdlib + Playwright)
  dashboard/             the console: HTTP server + web UI + auth
    static/              index.html, login.html, app.js, styles.css
  damaplaus-BOT/         bot 1  (package: dama_bot)
  damaplus-egregna-bot/  bot 2  (package: egregna_bot)
  damaplus-xo-bot/       bot 3  (package: xo_bot)
  damaplus-chess-bot/    bot 4  (package: chess_bot, uses Stockfish)
  tests/
```

- **Console**: `python -m dashboard --host 0.0.0.0 --port 8080`. Built on
  Python's stdlib `ThreadingHTTPServer` — no Flask/Django, no WSGI, no
  database. State is in memory plus two small JSON files it writes beside
  itself (`dashboard-settings.json`, `dashboard-users.json`).
- **Bots**: the console launches each one as a **child process**
  (`subprocess.Popen`) and reads its stdout. So only the console needs to be
  supervised — the bots live and die with it.
- **Browsers**: every bot drives its own **headless Chromium via Playwright**.
  5 bots = 5 concurrent Chromium instances. The console also opens a short-lived
  6th Chromium whenever an operator adds an account (phone + SMS code sign-in).
- **Auth**: operators sign in with username + password (PBKDF2-HMAC-SHA256,
  240k iterations, salted). Session is an HttpOnly SameSite=Strict cookie.
  Bots can't sign in; they carry a separate generated token that only opens
  the coordinator endpoints.

## Requirements

- Python >= 3.11 (I develop on 3.13)
- `playwright>=1.46` — all four bots and the console
- `python-chess>=1.999` — chess bot only
- `pytest>=8.0` — tests only
- Stockfish binary — chess bot only

Each bot project is its own package with its own `pyproject.toml`. `botkit/`
lives at the repo root and each bot adds it to `sys.path` itself, so it does
not need installing.

## What I need help with

1. **Provisioning.** Sizing this box: is 2 vCPU / 4 GB enough for 5 concurrent
   headless Chromium instances plus Stockfish, or do I need more? What actually
   runs out first — RAM, CPU, or file descriptors?

2. **Playwright on a headless server.** The system libraries Chromium needs,
   `playwright install --with-deps chromium`, and whether to run it as a
   dedicated unprivileged user. Chromium's sandbox in a VPS/container and
   whether `--no-sandbox` is needed (I'd rather not).

3. **The Stockfish problem.** My config points at
   `engines/stockfish/stockfish-windows-x86-64-avx2.exe` — a 114 MB **Windows**
   binary. On Linux I need a native build. Where should it come from (distro
   package vs official release), and which CPU variant do I pick? The path lives
   in `damaplus-chess-bot/config.json` under `stockfish.path`. Note the binary
   is large and probably should not be in git at all.

4. **Running it as a service.** A `systemd` unit for the console: correct user,
   working directory, `Restart=always`, environment, and graceful shutdown — on
   SIGTERM the console stops its child bots, and I don't want orphaned Chromium
   processes piling up after a restart. Please show me how to verify none leak.

5. **TLS and exposure.** The console sends passwords and a session cookie over
   plain HTTP, so it must sit behind TLS. I'd like nginx or Caddy terminating
   TLS and proxying to `127.0.0.1:8080`, with the console itself bound to
   loopback only so the port isn't reachable directly. Plus firewall rules.

6. **Secrets on disk.** Each bot account has a Playwright storage-state file
   (`<bot>/sessions/*.storage.json`) containing live login cookies for the
   gaming site. What file ownership and permissions should these have, and how
   should I back them up safely?

7. **Git hygiene.** One of those session files was committed before I
   git-ignored them, so live credentials are in my repo history. I need to purge
   it properly, and I'm not sure of the safest way given the repo may already be
   pushed.

8. **Logs.** Each bot writes to `<bot>/logs/<account>.log` and also to stdout,
   which the console captures. Give me a logrotate setup, and tell me whether
   I should route bot stdout to journald instead of files.

9. **Timezone.** The gaming site operates in `Africa/Addis_Ababa`. Should I set
   the server clock to that, or keep it UTC and only worry about display?

10. **Staying up.** What I should monitor to know the fleet is healthy, and a
    sane way to get alerted if the console dies or a bot gets stuck.

## Constraints

- Keep it as simple as it reasonably can be. I'd rather run one systemd unit
  behind a reverse proxy than adopt Docker/Kubernetes, unless you think
  containerising genuinely pays for itself here — if so, argue the case.
- No database, and I don't want to add one.
- I'd rather not run anything as root.

Please start by telling me what you'd change about this plan before I run any
of it, then give me the steps in the order I should do them.
