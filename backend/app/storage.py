"""Private SQLite storage and explicit copy-only legacy migration.

The pre-login local database is never opened for writing by the application.
Migration works on a consistent SQLite backup, not on the user's source file.
"""
import os
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Annotated

from fastapi import Depends
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import event, inspect, text
from sqlalchemy.engine import Connection, Engine, URL
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Field, Session, SQLModel, create_engine
from app.config import BACKEND_ENV_FILE

LEGACY_PATH = Path(__file__).resolve().parents[1] / "data" / "saved-trips.sqlite3"
DEFAULT_PATH = Path(__file__).resolve().parents[1] / "data" / "trippilot-private.sqlite3"
STORAGE_VERSION = "identity-v1"
_initialization_lock = Lock()


class StorageError(Exception):
    def __init__(self, code: str, status: int = 503):
        self.code, self.status = code, status
        super().__init__(code)


class StorageMetadata(SQLModel, table=True):
    __tablename__ = "storage_metadata"
    __table_args__ = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_bin"}
    key: str = Field(primary_key=True, max_length=64)
    value: str = Field(max_length=256)


class StorageSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_ENV_FILE, env_file_encoding="utf-8", extra="ignore")
    trippilot_database_path: str = str(DEFAULT_PATH)


def database_path() -> Path:
    try:
        target = Path(StorageSettings().trippilot_database_path)
    except (ValueError, OSError):
        raise StorageError("invalid_storage_configuration") from None
    if not target.is_absolute() or target.resolve() == LEGACY_PATH.resolve():
        raise StorageError("invalid_storage_configuration")
    return target


def _validate_archive_schema(connection: Connection) -> None:
    """Ownership and idempotency depend on DB constraints, not a marker alone."""
    schema = inspect(connection)
    columns = schema.get_columns("saved_trips")
    expected = {"id", "owner_id", "operation_id", "name", "saved_at", "schema_version",
                "start_date", "end_date", "transport_mode", "status", "snapshot_json"}
    if ({column["name"] for column in columns} != expected
            or any(column["nullable"] != (column["name"] == "owner_id") for column in columns)
            or schema.get_pk_constraint("saved_trips").get("constrained_columns") != ["id"]):
        raise StorageError("invalid_storage_schema")
    unique_columns = [tuple(item.get("column_names", ())) for item in schema.get_unique_constraints("saved_trips")]
    unique_indexes = [item for item in schema.get_indexes("saved_trips") if item.get("unique")]
    # An extra global operation_id uniqueness rule would incorrectly couple users.
    if (not unique_columns or any(columns != ("owner_id", "operation_id") for columns in unique_columns)
            or any(tuple(index.get("column_names", ())) != ("owner_id", "operation_id")
                   or index.get("dialect_options", {}).get("sqlite_where") is not None for index in unique_indexes)):
        raise StorageError("invalid_storage_schema")
    foreign_keys = schema.get_foreign_keys("saved_trips")
    if (len(foreign_keys) != 1 or foreign_keys[0].get("constrained_columns") != ["owner_id"]
            or foreign_keys[0].get("referred_table") != "accounts"
            or foreign_keys[0].get("referred_columns") != ["id"]):
        raise StorageError("invalid_storage_schema")


@lru_cache(maxsize=8)
def engine_for(path: str) -> Engine:
    target = Path(path)
    if not target.is_absolute() or target.resolve() == LEGACY_PATH.resolve():
        raise StorageError("invalid_storage_configuration")
    target = target.resolve()
    try:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
        except FileExistsError:
            target.chmod(0o600)
        # Import table declarations before create_all. These imports do not access DBs.
        from app import identity_models, saved_trips  # noqa: F401
        engine = create_engine(URL.create("sqlite", database=str(target)), connect_args={"check_same_thread": False, "timeout": 3}, echo=False)

        @event.listens_for(engine, "connect")
        def enforce_foreign_keys(dbapi_connection: sqlite3.Connection, _record: object) -> None:
            dbapi_connection.execute("PRAGMA foreign_keys=ON")

        with _initialization_lock, engine.begin() as connection:
            tables = inspect(connection).get_table_names()
            if tables:
                if "storage_metadata" not in tables:
                    raise StorageError("storage_migration_required")
                version = connection.execute(text("SELECT value FROM storage_metadata WHERE key = 'version'" )).scalar()
                if version != STORAGE_VERSION:
                    raise StorageError("unsupported_storage_version")
                if "saved_trips" not in tables:
                    raise StorageError("invalid_storage_schema")
                _validate_archive_schema(connection)
            # Invitation redemption is MySQL-only. Do not silently expand the
            # historical identity-v1 SQLite schema as a side effect of imports.
            tables_to_create = [table for name, table in SQLModel.metadata.tables.items() if name != "invitations"]
            SQLModel.metadata.create_all(connection, tables=tables_to_create)
            if not tables:
                connection.execute(text("INSERT INTO storage_metadata (key, value) VALUES ('version', :version)"), {"version": STORAGE_VERSION})
        target.chmod(0o600)
        return engine
    except StorageError:
        if "engine" in locals():
            engine.dispose()
        raise
    except (SQLAlchemyError, OSError):
        if "engine" in locals():
            engine.dispose()
        raise StorageError("storage_unavailable") from None


