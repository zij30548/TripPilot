"""Stop-writer, copy-only SQLite -> MySQL migration. No reset or overwrite.

Connection credentials come only from trusted server configuration. Sources
must be a private, self-contained stopped backup pair made by deploy/backup.py.
DDL may commit independently; the final private version marker is published in
the SAME DML transaction as all imported accounts, sessions, archives and usage.
Any partial target is retained for investigation and must not be retried over.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from typing import Iterable

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import SQLModel
from argon2 import extract_parameters, Type
from argon2.exceptions import InvalidHashError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from deploy.backup import (FILES, Refused,
                           inspect_pair, private_directory, sha256)
from core.business_usage import UsageError
from memory.mysql_storage import (MYSQL_STORAGE_VERSION, MySQLSettings,
                               current_mysql_engine, engine_from_url,
                               initialize_private_schema, validate_private_schema)
from memory.mysql_usage import (initialize_usage_schema,
                             import_usage_rows, validate_usage_schema)
from memory.saved_trips import SavedTripRow, detail
from memory.storage import StorageError

COPY_ORDER = ("accounts", "auth_sessions", "login_buckets", "saved_trips")
MAX_ROW_BYTES = 16 * 1024 * 1024  # includes escaped JSON; original archive limit remains 2 MiB


def _digest_rows(rows: Iterable[dict[str, object]]) -> tuple[str, int]:
    digest, count = hashlib.sha256(), 0
    for row in rows:
        value = dict(row)
        if "disabled" in value:
            value["disabled"] = int(value["disabled"])
        serialized = json.dumps(value, ensure_ascii=True, sort_keys=True,
                                separators=(",", ":"), allow_nan=False).encode("ascii")
        if len(serialized) > MAX_ROW_BYTES:
            raise Refused("migration_row_too_large")
        digest.update(serialized + b"\n")
        count += 1
    return digest.hexdigest(), count


def _sqlite_rows(db: sqlite3.Connection, table: str) -> Iterable[dict[str, object]]:
    if table not in (*COPY_ORDER, "usage_attempts"):
        raise Refused("migration_table_not_allowed")
    db.row_factory = sqlite3.Row
    for row in db.execute(f"SELECT * FROM {table} ORDER BY id"):
        yield dict(row)


def _mysql_rows(db: Connection, table: str) -> Iterable[dict[str, object]]:
    if table not in (*COPY_ORDER, "usage_attempts"):
        raise Refused("migration_table_not_allowed")
    query = text(f"SELECT * FROM {table} ORDER BY id").execution_options(stream_results=True, yield_per=1)
    # PyMySQL otherwise buffers the complete table before the first row. Scope
    # the server-side cursor to this statement and close it even on rejection.
    with db.execute(query) as result:
        for row in result.mappings():
            yield dict(row)


def _source_hashes(directory: Path) -> dict[str, str]:
    # Stopped backups must be self-contained; never copy live WAL/journal pairs.
    for name in FILES[:2]:
        if any((directory / (name + suffix)).exists() for suffix in ("-wal", "-shm", "-journal")):
            raise Refused("self_contained_stopped_backup_required")
    return {name: sha256(directory / name) for name in FILES}


def _source_rows_valid(table: str, row: dict[str, object]) -> None:
    model = SQLModel.metadata.tables[table]
    if set(row) != set(model.c.keys()):
        raise Refused("migration_source_fields")
    for column in model.c:
        value = row[column.name]
        if value is None:
            if not column.nullable:
                raise Refused("migration_source_null")
        elif isinstance(value, str) and (getattr(column.type, "length", None) is not None
                                        and len(value) > column.type.length):
            raise Refused("migration_source_capacity")
    if re.fullmatch(r"[0-9a-f]{32}" if table != "login_buckets" else r"[0-9a-f]{64}", str(row["id"])) is None:
        raise Refused("migration_source_identity")
    for key in ("created_at", "expires_at", "window_start"):
        if key in row and (type(row[key]) is not int or not 0 <= row[key] <= 2**63 - 1):
            raise Refused("migration_source_time")
    if table == "accounts":
        if re.fullmatch(r"[a-z0-9_.-]{3,48}", str(row["username"])) is None or row["disabled"] not in (0, 1):
            raise Refused("migration_source_account")
        try:
            if extract_parameters(row["password_hash"]).type is not Type.ID:
                raise Refused("migration_source_password_format")
        except (InvalidHashError, TypeError):
            raise Refused("migration_source_password_format") from None
    elif table == "auth_sessions":
        if (re.fullmatch(r"[0-9a-f]{32}", str(row["user_id"])) is None
                or re.fullmatch(r"[0-9a-f]{64}", str(row["token_hash"])) is None
                or re.fullmatch(r"[A-Za-z0-9_-]{43}", str(row["csrf_token"])) is None
                or row["expires_at"] <= row["created_at"]):
            raise Refused("migration_source_session")
    elif table == "login_buckets":
        if type(row["attempts"]) is not int or not 0 <= row["attempts"] <= 2**31 - 1:
            raise Refused("migration_source_limiter")
    elif table == "saved_trips":
        # Full v1/v2/v3 contract/replay validation, not just a JSON copy.
        detail(SavedTripRow(**row))


def _write_receipt(fd: int, value: dict[str, object]) -> None:
    data = json.dumps(value, sort_keys=True, indent=2).encode() + b"\n"
    os.lseek(fd, 0, os.SEEK_SET)
    os.ftruncate(fd, 0)
    os.write(fd, data)
    os.fsync(fd)


def _migrate(source: Path, engine: Engine, receipt: Path, *, source_stopped: bool) -> dict[str, object]:
    if not source_stopped:
        raise Refused("explicit_stopped_backup_confirmation_required")
    private_directory(source)
    if not receipt.is_absolute() or receipt.is_symlink():
        raise Refused("private_new_receipt_required")
    private_directory(receipt.parent)
    original = inspect_pair(source)
    source_hashes = _source_hashes(source)
    with engine.connect() as connection:
        if inspect(connection).get_table_names():
            raise Refused("mysql_target_not_empty")
    fd = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    record: dict[str, object] = {
        "version": "sqlite-to-mysql-v1", "state": "started",
        "source_files_sha256": source_hashes,
        "target_identity_sha256": hashlib.sha256(
            (str(engine.url.host) + ":" + str(engine.url.port) + "/" + str(engine.url.database)).encode()).hexdigest(),
        "accounts_and_archives": "preserve_ids_ownership_json_and_times",
        "sessions": "preserve_original_expiry_and_revocation",
        "usage": "all_states_preserved_no_refunds",
    }
    _write_receipt(fd, record)
    try:
        identity = str(original["usage"]["ledger_identity"])
        initialize_private_schema(engine, ready=False)
        initialize_usage_schema(engine, identity)
        expected: dict[str, tuple[str, int]] = {}
        with closing(sqlite3.connect((source / FILES[0]).as_uri() + "?mode=ro", uri=True)) as private, \
                closing(sqlite3.connect((source / FILES[1]).as_uri() + "?mode=ro", uri=True)) as usage, \
                engine.begin() as target:
            private.execute("PRAGMA trusted_schema=OFF")
            usage.execute("PRAGMA trusted_schema=OFF")
            private.execute("BEGIN")
            usage.execute("BEGIN")
            for table in COPY_ORDER:
                expected[table] = _digest_rows(_sqlite_rows(private, table))
                # Batched inserts keep migration memory bounded by one archive.
                for row in _sqlite_rows(private, table):
                    _source_rows_valid(table, row)
                    target.execute(SQLModel.metadata.tables[table].insert(), row)
            # All attempts remain authoritative, even failed/cancelled/uncertain.
            # Never discard an attempt merely because its historical owner was
            # quarantined or an account was disabled.
            expected["usage_attempts"] = _digest_rows(_sqlite_rows(usage, "usage_attempts"))
            import_usage_rows(target, _sqlite_rows(usage, "usage_attempts"))
            for table, desired in expected.items():
                if _digest_rows(_mysql_rows(target, table)) != desired:
                    raise Refused("migration_row_digest_mismatch")
            if _source_hashes(source) != source_hashes or inspect_pair(source) != original:
                raise Refused("migration_source_changed")
            target.execute(text("INSERT INTO storage_metadata (`key`, value) VALUES ('version', :version)"),
                           {"version": MYSQL_STORAGE_VERSION})
        # After an uncertain COMMIT error we do not erase or blindly retry.
        validate_private_schema(engine)
        with engine.connect() as target:
            validate_usage_schema(target, identity)
        record.update(state="verified", tables={name: {"sha256": value[0], "rows": value[1]}
                                              for name, value in expected.items()},
                      source_unchanged=_source_hashes(source) == source_hashes,
                      ledger_identity_preserved=True)
        _write_receipt(fd, record)
        return record
    except BaseException:
        record["state"] = "failed_or_uncertain_inspect_target_do_not_retry"
        _write_receipt(fd, record)
        raise
    finally:
        os.close(fd)


def migrate(source: Path, engine: Engine, receipt: Path, *, source_stopped: bool) -> dict[str, object]:
    if engine.dialect.name != "mysql":
        raise Refused("mysql_required")
    # Hold the advisory lock on a dedicated connection across DDL and the final
    # import transaction. No generic ignore-version/retry/overwrite switch.
    key = "trippilot_migrate_" + hashlib.sha256(str(engine.url.database).encode()).hexdigest()[:32]
    with engine.connect() as lock:
        if lock.execute(text("SELECT GET_LOCK(:key, 0)"), {"key": key}).scalar() != 1:
            raise Refused("migration_already_running")
        try:
            return _migrate(source, engine, receipt, source_stopped=source_stopped)
        finally:
            lock.execute(text("SELECT RELEASE_LOCK(:key)"), {"key": key})


def verify() -> None:
    from core.business_usage import get_usage_store
    current_mysql_engine()
    get_usage_store().verify()


def main() -> int:
    parser = argparse.ArgumentParser(description="Explicit copy-only full MySQL migration; never reset a quota or overwrite a target.")
    commands = parser.add_subparsers(dest="command", required=True)
    copy = commands.add_parser("migrate")
    copy.add_argument("--source-directory", type=Path, required=True)
    copy.add_argument("--receipt", type=Path, required=True)
    copy.add_argument("--confirm-source-stopped", action="store_true")
    commands.add_parser("verify")
    args = parser.parse_args()
    try:
        if args.command == "verify":
            verify()
            print("MySQL private storage, persistent usage and external identity anchor verified; no external business call.")
        else:
            raw = MySQLSettings().trippilot_mysql_url.get_secret_value()
            result = migrate(args.source_directory, engine_from_url(raw), args.receipt,
                             source_stopped=args.confirm_source_stopped)
            print(json.dumps({"result": "migration_verified", "tables": {key: item["rows"] for key, item in result["tables"].items()},
                              "source_unchanged": result["source_unchanged"], "usage_refunded": False}))
        return 0
    except (StorageError, UsageError, Refused, SQLAlchemyError, sqlite3.Error, OSError, ValueError, TypeError):
        print("MySQL operation stopped. Preserve source, target and receipt; inspect locally. No automatic retry, reset or fallback.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
