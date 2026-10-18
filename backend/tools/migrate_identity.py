"""Copy-only migration and rollback artifact creation; no secret arguments."""
import argparse
from pathlib import Path

from app.storage import StorageError, migrate_legacy_copy, restore_legacy_backup


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate a COPY of a legacy archive; never modify the source.")
    commands = parser.add_subparsers(dest="command", required=True)
    migrate = commands.add_parser("migrate")
    migrate.add_argument("--source", type=Path, required=True)
    migrate.add_argument("--destination", type=Path, required=True)
    migrate.add_argument("--backup", type=Path, required=True)
    rollback = commands.add_parser("rollback")
    rollback.add_argument("--backup", type=Path, required=True)
    rollback.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "migrate":
            if not all(p.is_absolute() for p in (args.source, args.destination, args.backup)):
                raise StorageError("invalid_migration_paths")
            result = migrate_legacy_copy(args.source, args.destination, args.backup)
            print(f"Migration complete: {result['storage_version']}; quarantined records: {result['quarantined_records']}.")
        else:
            if not all(p.is_absolute() for p in (args.backup, args.destination)):
                raise StorageError("invalid_migration_paths")
            restore_legacy_backup(args.backup, args.destination)
            print("Rollback copy created. Use the preceding application with this copy; newer writes are not merged.")
        return 0
    except StorageError as error:
        print(f"Storage operation failed: {error.code}.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
