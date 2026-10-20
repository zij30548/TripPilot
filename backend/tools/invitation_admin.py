"""Local MySQL invitation administration. Tokens are never command arguments.

Creation prints the newly generated secret exactly once on an interactive
terminal. Revoke accepts only the public record ID, not the invitation secret.
"""
import argparse
import sys

from sqlalchemy.exc import SQLAlchemyError

from app.auth import create_invitation, revoke_invitation
from app.storage import StorageError


def main() -> int:
    parser = argparse.ArgumentParser(description="Local invitation-code administration (MySQL only)")
    commands = parser.add_subparsers(dest="action", required=True)
    create = commands.add_parser("create")
    create.add_argument("--valid-hours", type=int, default=24)
    revoke = commands.add_parser("revoke")
    revoke.add_argument("invitation_id")
    args = parser.parse_args()
    try:
        if args.action == "create":
            if not sys.stdin.isatty() or not sys.stdout.isatty():
                print("interactive_terminal_required", file=sys.stderr)
                return 2
            identifier, token = create_invitation(args.valid_hours)
            print(f"Invitation ID (for revocation): {identifier}")
            print(f"One-time invitation code (share privately, never log): {token}")
        else:
            revoke_invitation(args.invitation_id)
            print("Invitation revoked.")
        return 0
    except (StorageError, SQLAlchemyError, OSError, ValueError):
        print("invitation_operation_failed", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("invitation_operation_cancelled", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
