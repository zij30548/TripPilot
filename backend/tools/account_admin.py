"""Local operator only: python -B -m tools.account_admin create USERNAME.

Passwords are read twice with getpass, never command arguments or environment.
Database selection uses trusted TRIPPILOT_MYSQL_URL when explicitly configured,
otherwise the historical TRIPPILOT_DATABASE_PATH. Selection failures never fall
back to a different database.
"""
import argparse
import getpass
import sys

from argon2.exceptions import HashingError
from sqlalchemy.exc import SQLAlchemyError

from app.auth import USERNAME_PATTERN, create_account, revoke_account
from app.storage import StorageError


def main() -> int:
    parser = argparse.ArgumentParser(description="Local invitation account administration")
    parser.add_argument("action", choices=("create", "revoke", "disable"))
    parser.add_argument("username")
    args = parser.parse_args()
    if USERNAME_PATTERN.fullmatch(args.username) is None:
        print("invalid_account_input", file=sys.stderr)
        return 2
    try:
        if args.action == "create":
            if not sys.stdin.isatty():
                print("interactive_terminal_required", file=sys.stderr)
                return 2
            first = getpass.getpass("Invitation password (12–128 characters): ")
            second = getpass.getpass("Repeat password: ")
            if first != second:
                print("passwords_do_not_match", file=sys.stderr)
                return 2
            identifier = create_account(args.username, first)
            print(f"Account created: {identifier}")
        else:
            count = revoke_account(args.username, disable=args.action == "disable")
            print(f"Sessions revoked: {count}")
        return 0
    except (StorageError, ValueError, SQLAlchemyError, OSError, HashingError):
        # Do not expose SQL, database paths, password hashes or exception inputs.
        print("account_operation_failed", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("account_operation_cancelled", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
