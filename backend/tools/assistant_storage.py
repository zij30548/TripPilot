"""Explicit isolated MySQL copy adding/preserving assistant-storage-v1.

This command does not alter the source, change runtime configuration, reset
quotas or restore an old allowance. MySQL DDL is not rollback-safe. Stop writers,
retain the original identity anchor, and preserve failed targets/receipts.
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
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import SQLModel

from core.business_usage import UsageError
from memory.chat_models import CHAT_VERSION
from memory.chat_places_models import CHAT_PLACES_VERSION
from memory.chat_places_storage import ChatPlacesStore, _checked, chat_places_tables, validate_chat_places_extension
from memory.chat_storage import _decoded, chat_tables, validate_chat_extension
from memory.mysql_storage import (MYSQL_STORAGE_VERSION, MySQLSettings, current_mysql_engine, engine_from_url,
                               initialize_private_schema, private_tables, validate_private_schema)
from memory.mysql_usage import initialize_usage_schema, usage_attempts, usage_metadata, validate_usage_schema
from memory.storage import StorageError
from memory.assistant_models import ASSISTANT_TABLES, ASSISTANT_VERSION
from memory.assistant_store import AssistantStore, assistant_tables, validate_assistant_extension
from tools.chat_places_storage import _chat_row_valid, _frozen_source_valid
from tools.chat_storage import Refused, _anchor_identity, _hash, _new_receipt, _rows_digest, _write


class CopySettings(MySQLSettings):
    trippilot_assistant_copy_target_url: SecretStr = SecretStr("")


def provision_assistant_extension(engine: Engine) -> None:
    """Explicit helper for a NEW isolated/test extension; never runtime startup."""
    with engine.connect() as db:
        validate_chat_extension(db)
        if (ASSISTANT_TABLES & set(inspect(db).get_table_names())
                or db.execute(text("SELECT value FROM storage_metadata WHERE `key`='assistant_version'")).scalar() is not None):
            raise Refused("assistant_extension_already_present")
    SQLModel.metadata.create_all(engine, tables=assistant_tables(), checkfirst=False)
    with engine.begin() as db:
        db.execute(text("INSERT INTO storage_metadata (`key`,value) VALUES ('assistant_version',:version)"), {"version": ASSISTANT_VERSION})
        validate_assistant_extension(db)


def _snapshot(db, tables: dict, order: tuple[str, ...]) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    total_rows = total_bytes = 0
    for name in order:
        result[name] = []
        for row in db.execution_options(stream_results=True).execute(select(tables[name]).order_by(*tables[name].primary_key.columns)).mappings():
            item = dict(row)
            total_rows += 1
            total_bytes += len(json.dumps(item, ensure_ascii=True, sort_keys=True, allow_nan=False).encode())
            if total_rows > 100_000 or total_bytes > 256 * 1024 * 1024:
                raise Refused("copy_source_too_large")
            result[name].append(item)
    return result


def _validate_source(db, frozen: dict, *, assistant_present: bool) -> None:
    _frozen_source_valid(db, frozen)
    owners = {row["id"] for row in frozen["accounts"]}
    conversations = {row["id"]: row for row in frozen["chat_conversations"]}
    places = ChatPlacesStore(db.engine)
    for row in frozen["chat_places_workspaces"]:
        _chat_row_valid("chat_places_workspaces", row)
        conversation = conversations.get(row["id"])
        if conversation is None or row["owner_id"] != conversation["owner_id"]:
            raise Refused("invalid_source_place_owner")
        places._view(db, row["owner_id"], conversation, row)
    for row in frozen["chat_places_operations"]:
        _chat_row_valid("chat_places_operations", row)
        conversation = conversations.get(row["conversation_id"])
        if (conversation is None or row["owner_id"] != conversation["owner_id"]
                or row["state"] not in {"running", "succeeded", "failed", "cancelled", "uncertain"}
                or row["kind"] not in {"sync", "set_city", "search", "ask", "confirm", "remove"}
                or re.fullmatch(r"[0-9a-f]{64}", row["input_hash"]) is None
                or not 0 <= row["intent_revision"] <= conversation["revision"]):
            raise Refused("invalid_source_place_receipt")
        if row["result_json"] is not None:
            result = _checked(_decoded(row["result_json"]))
            if result["conversation_id"] != row["conversation_id"] or result["intent_revision"] != row["intent_revision"]:
                raise Refused("invalid_source_place_receipt")
    if assistant_present:
        for row in frozen["assistant_tasks"]:
            conversation = conversations.get(row["conversation_id"])
            if conversation is None or row["owner_id"] != conversation["owner_id"]:
                raise Refused("invalid_source_assistant_owner")
            AssistantStore._view(row, conversation)
        for row in frozen["assistant_preferences"]:
            if row["owner_id"] not in owners:
                raise Refused("invalid_source_assistant_owner")
            AssistantStore._preference_view(row)


def _copy_locked(source: Engine, target: Engine, receipt: Path, anchor: Path, *, confirm_source_stopped: bool) -> dict:
    if (not confirm_source_stopped or source.dialect.name != "mysql" or target.dialect.name != "mysql"
            or source.url == target.url or re.fullmatch(r"trippilot_(?:chat|test)_[0-9a-f]{32}", target.url.database or "") is None):
        raise Refused("isolated_mysql_copy_required")
    validate_private_schema(source)
    identity, anchor_hash = _anchor_identity(anchor), _hash(anchor)
    with source.connect() as db:
        validate_chat_extension(db)
        validate_chat_places_extension(db)
        present = validate_assistant_extension(db, required=False)
    with target.connect() as db:
        if inspect(db).get_table_names():
            raise Refused("mysql_target_not_empty")
    fd = _new_receipt(receipt)
    record = {"version": "assistant-copy-receipt-v1", "state": "started", "assistant_version": ASSISTANT_VERSION,
              "mode": "copy-existing" if present else "upgrade-copy", "production_cutover": False,
              "quota_refunded": False, "anchor_sha256": anchor_hash, "rows": {}}
    try:
        _write(fd, record)
        tables = {table.name: table for table in [*private_tables(), *chat_tables(), *chat_places_tables(), *assistant_tables()]}
        tables.update(usage_metadata=usage_metadata, usage_attempts=usage_attempts)
        base_order = ("storage_metadata", "accounts", "auth_sessions", "login_buckets", "saved_trips", "invitations",
                      "usage_metadata", "usage_attempts", "chat_conversations", "chat_turns", "chat_places_workspaces", "chat_places_operations")
        order = base_order + (("assistant_tasks", "assistant_preferences") if present else ())
        with source.connect().execution_options(isolation_level="REPEATABLE READ") as original, original.begin():
            original.exec_driver_sql("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            validate_usage_schema(original, identity)
            frozen = _snapshot(original, tables, order)
            metadata = {row["key"]: row["value"] for row in frozen["storage_metadata"]}
            expected = {"version": MYSQL_STORAGE_VERSION, "chat_version": CHAT_VERSION, "chat_places_version": CHAT_PLACES_VERSION}
            if present:
                expected["assistant_version"] = ASSISTANT_VERSION
            if metadata != expected:
                raise Refused("unexpected_source_metadata")
            _validate_source(original, frozen, assistant_present=present)
            record["rows"] = {name: {"count": len(rows), "sha256": _rows_digest(rows)} for name, rows in frozen.items()}
            _write(fd, record)
            # Every source value/reference is validated before the first DDL.
            initialize_private_schema(target, ready=False)
            initialize_usage_schema(target, identity)
            SQLModel.metadata.create_all(target, tables=[*chat_tables(), *chat_places_tables(), *assistant_tables()], checkfirst=False)
            with target.begin() as destination:
                for name in order:
                    if name in {"storage_metadata", "usage_metadata"}:
                        continue
                    if frozen[name]:
                        destination.execute(tables[name].insert(), frozen[name])
                    copied = [dict(row) for row in destination.execute(select(tables[name]).order_by(*tables[name].primary_key.columns)).mappings()]
                    if copied != frozen[name]:
                        raise Refused("copy_verification_failed")
                ready = {**metadata, "assistant_version": ASSISTANT_VERSION}
                destination.execute(text("INSERT INTO storage_metadata (`key`,value) VALUES (:key,:value)"),
                                    [{"key": key, "value": value} for key, value in ready.items()])
        validate_private_schema(target)
        with target.connect() as db:
            validate_usage_schema(db, identity)
            validate_assistant_extension(db)
        # Fresh snapshot is additional detection, not proof that writers stopped.
        with source.connect().execution_options(isolation_level="REPEATABLE READ") as fresh, fresh.begin():
            fresh.exec_driver_sql("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
            if _snapshot(fresh, tables, order) != frozen:
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


def mysql_copy(source: Engine, target: Engine, receipt: Path, *, anchor: Path, confirm_source_stopped: bool) -> dict:
    if (target.dialect.name != "mysql" or re.fullmatch(r"trippilot_(?:chat|test)_[0-9a-f]{32}", target.url.database or "") is None):
        raise Refused("isolated_mysql_copy_required")
    key = "assistant_copy_" + hashlib.sha256((target.url.database or "").encode()).hexdigest()[:32]
    with target.connect() as lock:
        if lock.execute(text("SELECT GET_LOCK(:key,0)"), {"key": key}).scalar() != 1:
            raise Refused("copy_already_running")
        try:
            return _copy_locked(source, target, receipt, anchor, confirm_source_stopped=confirm_source_stopped)
        finally:
            lock.execute(text("SELECT RELEASE_LOCK(:key)"), {"key": key})


def main() -> int:
    parser = argparse.ArgumentParser(description="Copy stopped private MySQL into a NEW isolated assistant target; never in-place.")
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--anchor", type=Path, required=True)
    parser.add_argument("--confirm-source-stopped", action="store_true")
    args = parser.parse_args()
    try:
        result = mysql_copy(current_mysql_engine(), engine_from_url(CopySettings().trippilot_assistant_copy_target_url.get_secret_value()),
                            args.receipt, anchor=args.anchor, confirm_source_stopped=args.confirm_source_stopped)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (Refused, StorageError, UsageError, SQLAlchemyError, OSError, ValueError, TypeError):
        print("Assistant copy stopped. Preserve source, target and private receipt; no retry, cutover or quota reset.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
