"""Explicit isolated-copy chat schema upgrade/rehearsal; never runtime DDL.

The source remains read-only and is the pre-upgrade rollback artifact. This is
not a production cutover/restore command: new writes make an older source stale,
and rolling quota/session state back is never authorized by a schema upgrade.
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
import stat

from pydantic import SecretStr
from sqlalchemy import inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import SQLModel

from memory.chat_models import CHAT_TABLES, CHAT_VERSION
from memory.chat_storage import chat_tables, validate_chat_extension
from memory.mysql_storage import (MYSQL_STORAGE_VERSION, MySQLSettings, current_mysql_engine,
                               engine_from_url, initialize_private_schema, private_tables, validate_private_schema)
from memory.mysql_usage import initialize_usage_schema, usage_attempts, usage_metadata, validate_usage_schema
from memory.storage import LEGACY_PATH, STORAGE_VERSION, StorageError, _backup_new, engine_for


class Refused(Exception):
    pass


class CopySettings(MySQLSettings):
    trippilot_chat_copy_target_url: SecretStr = SecretStr("")


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _private_existing(path: Path) -> None:
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise Refused("private_source_required")
    info = path.stat()
    if info.st_nlink != 1 or stat.S_IMODE(info.st_mode) & 0o077:
        raise Refused("private_source_required")
    for parent in path.parents:
        if parent.is_symlink():
            raise Refused("symlink_path_refused")


def _new_receipt(path: Path) -> int:
    if (not path.is_absolute() or path.parent.is_symlink() or not path.parent.is_dir()
            or stat.S_IMODE(path.parent.stat().st_mode) & 0o077):
        raise Refused("private_receipt_directory_required")
    if any(parent.is_symlink() for parent in path.parents):
        raise Refused("symlink_path_refused")
    return os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)


def _write(fd: int, record: dict) -> None:
    data = json.dumps(record, ensure_ascii=True, sort_keys=True).encode("ascii") + b"\n"
    os.lseek(fd, 0, os.SEEK_SET)
    os.ftruncate(fd, 0)
    os.write(fd, data)
    os.fsync(fd)


def provision_chat_extension(engine: Engine) -> None:
    """Only called by explicit tools/tests after base schema validation."""
    with engine.connect() as connection:
        if CHAT_TABLES & set(inspect(connection).get_table_names()) or connection.execute(
                text("SELECT value FROM storage_metadata WHERE `key`='chat_version'")).scalar() is not None:
            raise Refused("chat_extension_already_present")
    # MySQL DDL can partially commit. A missing ready marker rejects partial
    # targets; the CLI retains target + receipt and never retries automatically.
    SQLModel.metadata.create_all(engine, tables=chat_tables(), checkfirst=False)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO storage_metadata (`key`,value) VALUES ('chat_version',:version)"), {"version": CHAT_VERSION})
        validate_chat_extension(connection)


def sqlite_copy(source: Path, destination: Path, receipt: Path, *, confirm_source_stopped: bool,
                rollback: bool = False) -> dict:
    if not confirm_source_stopped:
        raise Refused("source_stopped_confirmation_required")
    _private_existing(source)
    if (not destination.is_absolute() or destination.resolve() in {source.resolve(), LEGACY_PATH.resolve()}
            or destination.exists() or destination.is_symlink() or receipt.resolve() in {source.resolve(), destination.resolve()}):
        raise Refused("new_isolated_destination_required")
    if (not destination.parent.is_dir() or stat.S_IMODE(destination.parent.stat().st_mode) & 0o077
            or any(parent.is_symlink() for parent in destination.parents)):
        raise Refused("private_destination_directory_required")
    for suffix in ("-wal", "-shm", "-journal"):
        if Path(str(source) + suffix).exists():
            raise Refused("self_contained_stopped_source_required")
    original_hash = _hash(source)
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as db:
        if db.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise Refused("invalid_source")
        metadata = dict(db.execute("SELECT key,value FROM storage_metadata"))
        expected_tables = {"storage_metadata", "accounts", "auth_sessions", "login_buckets", "saved_trips"}
        if rollback:
            expected_tables |= CHAT_TABLES
        actual_tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if actual_tables != expected_tables:
            raise Refused("unexpected_source_tables")
        if metadata.get("version") != STORAGE_VERSION:
            raise Refused("unsupported_source_version")
        if rollback:
            if metadata.get("chat_version") != CHAT_VERSION or any(db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in sorted(CHAT_TABLES)):
                raise Refused("rollback_rehearsal_requires_empty_chat")
        elif metadata.get("chat_version") is not None:
            raise Refused("chat_extension_already_present")
    fd = _new_receipt(receipt)
    record = {"version": "chat-copy-receipt-v1", "state": "started", "mode": "rollback-rehearsal" if rollback else "upgrade-copy",
              "source_sha256": original_hash, "chat_version": CHAT_VERSION, "production_cutover": False,
              "source_unchanged": None, "quota_refunded": False}
    try:
        _write(fd, record)
        _backup_new(source, destination)
        if rollback:
            with closing(sqlite3.connect(destination)) as db:
                db.execute("PRAGMA foreign_keys=ON")
                db.execute("BEGIN IMMEDIATE")
                db.execute("DROP TABLE chat_turns")
                db.execute("DROP TABLE chat_conversations")
                db.execute("DELETE FROM storage_metadata WHERE key='chat_version'")
                db.commit()
            engine = engine_for(str(destination))
        else:
            engine = engine_for(str(destination))
            provision_chat_extension(engine)
        engine.dispose()
        engine_for.cache_clear()
        if _hash(source) != original_hash:
            raise Refused("source_changed")
        record.update(state="verified", source_unchanged=True, target_sha256=_hash(destination))
        _write(fd, record)
        return record
    except BaseException:
        record["state"] = "failed_or_uncertain_preserve_artifacts"
        _write(fd, record)
        raise
    finally:
        os.close(fd)


def _rows_digest(rows: list[dict]) -> str:
    return hashlib.sha256(json.dumps(rows, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _anchor_identity(path: Path) -> str:
    _private_existing(path)
    if path.stat().st_size != 33:
        raise Refused("invalid_usage_identity_anchor")
    value = path.read_bytes()
    if re.fullmatch(rb"[0-9a-f]{32}\n", value) is None:
        raise Refused("invalid_usage_identity_anchor")
    return value[:-1].decode("ascii")


def _mysql_copy_locked(source: Engine, target: Engine, receipt: Path, anchor: Path, *, confirm_source_stopped: bool,
                       rollback: bool = False) -> dict:
    """Read one consistent source; publish base ready + chat marker together.

    Target name is intentionally rehearsal-only, so a typo cannot upgrade the
    active production database. Never auto-switch application configuration.
    """
    if (not confirm_source_stopped or source.dialect.name != "mysql" or target.dialect.name != "mysql"
            or source.url == target.url or re.fullmatch(r"trippilot_(?:chat|test)_[0-9a-f]{32}", target.url.database or "") is None):
        raise Refused("isolated_mysql_copy_required")
    validate_private_schema(source)
    from memory.chat_places_storage import validate_chat_places_extension
    with source.connect() as connection:
        from memory.assistant_store import validate_assistant_extension
        if validate_assistant_extension(connection, required=False):
            raise Refused("assistant_extension_requires_own_copy_tool")
        if validate_chat_places_extension(connection, required=False):
            raise Refused("chat_places_extension_requires_own_copy_tool")
    expected_identity = _anchor_identity(anchor)
    anchor_hash = _hash(anchor)
    with source.connect() as connection:
        has_chat = validate_chat_extension(connection, required=False)
        if has_chat != rollback:
            raise Refused("source_extension_version_mismatch")
        if rollback and any(connection.execute(select(text("COUNT(*)")).select_from(table)).scalar_one() for table in chat_tables()):
            raise Refused("rollback_rehearsal_requires_empty_chat")
    with target.connect() as connection:
        if inspect(connection).get_table_names():
            raise Refused("mysql_target_not_empty")
    fd = _new_receipt(receipt)
    record = {"version": "chat-copy-receipt-v1", "state": "started", "mode": "rollback-rehearsal" if rollback else "upgrade-copy",
              "chat_version": CHAT_VERSION, "production_cutover": False, "quota_refunded": False, "rows": {},
              "anchor_sha256": anchor_hash}
    try:
        _write(fd, record)
        tables = {table.name: table for table in private_tables()}
        tables.update(usage_metadata=usage_metadata, usage_attempts=usage_attempts)
        order = ("storage_metadata", "accounts", "auth_sessions", "login_buckets", "saved_trips", "invitations", "usage_metadata", "usage_attempts")
        with source.connect().execution_options(isolation_level="REPEATABLE READ") as original, original.begin():
            original.exec_driver_sql("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            identity = original.execute(select(usage_metadata.c.value).where(usage_metadata.c.key == "identity")).scalar_one()
            if identity != expected_identity:
                raise Refused("usage_identity_anchor_mismatch")
            validate_usage_schema(original, identity)
            frozen, row_count, byte_count = {}, 0, 0
            for name in order:
                frozen[name] = []
                for row in original.execution_options(stream_results=True).execute(
                        select(tables[name]).order_by(*tables[name].primary_key.columns)).mappings():
                    item = dict(row)
                    row_count += 1
                    byte_count += len(json.dumps(item, ensure_ascii=True).encode())
                    if row_count > 100_000 or byte_count > 256 * 1024 * 1024:
                        raise Refused("copy_source_too_large")
                    frozen[name].append(item)
            record["rows"] = {name: {"count": len(rows), "sha256": _rows_digest(rows)} for name, rows in frozen.items()}
            record["usage_identity_sha256"] = hashlib.sha256(identity.encode()).hexdigest()
            _write(fd, record)
            initialize_private_schema(target, ready=False)
            initialize_usage_schema(target, identity)
            if not rollback:
                SQLModel.metadata.create_all(target, tables=chat_tables(), checkfirst=False)
            with target.begin() as destination:
                for name in order:
                    if name in {"storage_metadata", "usage_metadata"}:
                        continue
                    if frozen[name]:
                        destination.execute(tables[name].insert(), frozen[name])
                    copied = [dict(row) for row in destination.execute(select(tables[name]).order_by(*tables[name].primary_key.columns)).mappings()]
                    if copied != frozen[name]:
                        raise Refused("copy_verification_failed")
                destination.execute(text("INSERT INTO storage_metadata (`key`,value) VALUES ('version',:version)"), {"version": MYSQL_STORAGE_VERSION})
                if not rollback:
                    destination.execute(text("INSERT INTO storage_metadata (`key`,value) VALUES ('chat_version',:version)"), {"version": CHAT_VERSION})
            # Version/constraint validation includes new extension; all original
            # IDs, sessions, snapshots and every conservative attempt copied.
            validate_private_schema(target)
            with target.connect() as destination:
                validate_usage_schema(destination, identity)
            for name in order:
                current = [dict(row) for row in original.execute(select(tables[name]).order_by(*tables[name].primary_key.columns)).mappings()]
                if current != frozen[name]:
                    raise Refused("source_snapshot_changed")
        record.update(state="verified", source_unchanged=True)
        if _anchor_identity(anchor) != expected_identity or _hash(anchor) != anchor_hash:
            raise Refused("usage_identity_anchor_changed")
        _write(fd, record)
        return record
    except BaseException:
        record["state"] = "failed_or_uncertain_preserve_artifacts"
        _write(fd, record)
        raise
    finally:
        os.close(fd)


def mysql_copy(source: Engine, target: Engine, receipt: Path, *, anchor: Path, confirm_source_stopped: bool,
               rollback: bool = False) -> dict:
    if (target.dialect.name != "mysql" or re.fullmatch(r"trippilot_(?:chat|test)_[0-9a-f]{32}", target.url.database or "") is None):
        raise Refused("isolated_mysql_copy_required")
    key = "chat_copy_" + hashlib.sha256((target.url.database or "").encode()).hexdigest()[:40]
    with target.connect() as lock:
        if lock.execute(text("SELECT GET_LOCK(:key,0)"), {"key": key}).scalar() != 1:
            raise Refused("copy_already_running")
        try:
            return _mysql_copy_locked(source, target, receipt, anchor, confirm_source_stopped=confirm_source_stopped, rollback=rollback)
        finally:
            lock.execute(text("SELECT RELEASE_LOCK(:key)"), {"key": key})


def main() -> int:
    parser = argparse.ArgumentParser(description="Explicit chat schema copy/rehearsal. Never in-place upgrade, reset, or production cutover.")
    commands = parser.add_subparsers(dest="command", required=True)
    sqlite = commands.add_parser("sqlite-copy")
    sqlite.add_argument("--source", type=Path, required=True)
    sqlite.add_argument("--destination", type=Path, required=True)
    sqlite.add_argument("--receipt", type=Path, required=True)
    sqlite.add_argument("--confirm-source-stopped", action="store_true")
    sqlite.add_argument("--rollback-rehearsal", action="store_true")
    mysql = commands.add_parser("mysql-copy")
    mysql.add_argument("--receipt", type=Path, required=True)
    mysql.add_argument("--anchor", type=Path, required=True)
    mysql.add_argument("--confirm-source-stopped", action="store_true")
    mysql.add_argument("--rollback-rehearsal", action="store_true")
    args = parser.parse_args()
    try:
        result = (sqlite_copy(args.source, args.destination, args.receipt, confirm_source_stopped=args.confirm_source_stopped,
                              rollback=args.rollback_rehearsal) if args.command == "sqlite-copy" else
                  mysql_copy(current_mysql_engine(), engine_from_url(CopySettings().trippilot_chat_copy_target_url.get_secret_value()),
                             args.receipt, anchor=args.anchor, confirm_source_stopped=args.confirm_source_stopped, rollback=args.rollback_rehearsal))
        print(json.dumps(result, sort_keys=True))
        return 0
    except (Refused, StorageError, SQLAlchemyError, OSError, ValueError, TypeError):
        print("Chat storage copy stopped. Preserve source, target and private receipt; no automatic retry, migration or quota reset.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
