"""Explicit empty-target chat POI extension copy, never an in-place upgrade.

The source, original usage anchor, sessions and every quota receipt are retained.
This is migration rehearsal/preparation, not production cutover or a backup of
later chat-places writes. DDL/uncertain failures preserve the target and receipt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re

from pydantic import SecretStr
from sqlalchemy import inspect, select, text
from sqlalchemy import Integer
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import SQLModel

from app.chat_models import CHAT_TABLES, CHAT_VERSION
from app.chat_places_models import CHAT_PLACES_TABLES, CHAT_PLACES_VERSION
from app.chat_places_storage import chat_places_tables, validate_chat_places_extension
from app.chat_storage import ChatStore, chat_tables, validate_chat_extension
from app.business_usage import UsageError
from app.mysql_storage import (MYSQL_STORAGE_VERSION, MySQLSettings, current_mysql_engine, engine_from_url,
                               initialize_private_schema, private_tables, validate_private_schema)
from app.mysql_usage import initialize_usage_schema, usage_attempts, usage_metadata, validate_usage_schema
from app.storage import StorageError
from tools.chat_storage import Refused, _anchor_identity, _hash, _new_receipt, _rows_digest, _write
from tools.mysql_backup import TABLES as BASE_TABLES, _valid_row as validate_base_row

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")


def _chat_row_valid(name: str, row: dict) -> None:
    """Validate every historical receipt, not only the latest visible turn."""
    model = SQLModel.metadata.tables[name]
    if set(row) != set(model.c.keys()):
        raise Refused("invalid_source_chat_fields")
    for column in model.c:
        value = row[column.name]
        if value is None:
            if not column.nullable:
                raise Refused("invalid_source_chat_null")
        elif isinstance(column.type, Integer):
            if type(value) is not int or not 0 <= value <= 2**63 - 1:
                raise Refused("invalid_source_chat_number")
        elif (not isinstance(value, str) or getattr(column.type, "length", None) is not None and len(value) > column.type.length
              or len(value.encode("utf-8")) > 256 * 1024):
            raise Refused("invalid_source_chat_text")
    for key in ("id", "conversation_id", "operation_id", "create_operation_id", "confirm_operation_id", "active_turn_id"):
        if key in row and row[key] is not None and _UUID.fullmatch(row[key]) is None:
            raise Refused("invalid_source_chat_identity")
    if re.fullmatch(r"[0-9a-f]{32}", row["owner_id"]) is None:
        raise Refused("invalid_source_chat_owner")


def _frozen_source_valid(original, frozen: dict) -> None:
    # Reuse the existing row validators: this includes Argon2id/session/bucket
    # validation, full saved-trip-v1/v2/v3 replay, invitations and usage states.
    for name in BASE_TABLES:
        for row in frozen[name]:
            try:
                validate_base_row(name, row)
            except Exception:
                # Only a fixed table name survives; no snapshot, hash, token,
                # validation input or original exception becomes diagnostic text.
                raise Refused("invalid_source_" + name) from None
    owners = {row["id"] for row in frozen["accounts"]}
    for name, field in (("auth_sessions", "user_id"), ("saved_trips", "owner_id"), ("invitations", "consumed_by")):
        if any(row[field] is not None and row[field] not in owners for row in frozen[name]):
            raise Refused("invalid_source_owner_reference")
    # Usage rows deliberately have no account FK: valid historical/quarantined
    # owners must not lose attempts or receive new allowances during a copy.
    conversations = {row["id"]: row for row in frozen["chat_conversations"]}
    for row in conversations.values():
        _chat_row_valid("chat_conversations", row)
        if row["owner_id"] not in owners:
            raise Refused("invalid_source_chat_owner")
        ChatStore._view(original, row)
    for row in frozen["chat_turns"]:
        _chat_row_valid("chat_turns", row)
        conversation = conversations.get(row["conversation_id"])
        if (conversation is None or row["owner_id"] != conversation["owner_id"]
                or row["state"] not in {"running", "succeeded", "failed", "cancelled", "uncertain"}
                or not 1 <= row["frozen_revision"] <= conversation["revision"]
                or row["state"] == "running" and conversation["active_turn_id"] != row["id"]
                or re.fullmatch(r"[0-9a-f]{64}", row["input_hash"]) is None):
            raise Refused("invalid_source_chat_receipt")


class CopySettings(MySQLSettings):
    trippilot_chat_places_copy_target_url: SecretStr = SecretStr("")


def provision_chat_places_extension(engine: Engine) -> None:
    """For explicit empty-extension tools/tests only; never called at startup."""
    with engine.connect() as db:
        validate_chat_extension(db)
        if (CHAT_PLACES_TABLES & set(inspect(db).get_table_names())
                or db.execute(text("SELECT value FROM storage_metadata WHERE `key`='chat_places_version'")).scalar() is not None):
            raise Refused("chat_places_extension_already_present")
    SQLModel.metadata.create_all(engine, tables=chat_places_tables(), checkfirst=False)
    with engine.begin() as db:
        db.execute(text("INSERT INTO storage_metadata (`key`,value) VALUES ('chat_places_version',:version)"),
                   {"version": CHAT_PLACES_VERSION})
        validate_chat_places_extension(db)


def _copy_locked(source: Engine, target: Engine, receipt: Path, anchor: Path, *, confirm_source_stopped: bool,
                 rollback: bool = False) -> dict:
    if (not confirm_source_stopped or source.dialect.name != "mysql" or target.dialect.name != "mysql"
            or source.url == target.url
            or re.fullmatch(r"trippilot_(?:chat|test)_[0-9a-f]{32}", target.url.database or "") is None):
        raise Refused("isolated_mysql_copy_required")
    validate_private_schema(source)
    identity, anchor_hash = _anchor_identity(anchor), _hash(anchor)
    with source.connect() as db:
        validate_chat_extension(db)
        if validate_chat_places_extension(db, required=False) != rollback:
            raise Refused("source_extension_version_mismatch")
        if rollback and any(db.execute(select(text("COUNT(*)")).select_from(table)).scalar_one() for table in chat_places_tables()):
            raise Refused("rollback_rehearsal_requires_empty_chat_places")
    with target.connect() as db:
        if inspect(db).get_table_names():
            raise Refused("mysql_target_not_empty")
    fd = _new_receipt(receipt)
    record = {"version": "chat-places-copy-receipt-v1", "state": "started",
              "mode": "rollback-rehearsal" if rollback else "upgrade-copy", "chat_version": CHAT_VERSION,
              "chat_places_version": CHAT_PLACES_VERSION, "production_cutover": False, "quota_refunded": False,
              "anchor_sha256": anchor_hash, "rows": {}}
    try:
        _write(fd, record)
        tables = {table.name: table for table in [*private_tables(), *chat_tables()]}
        tables.update(usage_metadata=usage_metadata, usage_attempts=usage_attempts)
        order = ("storage_metadata", "accounts", "auth_sessions", "login_buckets", "saved_trips", "invitations",
                 "usage_metadata", "usage_attempts", "chat_conversations", "chat_turns")
        with source.connect().execution_options(isolation_level="REPEATABLE READ") as original, original.begin():
            original.exec_driver_sql("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            actual_identity = original.execute(select(usage_metadata.c.value).where(usage_metadata.c.key == "identity")).scalar_one()
            if actual_identity != identity:
                raise Refused("usage_identity_anchor_mismatch")
            validate_usage_schema(original, identity)
            frozen, total_rows, total_bytes = {}, 0, 0
            for name in order:
                frozen[name] = []
                for row in original.execution_options(stream_results=True).execute(select(tables[name]).order_by(*tables[name].primary_key.columns)).mappings():
                    item = dict(row)
                    total_rows += 1
                    total_bytes += len(json.dumps(item, ensure_ascii=True, sort_keys=True, allow_nan=False).encode())
                    if total_rows > 100_000 or total_bytes > 256 * 1024 * 1024:
                        raise Refused("copy_source_too_large")
                    frozen[name].append(item)
            source_metadata = {row["key"]: row["value"] for row in frozen["storage_metadata"]}
            expected_metadata = {"version": MYSQL_STORAGE_VERSION, "chat_version": CHAT_VERSION}
            if rollback:
                expected_metadata["chat_places_version"] = CHAT_PLACES_VERSION
            if source_metadata != expected_metadata:
                raise Refused("unexpected_source_metadata")
            if rollback and any(original.execute(select(text("COUNT(*)")).select_from(table)).scalar_one() for table in chat_places_tables()):
                raise Refused("rollback_rehearsal_requires_empty_chat_places")
            _frozen_source_valid(original, frozen)
            record["rows"] = {name: {"count": len(rows), "sha256": _rows_digest(rows)} for name, rows in frozen.items()}
            _write(fd, record)
            initialize_private_schema(target, ready=False)
            initialize_usage_schema(target, identity)
            SQLModel.metadata.create_all(target, tables=chat_tables(), checkfirst=False)
            if not rollback:
                SQLModel.metadata.create_all(target, tables=chat_places_tables(), checkfirst=False)
            with target.begin() as destination:
                for name in order:
                    if name in {"storage_metadata", "usage_metadata"}:
                        continue
                    if frozen[name]:
                        destination.execute(tables[name].insert(), frozen[name])
                    copied = [dict(row) for row in destination.execute(select(tables[name]).order_by(*tables[name].primary_key.columns)).mappings()]
                    if copied != frozen[name]:
                        raise Refused("copy_verification_failed")
                metadata = {key: value for key, value in source_metadata.items() if key != "chat_places_version"}
                if not rollback:
                    metadata["chat_places_version"] = CHAT_PLACES_VERSION
                destination.execute(text("INSERT INTO storage_metadata (`key`,value) VALUES (:key,:value)"),
                                    [{"key": key, "value": value} for key, value in metadata.items()])
            validate_private_schema(target)
            with target.connect() as db:
                validate_usage_schema(db, identity)
                validate_chat_places_extension(db, required=not rollback)
        # Re-reading the same REPEATABLE READ transaction cannot detect a
        # second writer. Compare a fresh consistent snapshot after target commit.
        # Operators must still stop all writers; comparison is not a write lock
        # and cannot prove there was no change after this final observation.
        with source.connect().execution_options(isolation_level="REPEATABLE READ") as current, current.begin():
            current.exec_driver_sql("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            for name in order:
                rows = [dict(row) for row in current.execute(select(tables[name]).order_by(*tables[name].primary_key.columns)).mappings()]
                if rows != frozen[name]:
                    raise Refused("source_snapshot_changed")
            if rollback and any(current.execute(select(text("COUNT(*)")).select_from(table)).scalar_one() for table in chat_places_tables()):
                raise Refused("source_snapshot_changed")
        if _anchor_identity(anchor) != identity or _hash(anchor) != anchor_hash:
            raise Refused("usage_identity_anchor_changed")
        record.update(state="verified", source_unchanged=True, source_check="two_snapshots_requires_stopped_writers")
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
    if (target.dialect.name != "mysql"
            or re.fullmatch(r"trippilot_(?:chat|test)_[0-9a-f]{32}", target.url.database or "") is None):
        raise Refused("isolated_mysql_copy_required")
    lock_key = "chat_places_copy_" + hashlib.sha256((target.url.database or "").encode()).hexdigest()[:32]
    with target.connect() as lock:
        if lock.execute(text("SELECT GET_LOCK(:key,0)"), {"key": lock_key}).scalar() != 1:
            raise Refused("copy_already_running")
        try:
            return _copy_locked(source, target, receipt, anchor, confirm_source_stopped=confirm_source_stopped, rollback=rollback)
        finally:
            lock.execute(text("SELECT RELEASE_LOCK(:key)"), {"key": lock_key})


def main() -> int:
    parser = argparse.ArgumentParser(description="Explicit isolated MySQL chat POI extension copy. No in-place DDL, automatic restore or quota reset.")
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--anchor", type=Path, required=True)
    parser.add_argument("--confirm-source-stopped", action="store_true")
    parser.add_argument("--rollback-rehearsal", action="store_true")
    args = parser.parse_args()
    try:
        result = mysql_copy(current_mysql_engine(), engine_from_url(CopySettings().trippilot_chat_places_copy_target_url.get_secret_value()),
                            args.receipt, anchor=args.anchor, confirm_source_stopped=args.confirm_source_stopped, rollback=args.rollback_rehearsal)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (Refused, StorageError, UsageError, SQLAlchemyError, OSError, ValueError, TypeError):
        print("Chat POI copy stopped. Preserve source, target and private receipt; no retry, cutover or quota reset.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
