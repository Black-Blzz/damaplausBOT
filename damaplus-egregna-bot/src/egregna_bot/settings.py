from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Account:
    id: str
    storage_state: Path
    variant: str


@dataclass(frozen=True)
class Settings:
    base_url: str
    headless: bool
    poll_interval_seconds: float
    action_delay_ms: tuple[int, int]
    difficulty_weights: dict[str, float]
    hard_search_depth: int
    think_time_seconds: float
    accounts: tuple[Account, ...]
    selectors: dict[str, str]
    attributes: dict[str, str]

    @classmethod
    def load(cls, filename: str | Path) -> "Settings":
        source = Path(filename)
        raw = json.loads(source.read_text(encoding="utf-8"))
        accounts = tuple(Account(a["id"], (source.parent / a["storage_state"]).resolve(), a["variant"])
                         for a in raw["accounts"])
        if len(accounts) != 1 or accounts[0].variant != "egregna":
            raise ValueError("this bot permits exactly one Egregna account")
        lo, hi = raw["action_delay_ms"]
        return cls(raw["base_url"], raw.get("headless", True), raw.get("poll_interval_seconds", 2.0),
                   (int(lo), int(hi)), raw["difficulty_weights"], int(raw.get("hard_search_depth", 12)),
                   float(raw.get("think_time_seconds", 2.0)),
                   accounts, raw["selectors"], raw["attributes"])