def current_engine(path: Path | None = None) -> Engine:
    from app.mysql_storage import current_mysql_engine, mysql_configured
    if mysql_configured():
        return current_mysql_engine()
    return engine_for(str(path if path is not None else database_path()))


def get_session(path: Annotated[Path, Depends(database_path)]) -> Iterator[Session]:
    try:
        with Session(current_engine(path), expire_on_commit=False) as session:
            yield session
    except (SQLAlchemyError, OSError):
        raise StorageError("storage_unavailable") from None


def _backup_new(source: Path, destination: Path) -> None:
    if not source.is_absolute() or not destination.is_absolute() or source.resolve() == destination.resolve():
        raise StorageError("invalid_migration_paths")
    if not source.is_file():
        raise StorageError("migration_source_missing")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    except FileExistsError:
        raise StorageError("migration_target_exists") from None
    try:
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original, closing(sqlite3.connect(destination)) as copied:
            if original.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise StorageError("migration_source_invalid")
            original.backup(copied)
    except (sqlite3.Error, OSError, StorageError):
        destination.unlink(missing_ok=True)
        raise StorageError("migration_copy_failed") from None


def migrate_legacy_copy(source: Path, destination: Path, backup: Path) -> dict[str, int | str]:
    """Create two new files and migrate only destination; never mutate source.

    On failure the pre-migration backup remains available. Existing targets are
    never overwritten, so repeating a command cannot destroy later user data.
    """
    if not all(p.is_absolute() for p in (source, destination, backup)):
        raise StorageError("invalid_migration_paths")
    source, destination, backup = map(lambda p: p.resolve(), (source, destination, backup))
    if len({source, destination, backup}) != 3 or destination == LEGACY_PATH.resolve() or backup == LEGACY_PATH.resolve():
        raise StorageError("invalid_migration_paths")
    if destination.exists() or backup.exists():
        raise StorageError("migration_target_exists")
    expected = {"id", "operation_id", "name", "saved_at", "schema_version", "start_date", "end_date", "transport_mode", "status", "snapshot_json"}
    try:
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original:
            columns = {r[1] for r in original.execute("PRAGMA table_info(saved_trips)")}
            tables = {r[0] for r in original.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if columns != expected or tables - {"saved_trips", "sqlite_sequence"}:
                raise StorageError("migration_source_schema_unsupported")
    except sqlite3.Error:
        raise StorageError("migration_source_invalid") from None
    _backup_new(source, backup)
    _backup_new(backup, destination)
    try:
        with closing(sqlite3.connect(destination)) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("ALTER TABLE saved_trips RENAME TO unowned_before_identity")
            db.execute("""CREATE TABLE saved_trips (
                id VARCHAR NOT NULL PRIMARY KEY, operation_id VARCHAR NOT NULL,
                name VARCHAR NOT NULL, saved_at VARCHAR NOT NULL,
                schema_version VARCHAR NOT NULL, start_date VARCHAR NOT NULL,
                end_date VARCHAR NOT NULL, transport_mode VARCHAR NOT NULL,
                status VARCHAR NOT NULL, snapshot_json VARCHAR NOT NULL,
                owner_id VARCHAR NULL REFERENCES accounts(id),
                CONSTRAINT uq_saved_trips_owner_operation UNIQUE (owner_id, operation_id))""")
            names = "id, operation_id, name, saved_at, schema_version, start_date, end_date, transport_mode, status, snapshot_json"
            db.execute(f"INSERT INTO saved_trips ({names}, owner_id) SELECT {names}, NULL FROM unowned_before_identity")
            count = db.execute("SELECT COUNT(*) FROM saved_trips").fetchone()[0]
            db.execute("DROP TABLE unowned_before_identity")
            db.execute("CREATE INDEX ix_saved_trips_owner_id ON saved_trips (owner_id)")
            db.execute("CREATE INDEX ix_saved_trips_saved_at ON saved_trips (saved_at)")
            db.execute("CREATE TABLE storage_metadata (key VARCHAR NOT NULL PRIMARY KEY, value VARCHAR NOT NULL)")
            db.execute("INSERT INTO storage_metadata VALUES ('version', ?)", (STORAGE_VERSION,))
            db.commit()
        # Populate identity tables through the same guarded initialization path.
        engine_for(str(destination)).dispose()
        engine_for.cache_clear()
        return {"storage_version": STORAGE_VERSION, "quarantined_records": count}
    except (sqlite3.Error, StorageError):
        raise StorageError("migration_failed_backup_retained") from None


def restore_legacy_backup(backup: Path, destination: Path) -> None:
    """Rollback artifact for the preceding application, never in-place restore."""
    if not backup.is_absolute() or not destination.is_absolute() or destination.resolve() == LEGACY_PATH.resolve():
        raise StorageError("invalid_migration_paths")
    _backup_new(backup.resolve(), destination.resolve())
