"""Separate, explicit data initialization/verification and existing invitation CLI.

Execute using final backend venv and systemd EnvironmentFile as trippilot-api.
No credentials in arguments, no existing DB migration or automatic repair.
"""
from __future__ import annotations
import os
from pathlib import Path
import pwd
import sqlite3
from contextlib import closing
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
DATA = Path("/var/lib/trippilot")


def mysql_selected() -> bool:
    # This deployment entry point receives the root-controlled EnvironmentFile
    # through systemd-run. Do not silently read a release checkout's dotenv.
    return bool(os.environ.get("TRIPPILOT_MYSQL_URL", ""))


def validate_environment() -> None:
    from app.auth import AuthSettings
    from app.config import Settings
    from app.business_usage import UsageSettings
    from app.storage import StorageSettings
    auth, settings, usage, storage = AuthSettings(_env_file=None), Settings(_env_file=None), UsageSettings(_env_file=None), StorageSettings(_env_file=None)
    from config import validate_mysql
    validate_mysql({key: os.environ.get(key, "") for key in ("TRIPPILOT_MYSQL_URL", "BUSINESS_USAGE_IDENTITY_PATH")})
    if (not auth.auth_cookie_secure or auth.auth_allowed_origins != ("https://trip.zijinglab.com",)
            or settings.amap_allowed_page_origins != ("https://trip.zijinglab.com",)
            or usage.business_usage_path != str(DATA / "usage.sqlite3")
            or storage.trippilot_database_path != str(DATA / "identity.sqlite3")):
        raise ValueError("production_data_configuration")


def initialize_identity(directory: Path) -> str:
    if mysql_selected(): raise ValueError("mysql_requires_explicit_migration_not_sqlite_init")
    from backup import IDENTITY_COLUMNS, inspect_database, private_directory
    from app.storage import engine_for
    private_directory(directory)
    path = directory / "identity.sqlite3"
    if path.exists() or path.is_symlink():
        inspect_database(path, IDENTITY_COLUMNS)
        return "Existing identity DB verified read-only; not initialized or migrated."
    if any(p.exists() or p.is_symlink() for p in (directory / "usage.sqlite3", directory / "usage.sqlite3.identity", directory / ".usage-initialized")):
        raise ValueError("existing_state_without_identity_investigate")
    if any(p.exists() or p.is_symlink() for p in (Path(str(path) + suffix) for suffix in ("-wal", "-shm", "-journal"))):
        raise ValueError("orphan_identity_sidecar")
    engine_for(str(path)).dispose()
    engine_for.cache_clear()
    inspect_database(path, IDENTITY_COLUMNS)
    return "New private identity DB initialized; no account created."


def initialize_usage(directory: Path) -> str:
    if mysql_selected(): raise ValueError("mysql_requires_explicit_migration_not_sqlite_init")
    from app.business_usage import UsageStore
    from backup import IDENTITY_COLUMNS, inspect_database, private_directory, private_file, write_new
    private_directory(directory)
    path, anchor = directory / "usage.sqlite3", directory / "usage.sqlite3.identity"
    marker = directory / ".usage-initialized"
    if marker.exists() or marker.is_symlink():
        private_file(marker)
        if marker.read_bytes() != b"release-1f-usage-initialized\n": raise ValueError("usage_marker")
        if not path.exists() or not anchor.exists(): raise ValueError("initialized_usage_missing_do_not_recreate")
    if path.exists() and anchor.exists():
        UsageStore().verify()
        message = "Existing usage ledger verified; no reinitialization or reset."
    elif not any(p.exists() or p.is_symlink() for p in (path, anchor)):
        if any(p.exists() or p.is_symlink() for p in (Path(str(path) + suffix) for suffix in ("-wal", "-shm", "-journal"))):
            raise ValueError("orphan_usage_sidecar")
        identity = directory / "identity.sqlite3"
        inspect_database(identity, IDENTITY_COLUMNS)
        with closing(sqlite3.connect(identity.as_uri() + "?mode=ro", uri=True)) as db:
            if any(db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in ("accounts", "auth_sessions", "saved_trips", "login_buckets")):
                raise ValueError("existing_identity_no_new_ledger_investigate")
        UsageStore().initialize()
        message = "New independent usage ledger initialized ONCE."
    else: raise ValueError("partial_usage_store_investigate")
    if not marker.exists(): write_new(marker, b"release-1f-usage-initialized\n")
    return message


def verify_active_storage(directory: Path) -> str:
    if mysql_selected():
        from tools.mysql_migration import verify
        verify()
        return "MySQL private storage, persistent usage and existing external anchor verified; SQLite files were not treated as active."
    from backup import inspect_pair
    inspect_pair(directory)
    return "Identity, usage ledger and anchor verified read-only."


def main() -> int:
    try:
        os.umask(0o077)
        if os.geteuid() != pwd.getpwnam("trippilot-api").pw_uid: raise ValueError("service_identity_required")
        if len(sys.argv) < 2: raise ValueError("explicit_action_required")
        validate_environment()
        action = sys.argv[1]
        if action == "init-identity":
            print(initialize_identity(DATA))
        elif action == "init-usage":
            print(initialize_usage(DATA))
        elif action == "verify":
            print(verify_active_storage(DATA))
        elif action == "invite":
            verify_active_storage(DATA)
            import runpy
            sys.argv = ["account_admin", "create", *sys.argv[2:]]
            runpy.run_module("tools.account_admin", run_name="__main__")
        else: raise ValueError("unsupported_action")
        return 0
    except Exception:
        print("Data operation stopped; investigate locally. No reset, repair, migration or fallback was performed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
