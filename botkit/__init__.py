"""Shared helpers for the DamaPlus bot fleet.

Imported by every bot package and by the dashboard.  Stdlib only (except the
matchmaking helper, which needs Playwright), so it works from any of the
per-bot virtualenvs without extra installs.
"""

from .lobby import LobbySnapshot, fetch_lobby
from .control import ControlClient, NullControlClient, Permit, make_client

__all__ = [
    "LobbySnapshot",
    "fetch_lobby",
    "ControlClient",
    "NullControlClient",
    "Permit",
    "make_client",
]
