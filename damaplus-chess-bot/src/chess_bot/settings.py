from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Account:
    id: str
    storage_state: Path


@dataclass(frozen=True)
class Settings:
    base_url: str
    headless: bool
    poll_interval_seconds: float
    action_delay_ms: tuple[int, int]
    search_depth: int
    bot_elo: int
    account: Account
    selectors: dict[str, str]

    @classmethod
    def load(cls, filename: str | Path) -> "Settings":
        source = Path(filename)
        raw = json.loads(source.read_text(encoding="utf-8"))
        accounts = raw["accounts"]
        if len(accounts) != 1 or accounts[0].get("variant") != "chess":
            raise ValueError("configure exactly one chess account")
        lo, hi = raw.get("action_delay_ms", [350, 900])
        account = Account(accounts[0]["id"], (source.parent / accounts[0]["storage_state"]).resolve())
        return cls(raw["base_url"], bool(raw.get("headless", True)), float(raw.get("poll_interval_seconds", 1)),
                   (int(lo), int(hi)), int(raw.get("search_depth", 2)), int(raw.get("bot_elo", 1000)),
                   account, raw["selectors"])
