#!/usr/bin/env python3
"""Stopped-writer SQLite backup and isolated recovery rehearsal; never a live restore.

Run with Python -B. Backups contain private archives, password hashes and session
material: root-only local artifacts, not public evidence. No network or app import.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
from contextlib import closing
from datetime import datetime, timezone
from typing import Callable

LIVE = Path("/var/lib/trippilot")
BACKUPS = Path("/var/backups/trippilot")
FILES = ("identity.sqlite3", "usage.sqlite3", "usage.sqlite3.identity")
VERSION = "trippilot-stopped-backup-v1"
BACKEND_CONFIG = Path("/etc/trippilot/backend.env")
NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}")
IDENTIFIER = re.compile(r"[0-9a-f]{32}\n")
IDENTITY_COLUMNS = {
    "storage_metadata": {"key", "value"},
    "accounts": {"id", "username", "password_hash", "disabled", "created_at"},
    "auth_sessions": {"id", "token_hash", "user_id", "csrf_token", "expires_at", "created_at"},
    "login_buckets": {"id", "window_start", "attempts"},
    "saved_trips": {"id", "owner_id", "operation_id", "name", "saved_at", "schema_version", "start_date", "end_date", "transport_mode", "status", "snapshot_json"},
}
USAGE_COLUMNS = {
    "usage_metadata": {"key", "value"},
    "usage_attempts": {"id", "owner_id", "service", "created_at", "state", "dispatched_at"},
}
USAGE_SQL = {
    ("table", "usage_metadata"): "CREATE TABLE usage_metadata (key TEXT PRIMARY KEY NOT NULL, value TEXT NOT NULL)",
    ("table", "usage_attempts"): "CREATE TABLE usage_attempts (id TEXT PRIMARY KEY NOT NULL CHECK(length(id)=32), owner_id TEXT NOT NULL CHECK(length(owner_id)=32), service TEXT NOT NULL CHECK(service IN ('amap_poi','amap_route','amap_weather','amap_proxy','qwen_text','qwen_vision','media_photo','media_video')), created_at INTEGER NOT NULL CHECK(created_at>=0), state TEXT NOT NULL CHECK(state IN ('reserved','dispatching','received','failed','cancelled')), dispatched_at INTEGER CHECK(dispatched_at>=0))",
    ("index", "usage_created"): "CREATE INDEX usage_created ON usage_attempts (created_at)",
    ("index", "usage_owner_created"): "CREATE INDEX usage_owner_created ON usage_attempts (owner_id, created_at)",
    ("index", "usage_service_created"): "CREATE INDEX usage_service_created ON usage_attempts (service, created_at)",
    ("index", "usage_owner_service_created"): "CREATE INDEX usage_owner_service_created ON usage_attempts (owner_id, service, created_at)",
}


class Refused(Exception):
    """Only fixed safe reason codes reach the CLI; never raw database errors."""


def no_symlinks(path: Path) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise Refused("unsafe_path")
    for part in (path, *path.parents):
        if part.is_symlink():
            raise Refused("symlink_refused")


def private_file(path: Path) -> None:
    no_symlinks(path)
    info = path.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) & 0o077):
        raise Refused("file_permissions_or_type")


def private_directory(path: Path) -> None:
    no_symlinks(path)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
        raise Refused("directory_not_private")


def create_directory(path: Path) -> None:
    no_symlinks(path)
    private_directory(path.parent)
    path.mkdir(mode=0o700, exist_ok=False)


def write_new(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


def sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def rows_sha256(db: sqlite3.Connection, tables: dict[str, set[str]]) -> str:
    """Stream a deterministic logical digest; never export row contents."""
    digest = hashlib.sha256()
    for table, columns in sorted(tables.items()):
        ordered = sorted(columns)
        digest.update((table + "\n").encode("ascii"))
        names = ",".join(ordered)
        for row in db.execute(f"SELECT {names} FROM {table} ORDER BY {names}"):
            digest.update(json.dumps(row, ensure_ascii=True, separators=(",", ":")).encode("ascii") + b"\n")
    return digest.hexdigest()


def readonly(path: Path) -> sqlite3.Connection:
    private_file(path)
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = path.with_name(path.name + suffix)
        no_symlinks(sidecar)
        if sidecar.exists():
            private_file(sidecar)
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3)
    connection.execute("PRAGMA trusted_schema=OFF")
    return connection


def identity_constraints(db: sqlite3.Connection) -> None:
    """Read-only ownership/session constraint checks, not create_all/migration."""
    primary = {"storage_metadata": "key", "accounts": "id", "auth_sessions": "id", "login_buckets": "id", "saved_trips": "id"}
    expected_unique = {"storage_metadata": set(), "accounts": {("username",)}, "auth_sessions": {("token_hash",)}, "login_buckets": set(), "saved_trips": {("owner_id", "operation_id")}}
    expected_foreign = {"storage_metadata": set(), "accounts": set(), "login_buckets": set(),
                        "auth_sessions": {("user_id", "accounts", "id")}, "saved_trips": {("owner_id", "accounts", "id")}}
    for table in IDENTITY_COLUMNS:
        columns = db.execute(f"PRAGMA table_info({table})").fetchall()
        if ([column[1] for column in columns if column[5]] != [primary[table]]
                or any(bool(column[3]) != (table != "saved_trips" or column[1] != "owner_id") for column in columns)):
            raise Refused("identity_constraints")
        unique: set[tuple[str, ...]] = set()
        for item in db.execute(f"PRAGMA index_list({table})"):
            if item[2] and item[3] != "pk":
                # Names originate from SQLite, but remain strictly quoted rather
                # than interpolated as instructions or an unescaped identifier.
                escaped = item[1].replace('"', '""')
                if item[4]:
                    raise Refused("identity_constraints")
                unique.add(tuple(row[2] for row in db.execute(f'PRAGMA index_info("{escaped}")')))
        foreign = {(item[3], item[2], item[4]) for item in db.execute(f"PRAGMA foreign_key_list({table})")}
        if unique != expected_unique[table] or foreign != expected_foreign[table]:
            raise Refused("identity_constraints")


def inspect_database(path: Path, expected: dict[str, set[str]]) -> dict[str, object]:
    with closing(readonly(path)) as db:
        if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise Refused("database_integrity")
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if tables != set(expected):
            raise Refused("database_schema")
        for table, columns in expected.items():
            # All identifiers originate in the constant allowlist above.
            if {row[1] for row in db.execute(f"PRAGMA table_info({table})")} != columns:
                raise Refused("database_schema")
        if db.execute("PRAGMA foreign_key_check").fetchall():
            raise Refused("database_foreign_keys")
        if expected == IDENTITY_COLUMNS:
            if dict(db.execute("SELECT key, value FROM storage_metadata")) != {"version": "identity-v1"}:
                raise Refused("identity_version")
            if db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type IN ('trigger','view')").fetchone()[0]:
                raise Refused("identity_schema")
            identity_constraints(db)
            return {"storage_version": "identity-v1", "sessions": db.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0],
                    "contents_sha256": rows_sha256(db, IDENTITY_COLUMNS),
                    "non_session_sha256": rows_sha256(db, {name: cols for name, cols in IDENTITY_COLUMNS.items() if name != "auth_sessions"})}
        objects = {(kind, name): sql for kind, name, sql in db.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")}
        if objects != USAGE_SQL:
            raise Refused("usage_schema")
        metadata = dict(db.execute("SELECT key, value FROM usage_metadata"))
        if (set(metadata) != {"version", "identity"} or metadata["version"] != "business-usage-v1"
                or re.fullmatch(r"[0-9a-f]{32}", metadata["identity"]) is None
                or db.execute("PRAGMA user_version").fetchone() != (1,)):
            raise Refused("usage_version")
        states = {"reserved", "dispatching", "received", "failed", "cancelled"}
        services = {"amap_poi", "amap_route", "amap_weather", "amap_proxy", "qwen_text", "qwen_vision", "media_photo", "media_video"}
        rows = db.execute("SELECT id, owner_id, service, created_at, state, dispatched_at FROM usage_attempts ORDER BY id")
        if any(re.fullmatch(r"[0-9a-f]{32}", row[0]) is None
               or re.fullmatch(r"[0-9a-f]{32}", row[1]) is None
               or row[2] not in services or type(row[3]) is not int or row[3] < 0
               or row[4] not in states or row[5] is not None and (type(row[5]) is not int or row[5] < 0)
               for row in rows):
            raise Refused("usage_data")
        count, max_created, max_dispatched = db.execute("SELECT COUNT(*), MAX(created_at), MAX(dispatched_at) FROM usage_attempts").fetchone()
        return {"storage_version": metadata["version"], "ledger_identity": metadata["identity"],
                "attempt_count": count, "max_created_at": max_created,
                "max_dispatched_at": max_dispatched,
                "attempts_sha256": rows_sha256(db, USAGE_COLUMNS)}


def inspect_pair(directory: Path) -> dict[str, object]:
    private_directory(directory)
    identity = inspect_database(directory / FILES[0], IDENTITY_COLUMNS)
    usage = inspect_database(directory / FILES[1], USAGE_COLUMNS)
    anchor = directory / FILES[2]
    private_file(anchor)
    if anchor.stat().st_size != 33:
        raise Refused("usage_anchor")
    text = anchor.read_text(encoding="ascii")
    if IDENTIFIER.fullmatch(text) is None or text[:-1] != usage["ledger_identity"]:
        raise Refused("usage_anchor")
    return {"identity": identity, "usage": usage}


def sqlite_copy(source: Path, destination: Path) -> None:
    write_new(destination, b"")
    with closing(readonly(source)) as original, closing(sqlite3.connect(destination)) as copied:
        original.backup(copied)
        # Make the new artifact self-contained even when the source uses WAL.
        # Only this just-created copy is changed; never checkpoint the live source.
        if copied.execute("PRAGMA journal_mode=DELETE").fetchone() != ("delete",):
            raise Refused("backup_journal_mode")
    with destination.open("rb") as output:
        os.fsync(output.fileno())


def backup_pair(source: Path, destination: Path, *, ensure_stopped: Callable[[], None]) -> None:
    """Caller supplies a stopped-writer check; CLI never permits arbitrary sources."""
    ensure_stopped()
    before = inspect_pair(source)
    if source == destination or source in destination.parents or destination in source.parents:
        raise Refused("overlapping_paths")
    create_directory(destination)
    # A failed partial directory stays private for investigation; never overwrite or
    # delete either it or an older backup on a later invocation.
    for name in FILES[:2]:
        sqlite_copy(source / name, destination / name)
    write_new(destination / FILES[2], (source / FILES[2]).read_bytes())
    ensure_stopped()
    after = inspect_pair(source)
    copied = inspect_pair(destination)
    if before != after or copied != before:
        raise Refused("source_changed_or_copy_mismatch")
    manifest = {"version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
                "consistency": "backend-stopped-and-operator-confirms-no-other-writers",
                "files": {name: sha256(destination / name) for name in FILES},
                "summary": copied,
                "sensitive": "contains private archives, password hashes and sessions; root-only; never publish",
                "restore_policy": "isolated drill only; never replace live data or roll quotas back"}
    write_new(destination / "manifest.json", json.dumps(manifest, ensure_ascii=True, indent=2).encode("ascii") + b"\n")
    sync_directory(destination)
    sync_directory(destination.parent)


def verify_backup(source: Path) -> dict[str, object]:
    private_directory(source)
    manifest_path = source / "manifest.json"
    private_file(manifest_path)
    if manifest_path.stat().st_size > 16 * 1024:
        raise Refused("manifest_size")
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise Refused("manifest_duplicate_field")
            result[key] = value
        return result
    manifest = json.loads(manifest_path.read_bytes(), object_pairs_hook=unique)
    if (not isinstance(manifest, dict) or manifest.get("version") != VERSION
            or set(manifest.get("files", {})) != set(FILES)
            or set(manifest) != {"version", "created_at", "consistency", "files", "summary", "sensitive", "restore_policy"}):
        raise Refused("manifest_version_or_fields")
    for name in FILES:
        private_file(source / name)
        if manifest["files"][name] != sha256(source / name):
            raise Refused("backup_hash")
    if inspect_pair(source) != manifest["summary"]:
        raise Refused("backup_summary")
    return manifest


def restore_drill(source: Path, destination: Path) -> None:
    """A new offline copy, never a production replacement or allowance rollback."""
    manifest = verify_backup(source)
    no_symlinks(destination)
    if (destination == LIVE or LIVE in destination.parents or source == destination
            or source in destination.parents or destination in source.parents):
        raise Refused("restore_destination_unsafe")
    create_directory(destination)
    for name in FILES:
        descriptor = os.open(destination / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with (source / name).open("rb") as original, os.fdopen(descriptor, "wb") as copied:
            shutil.copyfileobj(original, copied, 1024 * 1024)
            copied.flush()
            os.fsync(copied.fileno())
    # Preserve account/archive/login throttle state, but never resurrect authenticated
    # sessions. Pre-login CSRF is only an anonymous browser cookie, not stored here.
    with closing(sqlite3.connect(destination / FILES[0])) as db:
        db.execute("DELETE FROM auth_sessions")
        db.commit()
    current = inspect_pair(destination)
    if (current["identity"]["sessions"] != 0 or current["usage"] != manifest["summary"]["usage"]
            or current["identity"]["non_session_sha256"] != manifest["summary"]["identity"]["non_session_sha256"]):
        raise Refused("restore_validation")
    if any(sha256(destination / name) != manifest["files"][name] for name in FILES[1:]):
        raise Refused("restore_usage_changed")
    receipt = {"version": "trippilot-isolated-restore-drill-v1", "backup_manifest_sha256": sha256(source / "manifest.json"),
               "sessions_removed": manifest["summary"]["identity"]["sessions"],
               "usage_preserved": True, "production_replacement_allowed": False,
               "warning": "Offline rehearsal only. Never start paid dispatch using an older ledger; reconcile with current attempts first."}
    write_new(destination / "drill.json", json.dumps(receipt, indent=2).encode("ascii") + b"\n")
    sync_directory(destination)
    sync_directory(destination.parent)


def systemd_stopped() -> None:
    result = subprocess.run(["/usr/bin/systemctl", "show", "trippilot-api.service", "--property=LoadState", "--property=ActiveState", "--property=SubState"],
                            capture_output=True, timeout=10, check=False, text=True)
    properties = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if (result.returncode != 0 or properties.get("LoadState") != "loaded"
            or properties.get("ActiveState") != "inactive" or properties.get("SubState") != "dead"):
        raise Refused("backend_must_be_stopped")


def production_root() -> None:
    if os.geteuid() != 0:
        raise Refused("root_required")
    private_directory(BACKUPS)
    if BACKUPS.stat().st_uid != 0:
        raise Refused("backup_root_owner")


def require_sqlite_backend(path: Path = BACKEND_CONFIG) -> None:
    """Never infer active storage from sudo's inherited environment or old files.

    Read the protected service configuration before *creating* a SQLite backup.
    Historical manifest verification/isolated drills remain available and do not
    claim to back up an active MySQL deployment. Missing/invalid config stops.
    """
    from config import BACKEND, parse, validate_mysql
    private_file(path)
    info = path.stat()
    if (info.st_uid != 0 or info.st_gid != 0 or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size > 16384):
        raise Refused("root_backend_configuration_required")
    values = parse(path.read_text(), BACKEND)
    if validate_mysql(values):
        raise Refused("active_mysql_requires_mysql_backup_tool")
    if (values["TRIPPILOT_DATABASE_PATH"] != str(LIVE / "identity.sqlite3")
            or values["BUSINESS_USAGE_PATH"] != str(LIVE / "usage.sqlite3")):
        raise Refused("sqlite_backend_paths_mismatch")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("backup", "verify", "restore-drill"))
    parser.add_argument("--batch", required=True, help="Existing/new batch name under /var/backups/trippilot")
    parser.add_argument("--confirm-no-other-writers", action="store_true", help="Required for backup: operator stopped backend and all CLI/other writers")
    parser.add_argument("--drill", help="New isolated directory name under /var/backups/trippilot/restore-drills")
    arguments = parser.parse_args()
    try:
        production_root()
        if NAME.fullmatch(arguments.batch) is None or arguments.batch in {"restore-drills", ".", ".."}:
            raise Refused("invalid_batch")
        batch = BACKUPS / arguments.batch
        if arguments.command == "backup":
            if not arguments.confirm_no_other_writers or arguments.drill:
                raise Refused("explicit_stopped_writer_confirmation_required")
            require_sqlite_backend()
            backup_pair(LIVE, batch, ensure_stopped=systemd_stopped)
        elif arguments.command == "verify":
            if arguments.drill:
                raise Refused("unexpected_drill")
            verify_backup(batch)
        else:
            if not arguments.drill or NAME.fullmatch(arguments.drill) is None or arguments.drill in {".", ".."}:
                raise Refused("invalid_drill")
            parent = BACKUPS / "restore-drills"
            if not parent.exists():
                create_directory(parent)
            private_directory(parent)
            restore_drill(batch, parent / arguments.drill)
    except Refused as error:
        print(f"Operation refused ({error}); investigate before retrying. Existing files were not reset or overwritten.")
        return 1
    except (OSError, ValueError, TypeError, sqlite3.Error, subprocess.SubprocessError):
        print("Operation refused (io_schema_or_systemd_failure); investigate permissions, integrity and service state. Existing files were not reset or overwritten.")
        return 1
    print("Protected backup operation completed. Recovery copies are offline drills, not approved live replacements.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
