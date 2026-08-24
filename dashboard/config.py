"""Static fleet layout plus the operator settings that survive a restart."""

from __future__ import annotations

import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"
SETTINGS_FILE = BASE_DIR / "dashboard-settings.json"

SITE_URL = "https://damaplus.online"

# Hard ceiling on concurrent bots, per the operator brief.
MAX_BOTS = 5

# One entry per bot project.  ``game`` is the identifier the site uses in its
# lobby API and in ``data-find-player``; ``variant`` is our internal short name.
BOT_VARIANTS = {
    "tankegna": {
        "name": "Dama Tankegna",
        "game": "dama-tankegna",
        "cwd": BASE_DIR / "damaplaus-BOT",
        "module": "dama_bot.main",
    },
    "egregna": {
        "name": "Dama Egregna",
        "game": "dama-egregna",
        "cwd": BASE_DIR / "damaplus-egregna-bot",
        "module": "egregna_bot.main",
    },
    "xo": {
        "name": "XO",
        "game": "xo",
        "cwd": BASE_DIR / "damaplus-xo-bot",
        "module": "xo_bot.main",
    },
    "chess": {
        "name": "Chess",
        "game": "chess",
        "cwd": BASE_DIR / "damaplus-chess-bot",
        "module": "chess_bot.main",
    },
}

GAME_KEYS = [entry["game"] for entry in BOT_VARIANTS.values()]

DEFAULT_SETTINGS = {
    "odd_gate_enabled": True,
    "last_variant": "tankegna",
    "last_stake": 0,          # 0 means "use the first stake the site offers"
    "last_count": 1,
    "stagger_seconds": 2.0,
    "min_balance": 10.0,
    "headless": True,
}


def load_settings() -> dict:
    settings = dict(DEFAULT_SETTINGS)
    try:
        stored = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        settings.update({k: v for k, v in stored.items() if k in DEFAULT_SETTINGS})
    except (OSError, ValueError):
        pass
    return settings


def save_settings(settings: dict) -> None:
    try:
        payload = {k: v for k, v in settings.items() if k in DEFAULT_SETTINGS}
        SETTINGS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        pass
