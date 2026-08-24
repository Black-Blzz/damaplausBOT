"""Operator sign-in: hashing, sessions, and the brute-force throttle."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard import users as users_module
from dashboard.users import Accounts, display_name, hash_password, verify_password

OPERATORS = [
    ("kaliesh", "damaplus@1234"),
    ("dagi", "damaplus@1234"),
    ("bass", "damaplus@1234"),
]


@pytest.fixture
def accounts():
    return Accounts()


@pytest.mark.parametrize("username,password", OPERATORS)
def test_each_operator_can_sign_in(accounts, username, password):
    token, problem = accounts.authenticate(username, password)
    assert token and not problem
    assert accounts.username_for(token) == username


@pytest.mark.parametrize("username,password", OPERATORS)
def test_the_console_greets_by_name(username, password):
    assert display_name(username)[0].isupper()


def test_usernames_are_case_insensitive(accounts):
    token, _ = accounts.authenticate("KALIESH", "damaplus@1234")
    assert accounts.username_for(token) == "kaliesh"


@pytest.mark.parametrize("username,password", [
    ("kaliesh", "wrong"),
    ("kaliesh", ""),
    ("bass", "damapluss@1234"),   # the old typo'd password must not still work
    ("dagi", "damapluss@1234"),
    ("nobody", "damaplus@1234"),
    ("", "damaplus@1234"),
])
def test_bad_credentials_are_refused(accounts, username, password):
    token, problem = accounts.authenticate(username, password)
    assert not token and problem


def test_only_the_three_operators_exist():
    assert set(users_module.DEFAULT_USERS) == {"kaliesh", "dagi", "bass"}


def test_no_plaintext_password_is_stored():
    """Reading the source must not hand anyone a login."""
    source = Path(users_module.__file__).read_text(encoding="utf-8")
    for _, password in OPERATORS:
        assert password not in source
    for digest in users_module.DEFAULT_USERS.values():
        assert digest.startswith("pbkdf2_sha256$")


def test_hashes_are_salted_per_password():
    assert hash_password("same") != hash_password("same")
    encoded = hash_password("same")
    assert verify_password("same", encoded)
    assert not verify_password("other", encoded)


def test_a_corrupt_digest_never_authenticates():
    for bad in ("", "nonsense", "md5$1$x$y", "pbkdf2_sha256$notanumber$a$b"):
        assert not verify_password("anything", bad)


# -- sessions -------------------------------------------------------------

def test_signing_out_invalidates_the_session(accounts):
    token, _ = accounts.authenticate("dagi", "damaplus@1234")
    accounts.logout(token)
    assert accounts.username_for(token) == ""


def test_an_unknown_token_is_nobody(accounts):
    assert accounts.username_for("made-up") == ""
    assert accounts.username_for("") == ""


def test_an_idle_session_expires(accounts, monkeypatch):
    token, _ = accounts.authenticate("bass", "damaplus@1234")
    monkeypatch.setattr(users_module, "SESSION_IDLE_SECONDS", 0.0)
    assert accounts.username_for(token) == ""


def test_signed_in_lists_current_operators(accounts):
    accounts.authenticate("kaliesh", "damaplus@1234")
    accounts.authenticate("dagi", "damaplus@1234")
    assert accounts.signed_in() == ["dagi", "kaliesh"]


# -- throttle -------------------------------------------------------------

def test_repeated_failures_lock_the_account(accounts):
    for _ in range(users_module.MAX_FAILURES):
        accounts.authenticate("kaliesh", "wrong")
    token, problem = accounts.authenticate("kaliesh", "damaplus@1234")
    assert not token
    assert "Too many failed attempts" in problem


def test_the_lockout_is_per_username(accounts):
    for _ in range(users_module.MAX_FAILURES):
        accounts.authenticate("kaliesh", "wrong")
    token, _ = accounts.authenticate("dagi", "damaplus@1234")
    assert token, "one operator's mistakes must not lock another out"


def test_a_success_clears_earlier_failures(accounts):
    for _ in range(users_module.MAX_FAILURES - 1):
        accounts.authenticate("dagi", "wrong")
    assert accounts.authenticate("dagi", "damaplus@1234")[0]
    for _ in range(users_module.MAX_FAILURES - 1):
        accounts.authenticate("dagi", "wrong")
    assert accounts.authenticate("dagi", "damaplus@1234")[0]
