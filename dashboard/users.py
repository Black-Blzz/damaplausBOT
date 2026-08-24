"""Operator accounts and signed-in sessions for the console.

Passwords are never stored in the clear, here or in the repository: each one is
a PBKDF2-HMAC-SHA256 digest with its own salt, so reading this file does not
hand anyone a login.  Change a password with::

    python -m dashboard --set-password <username>

which rewrites ``dashboard-users.json``; that file, when present, replaces the
built-in accounts entirely.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time

from .config import BASE_DIR

USERS_FILE = BASE_DIR / "dashboard-users.json"

ITERATIONS = 240_000
SESSION_TTL_SECONDS = 12 * 3600
SESSION_IDLE_SECONDS = 4 * 3600
COOKIE_NAME = "fleet_session"

# Brute-force throttle: after this many failures a username pauses.
MAX_FAILURES = 5
LOCKOUT_SECONDS = 120.0

# The three operators. Digests only -- the plaintext lives nowhere in the repo.
DEFAULT_USERS = {
    "kaliesh": "pbkdf2_sha256$240000$HrK71LMhAkjES69hZnD8ew==$aMYORb5oBXUBM0wr2WyA1nY1fiPzTBLeUR1+9heAxyc=",
    "dagi": "pbkdf2_sha256$240000$+DL4qo6NS98O3DI74Q9dgw==$Io9qIWnISJxT+Y3IykHe2Wyat/AvFRNVqGFrM1GFLUM=",
    "bass": "pbkdf2_sha256$240000$/ymDKW0lJu6tScJ0fFvSaw==$nN3AcZ7lRQwOHwuHwLVP/9p9AXMoR580F7SAg3vafcM=",
}

# How each operator's name is written when the console greets them.
DISPLAY_NAMES = {"kaliesh": "Kaliesh", "dagi": "Dagi", "bass": "Bass"}


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def hash_password(password: str, iterations: int = ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time check of a password against a stored digest."""
    try:
        scheme, iterations, salt, expected = encoded.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), base64.b64decode(salt), int(iterations)
        )
        return hmac.compare_digest(candidate, base64.b64decode(expected))
    except (ValueError, TypeError):
        return False


def load_users() -> dict[str, str]:
    """Accounts from dashboard-users.json, falling back to the built-ins."""
    try:
        stored = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        users = {str(k).lower(): str(v) for k, v in (stored.get("users") or {}).items()}
        if users:
            return users
    except (OSError, ValueError):
        pass
    return dict(DEFAULT_USERS)


def save_users(users: dict[str, str]) -> None:
    USERS_FILE.write_text(json.dumps({"users": users}, indent=2), encoding="utf-8")


def display_name(username: str) -> str:
    return DISPLAY_NAMES.get(username, username.capitalize())


class Accounts:
    """Authenticates operators and tracks who is signed in."""

    def __init__(self, log=None):
        self._log = log or (lambda _message: None)
        self._lock = threading.Lock()
        self._users = load_users()
        self._sessions: dict[str, dict] = {}
        self._failures: dict[str, list] = {}

    # -- login -----------------------------------------------------------
    def authenticate(self, username: str, password: str) -> tuple[str, str]:
        """Return ``(session_token, "")`` or ``("", reason)``."""
        name = (username or "").strip().lower()
        if not name or not password:
            return "", "Enter your username and password."

        locked = self._locked_for(name)
        if locked:
            return "", f"Too many failed attempts. Try again in {locked} seconds."

        digest = self._users.get(name)
        # Hash even when the user is unknown, so a missing account and a wrong
        # password take the same time and cannot be told apart.
        placeholder = next(iter(self._users.values()), hash_password("x"))
        if not verify_password(password, digest or placeholder) or digest is None:
            self._record_failure(name)
            self._log(f"failed sign-in for '{name}'")
            return "", "That username and password do not match."

        self._clear_failures(name)
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            self._sessions[token] = {"username": name, "created_at": now, "seen_at": now}
        self._log(f"{display_name(name)} signed in")
        return token, ""

    def logout(self, token: str) -> None:
        with self._lock:
            entry = self._sessions.pop(token, None)
        if entry:
            self._log(f"{display_name(entry['username'])} signed out")

    def username_for(self, token: str) -> str:
        """The signed-in user for a token, refreshing its idle timer."""
        if not token:
            return ""
        now = time.time()
        with self._lock:
            entry = self._sessions.get(token)
            if entry is None:
                return ""
            if now - entry["created_at"] > SESSION_TTL_SECONDS or \
               now - entry["seen_at"] > SESSION_IDLE_SECONDS:
                del self._sessions[token]
                return ""
            entry["seen_at"] = now
            return entry["username"]

    def signed_in(self) -> list[str]:
        now = time.time()
        with self._lock:
            return sorted({
                entry["username"] for entry in self._sessions.values()
                if now - entry["seen_at"] <= SESSION_IDLE_SECONDS
            })

    def sweep(self) -> None:
        """Drop sessions that have expired or gone idle."""
        now = time.time()
        with self._lock:
            for token, entry in list(self._sessions.items()):
                if now - entry["created_at"] > SESSION_TTL_SECONDS or \
                   now - entry["seen_at"] > SESSION_IDLE_SECONDS:
                    del self._sessions[token]

    # -- throttle --------------------------------------------------------
    def _locked_for(self, name: str) -> int:
        now = time.time()
        with self._lock:
            attempts = [t for t in self._failures.get(name, []) if now - t < LOCKOUT_SECONDS]
            self._failures[name] = attempts
            if len(attempts) >= MAX_FAILURES:
                return int(LOCKOUT_SECONDS - (now - attempts[0])) + 1
        return 0

    def _record_failure(self, name: str) -> None:
        with self._lock:
            self._failures.setdefault(name, []).append(time.time())

    def _clear_failures(self, name: str) -> None:
        with self._lock:
            self._failures.pop(name, None)
