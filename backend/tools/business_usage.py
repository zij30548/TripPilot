"""Explicit initialization/verification only: no reset, refund or network call."""
import argparse

from core.business_usage import UsageError, UsageSettings, UsageStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify configured MySQL usage or explicitly initialize/verify a legacy independent SQLite ledger.")
    parser.add_argument("command", choices=("init", "verify"))
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--database", help="New/existing absolute legacy SQLite usage database path")
    target.add_argument("--mysql", action="store_true", help="Verify configured MySQL; initialization requires the explicit full-storage provisioning/migration tool")
    arguments = parser.parse_args()
    try:
        if arguments.mysql:
            from memory.mysql_storage import mysql_configured
            from memory.mysql_usage import MySQLUsageStore
            if not mysql_configured() or arguments.command != "verify":
                raise UsageError()
            store = MySQLUsageStore()
        else:
            settings = UsageSettings(business_usage_path=arguments.database)
            store = UsageStore(settings)
        store.initialize() if arguments.command == "init" else store.verify()
    except (UsageError, ValueError):
        print("Business usage operation refused; investigate configuration, existing files or storage integrity. No quota reset performed.")
        return 1
    print("Business usage ledger initialized." if arguments.command == "init" else "Business usage ledger verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
