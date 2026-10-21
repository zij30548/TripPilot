"""Private consistent MySQL backup and isolated restore rehearsal, never reset.

JSONL contains password hashes, CSRF/session material and travel snapshots. It
is an administrator-only backup, NOT public evidence. A digest proves file
integrity, not authenticity, freshness or that restoring an older quota is safe.
No command performs in-place recovery, switches configuration or refunds usage.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import BinaryIO, Iterator

from pydantic import SecretStr
from sqlalchemy import Boolean, Integer, String, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import SQLModel

from core.business_usage import UsageError
from tooling.providers.qwen import strict_json
from memory.mysql_storage import (MYSQL_STORAGE_VERSION, MySQLSettings, current_mysql_engine,
                               engine_from_url, initialize_private_schema, private_tables,
                               validate_private_schema)
from memory.mysql_usage import (MYSQL_USAGE_VERSION, _checked_attempt, initialize_usage_schema,
                             usage_attempts, usage_metadata, validate_usage_schema)
from memory.storage import StorageError
from tools.mysql_migration import Refused, _source_rows_valid

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from deploy.backup import no_symlinks, private_directory, private_file, sync_directory

VERSION = "trippilot-mysql-private-backup-v1"
TABLES = ("storage_metadata", "accounts", "auth_sessions", "login_buckets", "saved_trips", "invitations",
          "usage_metadata", "usage_attempts")
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_LINE_BYTES = 16 * 1024 * 1024
MAX_ROWS = 100_000
ID = re.compile(r"[0-9a-f]{32}")
HASH = re.compile(r"[0-9a-f]{64}")


class BackupSettings(MySQLSettings):
    trippilot_mysql_restore_url: SecretStr = SecretStr("")


def tables() -> dict:
    return {**{table.name: table for table in private_tables()},
            "usage_metadata": usage_metadata, "usage_attempts": usage_attempts}


def _encoded(value: object) -> bytes:
    data = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii") + b"\n"
    if len(data) > MAX_LINE_BYTES:
        raise Refused("backup_row_too_large")
    return data


def _anchor(path: Path) -> str:
    private_file(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if info.st_size != 33 or info.st_nlink != 1:
            raise Refused("invalid_identity_anchor")
        value = source.read(34)
    if not re.fullmatch(rb"[0-9a-f]{32}\n", value):
        raise Refused("invalid_identity_anchor")
    return value[:-1].decode("ascii")


def _new_file(path: Path) -> int:
    no_symlinks(path)
    private_directory(path.parent)
    return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)


def _new_anchor(path: Path, identity: str) -> None:
    fd = _new_file(path)
    with os.fdopen(fd, "wb") as target:
        target.write((identity + "\n").encode("ascii"))
        target.flush()
        os.fsync(target.fileno())
    sync_directory(path.parent)


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for data in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(data)
    return digest.hexdigest()


def backup(engine: Engine, anchor_path: Path, output_path: Path) -> dict[str, object]:
    validate_private_schema(engine)
    # This legacy backup format has an exact eight-table manifest. Never emit a
    # seemingly complete backup which silently omits a newer chat extension.
    from memory.chat_models import CHAT_TABLES
    with engine.connect() as connection:
        if CHAT_TABLES & set(inspect(connection).get_table_names()):
            raise Refused("backup_chat_extension_requires_chat_copy_tool")
    identity = _anchor(anchor_path)
    counts = {name: 0 for name in TABLES}
    digest, size = hashlib.sha256(), 0
    fd = _new_file(output_path)
    # On failure an exclusive private partial file is retained, never silently
    # overwritten/reused. Without a valid footer restore validation rejects it.
    with os.fdopen(fd, "wb") as output:
        def write(value: object, *, hashed: bool = True) -> None:
            nonlocal size
            data = _encoded(value)
            size += len(data)
            if size > MAX_FILE_BYTES:
                raise Refused("backup_total_too_large")
            output.write(data)
            if hashed:
                digest.update(data)

        write({"kind": "header", "version": VERSION, "private_version": MYSQL_STORAGE_VERSION,
               "usage_version": MYSQL_USAGE_VERSION, "usage_identity": identity,
               "created_at": datetime.now(timezone.utc).isoformat(), "tables": list(TABLES),
               "consistency": "single_repeatable_read_snapshot", "sessions": "sensitive_original_session_material"})
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as db:
            with db.begin():
                db.exec_driver_sql("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
                validate_usage_schema(db, identity)
                if db.execute(text("SELECT value FROM storage_metadata WHERE `key`='version'")).scalar() != MYSQL_STORAGE_VERSION:
                    raise Refused("backup_version_changed")
                for name, table in ((name, tables()[name]) for name in TABLES):
                    result = db.execution_options(stream_results=True).execute(select(table).order_by(*table.primary_key.columns)).mappings()
                    for row in result:
                        counts[name] += 1
                        if sum(counts.values()) > MAX_ROWS:
                            raise Refused("backup_row_limit")
                        write({"kind": "row", "table": name, "value": dict(row)})
                if _anchor(anchor_path) != identity:
                    raise Refused("identity_anchor_changed")
        write({"kind": "footer", "rows": counts, "content_sha256": digest.hexdigest()}, hashed=False)
        output.flush()
        os.fsync(output.fileno())
    sync_directory(output_path.parent)
    return {"version": VERSION, "sha256": _file_hash(output_path), "rows": counts,
            "quota_freshness": "point_in_time_only_not_safe_to_refund", "sessions": "restore_drill_revokes_all"}


def _valid_row(name: str, row: object) -> dict[str, object]:
    model = tables()[name]
    if not isinstance(row, dict) or set(row) != set(model.c.keys()):
        raise Refused("backup_row_fields")
    for column in model.c:
        value = row[column.name]
        if value is None:
            if not column.nullable:
                raise Refused("backup_row_null")
        elif isinstance(column.type, Boolean):
            if type(value) is not bool:
                raise Refused("backup_row_type")
        elif isinstance(column.type, Integer):
            if type(value) is not int or not 0 <= value <= 2 ** 63 - 1:
                raise Refused("backup_row_type")
        elif isinstance(column.type, String):
            if not isinstance(value, str) or column.type.length is not None and len(value) > column.type.length:
                raise Refused("backup_row_type")
        else:
            # SQLModel AutoString is a TypeDecorator; Text may be a variant.
            if not isinstance(value, str) or getattr(column.type, "length", None) is not None and len(value) > column.type.length:
                raise Refused("backup_row_type")
    if name in {"accounts", "auth_sessions", "login_buckets", "saved_trips"}:
        _source_rows_valid(name, row)
    elif name == "invitations":
        if (ID.fullmatch(row["id"]) is None or HASH.fullmatch(row["token_hash"]) is None
                or row["expires_at"] <= row["created_at"]
                or (row["consumed_by"] is None) != (row["consumed_at"] is None)
                or row["consumed_by"] is not None and (ID.fullmatch(row["consumed_by"]) is None or row["consumed_at"] < row["created_at"])):
            raise Refused("backup_invitation_invalid")
    elif name == "usage_attempts":
        _checked_attempt(row)
    return row


def _lines(source: BinaryIO) -> Iterator[tuple[dict, bytes]]:
    total = 0
    while True:
        line = source.readline(MAX_LINE_BYTES + 1)
        if not line:
            return
        total += len(line)
        if total > MAX_FILE_BYTES or len(line) > MAX_LINE_BYTES or not line.endswith(b"\n"):
            raise Refused("backup_size_or_truncation")
        try:
            value = strict_json(line)
        except (ValueError, TypeError, RecursionError):
            raise Refused("backup_invalid_json") from None
        if not isinstance(value, dict):
            raise Refused("backup_invalid_structure")
        yield value, line


def inspect_backup(path: Path, expected_sha256: str) -> dict[str, object]:
    private_file(path)
    if HASH.fullmatch(expected_sha256) is None or path.stat().st_size > MAX_FILE_BYTES or _file_hash(path) != expected_sha256:
        raise Refused("backup_hash_or_size")
    counts = {name: 0 for name in TABLES}
    digest = hashlib.sha256()
    keys: dict[str, set] = {name: set() for name in TABLES}
    accounts: set[str] = set()
    uniques: dict[str, set] = {name: set() for name in TABLES}
    metadata: dict[str, dict] = {"storage_metadata": {}, "usage_metadata": {}}
    header, footer, previous_table = None, None, -1
    with path.open("rb") as source:
        for value, raw in _lines(source):
            if header is None:
                if (set(value) != {"kind", "version", "private_version", "usage_version", "usage_identity", "created_at", "tables", "consistency", "sessions"}
                        or value["kind"] != "header" or value["version"] != VERSION or value["private_version"] != MYSQL_STORAGE_VERSION
                        or value["usage_version"] != MYSQL_USAGE_VERSION or value["tables"] != list(TABLES)
                        or not isinstance(value["usage_identity"], str) or ID.fullmatch(value["usage_identity"]) is None
                        or value["consistency"] != "single_repeatable_read_snapshot" or value["sessions"] != "sensitive_original_session_material"):
                    raise Refused("backup_header")
                try:
                    if datetime.fromisoformat(value["created_at"]).utcoffset() is None:
                        raise ValueError()
                except (ValueError, TypeError):
                    raise Refused("backup_header_time") from None
                header = value
                digest.update(raw)
                continue
            if footer is not None:
                raise Refused("backup_trailing_content")
            if value.get("kind") == "footer":
                if (set(value) != {"kind", "rows", "content_sha256"} or value["rows"] != counts
                        or value["content_sha256"] != digest.hexdigest()):
                    raise Refused("backup_footer")
                footer = value
                continue
            name = value.get("table")
            if set(value) != {"kind", "table", "value"} or value["kind"] != "row" or name not in TABLES:
                raise Refused("backup_row_envelope")
            index = TABLES.index(name)
            if index < previous_table:
                raise Refused("backup_table_order")
            previous_table = index
            row = _valid_row(name, value["value"])
            key = row["key"] if name.endswith("metadata") else row["id"]
            if key in keys[name]:
                raise Refused("backup_duplicate_primary_key")
            keys[name].add(key)
            if name == "accounts":
                accounts.add(row["id"])
                unique = row["username"]
            elif name == "auth_sessions":
                if row["user_id"] not in accounts:
                    raise Refused("backup_foreign_key")
                unique = row["token_hash"]
            elif name == "saved_trips":
                if row["owner_id"] is not None and row["owner_id"] not in accounts:
                    raise Refused("backup_foreign_key")
                unique = (row["owner_id"], row["operation_id"]) if row["owner_id"] is not None else None
            elif name == "invitations":
                if row["consumed_by"] is not None and row["consumed_by"] not in accounts:
                    raise Refused("backup_foreign_key")
                unique = row["token_hash"]
            else:
                unique = None
            if unique is not None:
                if unique in uniques[name]:
                    raise Refused("backup_duplicate_unique_key")
                uniques[name].add(unique)
            if name.endswith("metadata"):
                metadata[name][row["key"]] = row["value"]
            counts[name] += 1
            if sum(counts.values()) > MAX_ROWS:
                raise Refused("backup_row_limit")
            digest.update(raw)
    if (header is None or footer is None or metadata["storage_metadata"] != {"version": MYSQL_STORAGE_VERSION}
            or metadata["usage_metadata"] != {"version": MYSQL_USAGE_VERSION, "identity": header["usage_identity"]}):
        raise Refused("backup_version_or_incomplete")
    return {"sha256": expected_sha256, "rows": counts, "usage_identity": header["usage_identity"]}


def _restore_drill(engine: Engine, backup_path: Path, anchor_output: Path, *, expected_sha256: str) -> dict[str, object]:
    original = inspect_backup(backup_path, expected_sha256)
    with engine.connect() as db:
        if inspect(db).get_table_names():
            raise Refused("restore_target_not_empty")
    _new_anchor(anchor_output, original["usage_identity"])
    initialize_private_schema(engine, ready=False)
    initialize_usage_schema(engine, original["usage_identity"])
    copied = {name: 0 for name in TABLES}
    # COMMIT failure remains uncertain and is never retried/erased. Anchor and
    # partial target are retained. The ready marker is part of the final DML.
    with engine.begin() as db, backup_path.open("rb") as source:
        reading_hash = hashlib.sha256()
        for value, raw in _lines(source):
            reading_hash.update(raw)
            if value.get("kind") != "row":
                continue
            if (set(value) != {"kind", "table", "value"} or value.get("table") not in TABLES):
                raise Refused("backup_changed")
            name, row = value["table"], _valid_row(value["table"], value["value"])
            if name in {"storage_metadata", "usage_metadata", "auth_sessions"}:
                continue
            db.execute(tables()[name].insert(), row)
            copied[name] += 1
        if reading_hash.hexdigest() != expected_sha256 or inspect_backup(backup_path, expected_sha256) != original:
            raise Refused("backup_changed")
        db.execute(text("INSERT INTO storage_metadata (`key`, value) VALUES ('version', :version)"),
                   {"version": MYSQL_STORAGE_VERSION})
    validate_private_schema(engine)
    with engine.connect() as db:
        validate_usage_schema(db, original["usage_identity"])
        if db.execute(text("SELECT count(*) FROM auth_sessions")).scalar_one() != 0:
            raise Refused("restore_sessions_not_revoked")
        for name in TABLES:
            desired = 0 if name == "auth_sessions" else original["rows"][name]
            if db.execute(select(text("count(*)")).select_from(tables()[name])).scalar_one() != desired:
                raise Refused("restore_count_mismatch")
    return {"result": "isolated_restore_drill_verified_not_production_recovery", "rows": original["rows"],
            "sessions_revoked": original["rows"]["auth_sessions"], "usage_attempts_preserved": copied["usage_attempts"],
            "quota_restore_clearance": False}


def restore_drill(engine: Engine, backup_path: Path, anchor_output: Path, *, expected_sha256: str) -> dict[str, object]:
    if engine.dialect.name != "mysql" or re.fullmatch(r"trippilot_restore_[0-9a-f]{32}", engine.url.database or "") is None:
        raise Refused("isolated_restore_database_required")
    # DDL auto-commits; use a dedicated advisory-lock connection to prevent two
    # operators provisioning the same supposedly empty rehearsal DB concurrently.
    key = "trippilot_restore_" + hashlib.sha256(engine.url.database.encode()).hexdigest()[:32]
    with engine.connect() as lock:
        if lock.execute(text("SELECT GET_LOCK(:key, 0)"), {"key": key}).scalar() != 1:
            raise Refused("restore_already_running")
        try:
            return _restore_drill(engine, backup_path, anchor_output, expected_sha256=expected_sha256)
        finally:
            lock.execute(text("SELECT RELEASE_LOCK(:key)"), {"key": key})


def main() -> int:
    parser = argparse.ArgumentParser(description="Private MySQL backup or isolated recovery rehearsal; never in-place restore.")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("backup")
    create.add_argument("--anchor", required=True, type=Path)
    create.add_argument("--output", required=True, type=Path)
    restore = commands.add_parser("restore-drill")
    restore.add_argument("--input", required=True, type=Path)
    restore.add_argument("--expected-sha256", required=True)
    restore.add_argument("--anchor-output", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = (backup(current_mysql_engine(), args.anchor, args.output) if args.command == "backup"
                  else restore_drill(engine_from_url(BackupSettings().trippilot_mysql_restore_url.get_secret_value()),
                                     args.input, args.anchor_output, expected_sha256=args.expected_sha256))
        print(json.dumps(result, sort_keys=True))
        return 0
    except (StorageError, UsageError, Refused, SQLAlchemyError, OSError, ValueError, TypeError, RecursionError):
        print("MySQL backup/rehearsal stopped. Preserve private artifacts and target; no automatic retry, reset or configuration change.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
