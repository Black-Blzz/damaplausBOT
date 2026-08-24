"""DamaPlus Dama (tankegna) bot.

Makes ``botkit`` -- the shared fleet helpers at the repository root -- importable
whether this bot is started by the dashboard (which sets PYTHONPATH) or by hand
from its own directory.
"""

import sys as _sys
from pathlib import Path as _Path

_REPO_ROOT = _Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in _sys.path:
    _sys.path.append(str(_REPO_ROOT))
