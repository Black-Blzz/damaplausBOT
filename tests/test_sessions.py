"""Choosing which saved sign-in a bot runs as, and deleting one."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from botkit.lobby import LobbySnapshot
from dashboard import processes as processes_module
from dashboard.coordinator import Coordinator
from dashboard.processes import Fleet, session_status

VARIANT = "xo"


class FakeLobby:
    def snapshot(self) -> LobbySnapshot:
        return LobbySnapshot((5, 30), {}, time.time())


def write_session(directory: Path, name: str, *, expired: bool = False) -> Path:
    path = directory / f"{name}.storage.json"
    path.write_text(json.dumps({
        "cookies": [{
            "name": "t", "value": "x", "domain": "damaplus.online", "path": "/",
            "expires": time.time() + (-10 if expired else 86_400),
        }],
        "origins": [],
    }), encoding="utf-8")
    return path


@pytest.fixture
def sessions_dir(tmp_path, monkeypatch):
    """Point the XO bot's session directory at a temporary one."""
    directory = tmp_path / "sessions"
    directory.mkdir()
    variants = {k: dict(v) for k, v in processes_module.BOT_VARIANTS.items()}
    variants[VARIANT]["cwd"] = tmp_path
    monkeypatch.setattr(processes_module, "BOT_VARIANTS", variants)
    return directory


@pytest.fixture
def fleet():
    return Fleet(Coordinator(FakeLobby()), lambda _message: None)


# -- reading what is on disk ---------------------------------------------

def test_a_live_session_is_usable(sessions_dir):
    assert session_status(write_session(sessions_dir, "good"))[0] == "active"


def test_an_expired_session_is_not(sessions_dir):
    assert session_status(write_session(sessions_dir, "old", expired=True))[0] == "expired"


def test_unreadable_files_are_reported_not_crashed(sessions_dir):
    (sessions_dir / "broken.storage.json").write_text("{not json", encoding="utf-8")
    assert session_status(sessions_dir / "broken.storage.json")[0] == "invalid"
    assert session_status(sessions_dir / "absent.storage.json")[0] == "missing"


# -- choosing accounts ----------------------------------------------------

def test_named_accounts_are_used_exactly(fleet, sessions_dir):
    for name in ("a", "b", "c"):
        write_session(sessions_dir, name)
    chosen, problem = fleet.plan_launch(VARIANT, 5, 2, ["c", "a"])
    assert problem is None
    assert [entry["account_id"] for entry in chosen] == ["c", "a"], "order must be respected"


def test_the_same_account_twice_only_runs_once(fleet, sessions_dir):
    write_session(sessions_dir, "a")
    chosen, problem = fleet.plan_launch(VARIANT, 5, 2, ["a", "a"])
    assert problem is None and len(chosen) == 1


def test_an_unknown_account_is_refused(fleet, sessions_dir):
    write_session(sessions_dir, "a")
    _, problem = fleet.plan_launch(VARIANT, 5, 1, ["ghost"])
    assert problem and "ghost" in problem


def test_an_expired_account_is_refused_by_name(fleet, sessions_dir):
    write_session(sessions_dir, "stale", expired=True)
    _, problem = fleet.plan_launch(VARIANT, 5, 1, ["stale"])
    assert problem and "Re-authenticate" in problem


def test_more_accounts_than_fleet_slots_is_refused(fleet, sessions_dir):
    names = [f"a{i}" for i in range(processes_module.MAX_BOTS + 1)]
    for name in names:
        write_session(sessions_dir, name)
    _, problem = fleet.plan_launch(VARIANT, 5, len(names), names)
    assert problem and "Fleet limit" in problem


def test_without_a_selection_free_accounts_are_taken(fleet, sessions_dir):
    for name in ("a", "b"):
        write_session(sessions_dir, name)
    chosen, problem = fleet.plan_launch(VARIANT, 5, 2, None)
    assert problem is None and len(chosen) == 2


def test_asking_for_more_than_exist_explains_itself(fleet, sessions_dir):
    write_session(sessions_dir, "a")
    _, problem = fleet.plan_launch(VARIANT, 5, 3, None)
    assert problem and "own account" in problem


# -- deleting -------------------------------------------------------------

def test_deleting_removes_the_file(fleet, sessions_dir):
    path = write_session(sessions_dir, "gone")
    assert fleet.delete_session(VARIANT, "gone") == ""
    assert not path.exists()


def test_deleting_something_absent_says_so(fleet, sessions_dir):
    assert "no saved session" in fleet.delete_session(VARIANT, "ghost")


def test_deleting_from_an_unknown_game_is_refused(fleet, sessions_dir):
    assert "Unknown game" in fleet.delete_session("poker", "a")


@pytest.mark.parametrize("account_id", ["../../etc/passwd", "..\\..\\secret", "a/b"])
def test_a_crafted_name_cannot_escape_the_sessions_folder(fleet, sessions_dir, account_id):
    """The name comes from the browser, so it must not reach other files."""
    assert fleet.session_path(VARIANT, account_id) is None
    assert fleet.delete_session(VARIANT, account_id)  # refused with a message


def test_a_session_in_use_is_not_deleted(fleet, sessions_dir, monkeypatch):
    path = write_session(sessions_dir, "busy")
    monkeypatch.setattr(fleet, "running_on", lambda variant, account: True)
    problem = fleet.delete_session(VARIANT, "busy")
    assert "Stop it first" in problem
    assert path.exists(), "a running bot's login must survive"
