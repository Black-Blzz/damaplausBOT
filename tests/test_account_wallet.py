"""Reading an account's wallet from its saved session, without a browser."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from botkit import account as account_module
from botkit.account import AccountState, _cookie_header, _csrf_token, read_account


def storage(cookies=None, local_storage=None) -> dict:
    return {
        "cookies": cookies if cookies is not None else [
            {"name": "sid", "value": "abc", "domain": ".damaplus.online", "path": "/"},
        ],
        "origins": [{
            "origin": "https://damaplus.online",
            "localStorage": local_storage if local_storage is not None else [
                {"name": "damaplus-auth-session-v1", "value": json.dumps({"csrfToken": "tok"})},
            ],
        }],
    }


@pytest.fixture
def session_file(tmp_path):
    def write(payload) -> Path:
        path = tmp_path / "a.storage.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path
    return write


# -- picking the session apart -------------------------------------------

def test_only_this_site_cookies_are_sent():
    header = _cookie_header(storage(cookies=[
        {"name": "sid", "value": "abc", "domain": ".damaplus.online"},
        {"name": "tracker", "value": "no", "domain": ".ads.example.com"},
    ]))
    assert header == "sid=abc", "another site's cookies must never be forwarded"


def test_several_cookies_are_joined():
    header = _cookie_header(storage(cookies=[
        {"name": "a", "value": "1", "domain": "damaplus.online"},
        {"name": "b", "value": "2", "domain": ".damaplus.online"},
    ]))
    assert header == "a=1; b=2"


def test_the_csrf_token_comes_out_of_local_storage():
    assert _csrf_token(storage()) == "tok"


@pytest.mark.parametrize("local_storage", [
    [],
    [{"name": "unrelated", "value": "{}"}],
    [{"name": "damaplus-auth-session-v1", "value": "not json"}],
    [{"name": "damaplus-auth-session-v1", "value": "{}"}],
])
def test_a_missing_csrf_token_is_not_fatal(local_storage):
    """The request is still worth making; the site may not require it."""
    assert _csrf_token(storage(local_storage=local_storage)) == ""


# -- failures we must report clearly -------------------------------------

def test_a_missing_file_is_reported(tmp_path):
    state = read_account(tmp_path / "absent.storage.json")
    assert not state.ok and "unreadable" in state.error


def test_a_corrupt_file_is_reported(tmp_path):
    path = tmp_path / "broken.storage.json"
    path.write_text("{not json", encoding="utf-8")
    state = read_account(path)
    assert not state.ok and "unreadable" in state.error


def test_a_session_with_no_cookies_never_hits_the_network(session_file, monkeypatch):
    monkeypatch.setattr(account_module.urllib.request, "urlopen",
                        lambda *a, **k: pytest.fail("should not have called the site"))
    state = read_account(session_file(storage(cookies=[])))
    assert not state.ok and "no cookies" in state.error


# -- a good answer --------------------------------------------------------

class FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_a_live_session_yields_the_wallet(session_file, monkeypatch):
    monkeypatch.setattr(account_module.urllib.request, "urlopen",
                        lambda *a, **k: FakeResponse({"player": {
                            "balance": 137.5, "bonusBalance": 20,
                            "displayName": "Selam", "phone": "+251921414686",
                        }}))
    state = read_account(session_file(storage()))
    assert state.ok
    assert state.balance == 137.5 and state.bonus == 20
    assert state.display_name == "Selam"
    assert state.phone_tail == "4686", "the tail is what identifies us to opponents"


def test_a_zero_balance_is_a_real_answer(session_file, monkeypatch):
    monkeypatch.setattr(account_module.urllib.request, "urlopen",
                        lambda *a, **k: FakeResponse({"player": {"balance": 0}}))
    state = read_account(session_file(storage()))
    assert state.ok and state.balance == 0.0, "zero must not be confused with unknown"


def test_a_response_without_a_player_means_signed_out(session_file, monkeypatch):
    monkeypatch.setattr(account_module.urllib.request, "urlopen",
                        lambda *a, **k: FakeResponse({"ok": False}))
    state = read_account(session_file(storage()))
    assert not state.ok and state.signed_out


def test_the_request_carries_the_session(session_file, monkeypatch):
    seen = {}

    def capture(request, *a, **k):
        seen["url"] = request.full_url
        seen["headers"] = {k.lower(): v for k, v in request.headers.items()}
        return FakeResponse({"player": {"balance": 1}})

    monkeypatch.setattr(account_module.urllib.request, "urlopen", capture)
    read_account(session_file(storage()))
    assert seen["url"].endswith("/api/player/session-delta")
    assert seen["headers"]["cookie"] == "sid=abc"
    assert seen["headers"]["x-csrf-token"] == "tok"
    assert seen["headers"]["x-damaplus-role"] == "player"


def test_unset_wallet_fields_come_back_as_unknown(session_file, monkeypatch):
    monkeypatch.setattr(account_module.urllib.request, "urlopen",
                        lambda *a, **k: FakeResponse({"player": {"displayName": "Selam"}}))
    state = read_account(session_file(storage()))
    assert state.ok and state.balance is None and state.bonus is None


def test_state_defaults_are_safe():
    blank = AccountState(False)
    assert blank.balance is None and blank.phone_tail == "" and not blank.signed_out
