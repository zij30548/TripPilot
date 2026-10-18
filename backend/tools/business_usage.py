"""Explicit initialization/verification only: no reset, refund or network call."""
import argparse

from app.business_usage import UsageError, UsageSettings, UsageStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize or verify the independent business usage ledger (never an identity/archive DB).")
    parser.add_argument("command", choices=("init", "verify"))
    parser.add_argument("--database", required=True, help="New/existing absolute usage database path")
    arguments = parser.parse_args()
    try:
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
