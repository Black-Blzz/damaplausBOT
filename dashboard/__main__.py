"""Entry point:  python -m dashboard [--host 0.0.0.0] [--port 8080]"""

from __future__ import annotations

import argparse
import getpass
import sys

from .server import serve
from .users import hash_password, load_users, save_users


def set_password(username: str) -> int:
    name = username.strip().lower()
    users = load_users()
    if name not in users:
        print(f"No operator called {name!r}. Known: {', '.join(sorted(users))}")
        return 1
    first = getpass.getpass(f"New password for {name}: ")
    if not first:
        print("Nothing entered; leaving the password unchanged.")
        return 1
    if first != getpass.getpass("Repeat it: "):
        print("Those did not match; leaving the password unchanged.")
        return 1
    users[name] = hash_password(first)
    save_users(users)
    print(f"Password updated for {name}.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="DamaPlus bot fleet console")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind address; use 0.0.0.0 to serve a network")
    parser.add_argument("--bot-token", default="",
                        help="secret the bots use; generated per run when omitted")
    parser.add_argument("--set-password", metavar="USERNAME",
                        help="change an operator's password and exit")
    args = parser.parse_args()

    if args.set_password:
        sys.exit(set_password(args.set_password))
    serve(args.port, args.host, args.bot_token)


if __name__ == "__main__":
    main()
