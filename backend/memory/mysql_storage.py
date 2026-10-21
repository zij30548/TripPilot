"""Explicit MySQL selection; runtime never creates, migrates or falls back.

Only trusted server configuration may select a database. URL values and raw
driver exceptions must not become response/log content. Schema initialization
is an explicit operator action against an empty database, not an application
startup side effect. Snapshot versions are independent of this storage version.
"""
import re
from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import Table, inspect, text
from sqlalchemy.engine import Engine, URL, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import SQLModel, create_engine

from core.config import BACKEND_ENV_FILE
from memory.storage import StorageError

MYSQL_STORAGE_VERSION = "private-mysql-v1"
PRIVATE_TABLES = frozenset({"storage_metadata", "accounts", "auth_sessions", "login_buckets", "saved_trips", "invitations"})
# The hard-quota ledger shares this MySQL database but has its own validator and
# version/identity anchor. Its tables are never created by private initialization.
USAGE_TABLES = frozenset({"usage_metadata", "usage_attempts"})


class MySQLSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_ENV_FILE, env_file_encoding="utf-8", extra="ignore")
    trippilot_mysql_url: SecretStr = SecretStr("")


def _configured_url() -> str:
    try:
        return MySQLSettings().trippilot_mysql_url.get_secret_value()
    except (ValueError, OSError):
        raise StorageError("invalid_mysql_configuration") from None


def mysql_configured() -> bool:
    return bool(_configured_url())


def validated_url(raw: str) -> URL:
    try:
        if (not isinstance(raw, str) or not raw or len(raw) > 2048
                or any(ord(c) < 33 or ord(c) == 127 for c in raw) or "#" in raw):
            raise ValueError()
        parsed = make_url(raw)
        if (parsed.drivername != "mysql+pymysql" or not parsed.username or not parsed.password
                or not parsed.host or not parsed.host.isascii()
                or re.fullmatch(r"[a-zA-Z0-9_.:-]{1,253}", parsed.host) is None
                or not parsed.database or re.fullmatch(r"[a-zA-Z0-9_]{1,64}", parsed.database) is None
                or parsed.port is not None and not 1 <= parsed.port <= 65535
                or dict(parsed.query) not in ({}, {"charset": "utf8mb4"})):
            raise ValueError()
        return parsed.set(query={"charset": "utf8mb4"})
    except (ValueError, TypeError, SQLAlchemyError):
        raise StorageError("invalid_mysql_configuration") from None


@lru_cache(maxsize=4)
def engine_from_url(raw: str) -> Engine:
    url = validated_url(raw)
    try:
        return create_engine(url, pool_pre_ping=True, pool_size=3, max_overflow=0,
                             pool_timeout=3, pool_recycle=1800, echo=False,
                             hide_parameters=True, isolation_level="READ COMMITTED",
                             connect_args={"connect_timeout": 3, "read_timeout": 5, "write_timeout": 5})
    except (SQLAlchemyError, ImportError, ValueError):
        raise StorageError("mysql_unavailable") from None


def private_tables() -> list[Table]:
    from memory import identity_models, saved_trips  # noqa: F401
    return [SQLModel.metadata.tables[name] for name in sorted(PRIVATE_TABLES)]


def initialize_private_schema(engine: Engine, *, ready: bool = True) -> None:
    """Only a new empty MySQL DB. DDL is not transactionally reversible in MySQL.

    ready=False is used for the stop-the-world copy importer: the version marker
    must be inserted in its final transaction only after every table was copied.
    A partially initialized database is retained and rejected by runtime.
    """
    if engine.dialect.name != "mysql":
        raise StorageError("mysql_required")
    try:
        with engine.connect() as connection:
            if inspect(connection).get_table_names():
                raise StorageError("mysql_target_not_empty")
        SQLModel.metadata.create_all(engine, tables=private_tables())
        if ready:
            with engine.begin() as connection:
                connection.execute(text("INSERT INTO storage_metadata (`key`, value) VALUES ('version', :version)"),
                                   {"version": MYSQL_STORAGE_VERSION})
    except SQLAlchemyError:
        raise StorageError("mysql_initialization_failed") from None


def validate_private_schema(engine: Engine) -> None:
    if engine.dialect.name != "mysql":
        raise StorageError("mysql_required")
    try:
        with engine.connect() as connection:
            schema = inspect(connection)
            tables = set(schema.get_table_names())
            from memory.chat_models import CHAT_TABLES
            from memory.chat_places_models import CHAT_PLACES_TABLES
            from memory.assistant_models import ASSISTANT_TABLES
            if not PRIVATE_TABLES <= tables or tables - PRIVATE_TABLES - USAGE_TABLES - CHAT_TABLES - CHAT_PLACES_TABLES - ASSISTANT_TABLES:
                raise StorageError("invalid_mysql_schema")
            if connection.execute(text("SELECT value FROM storage_metadata WHERE `key`='version'")).scalar() != MYSQL_STORAGE_VERSION:
                raise StorageError("unsupported_mysql_storage_version")
            for table in private_tables():
                options = schema.get_table_options(table.name)
                if (str(options.get("mysql_engine", "")).lower() != "innodb"
                        or options.get("mysql_collate") != "utf8mb4_bin"):
                    raise StorageError("invalid_mysql_schema")
                columns = {column["name"]: column for column in schema.get_columns(table.name)}
                if set(columns) != set(table.columns.keys()):
                    raise StorageError("invalid_mysql_schema")
                for model in table.columns:
                    actual = columns[model.name]
                    if actual["nullable"] != model.nullable:
                        raise StorageError("invalid_mysql_schema")
                    # String capacity and integer width must not silently shrink
                    # imported values; SQL strict mode is also checked below.
                    expected_type = model.type.dialect_impl(engine.dialect)
                    from sqlalchemy import BigInteger, Boolean, Integer, Text
                    from sqlalchemy.dialects.mysql import BIGINT, MEDIUMTEXT, TINYINT
                    if isinstance(expected_type, Boolean):
                        compatible = isinstance(actual["type"], TINYINT) and actual["type"].display_width == 1
                    elif isinstance(expected_type, BigInteger):
                        compatible = isinstance(actual["type"], BIGINT) and not actual["type"].unsigned
                    elif isinstance(expected_type, Integer):
                        compatible = actual["type"]._type_affinity is Integer and not isinstance(actual["type"], TINYINT)
                    elif isinstance(expected_type, Text):
                        compatible = isinstance(actual["type"], MEDIUMTEXT)
                    else:
                        compatible = actual["type"]._type_affinity is expected_type._type_affinity
                    if not compatible:
                        raise StorageError("invalid_mysql_schema")
                    if getattr(model.type, "length", None) != getattr(actual["type"], "length", None):
                        raise StorageError("invalid_mysql_schema")
                if schema.get_pk_constraint(table.name).get("constrained_columns") != [c.name for c in table.primary_key.columns]:
                    raise StorageError("invalid_mysql_schema")
                expected_fks = {(tuple(fk.parent.name for fk in constraint.elements),
                                 constraint.referred_table.name,
                                 tuple(fk.column.name for fk in constraint.elements))
                                for constraint in table.foreign_key_constraints}
                actual_fks = {(tuple(fk.get("constrained_columns", [])), fk.get("referred_table"),
                               tuple(fk.get("referred_columns", []))) for fk in schema.get_foreign_keys(table.name)}
                if actual_fks != expected_fks:
                    raise StorageError("invalid_mysql_schema")
                from sqlalchemy import UniqueConstraint
                expected_unique = {tuple(c.name for c in constraint.columns) for constraint in table.constraints if isinstance(constraint, UniqueConstraint)}
                expected_unique |= {tuple(c.name for c in index.columns) for index in table.indexes if index.unique}
                actual_unique = {tuple(c.get("column_names", [])) for c in schema.get_unique_constraints(table.name)}
                actual_unique |= {tuple(c.get("column_names", [])) for c in schema.get_indexes(table.name) if c.get("unique")}
                if actual_unique != expected_unique:
                    raise StorageError("invalid_mysql_schema")
            mode = str(connection.execute(text("SELECT @@SESSION.sql_mode")).scalar() or "")
            if "STRICT_TRANS_TABLES" not in mode and "STRICT_ALL_TABLES" not in mode:
                raise StorageError("mysql_strict_mode_required")
            from memory.chat_storage import validate_chat_extension
            validate_chat_extension(connection, required=False)
    except StorageError:
        raise
    except (SQLAlchemyError, OSError, ValueError):
        raise StorageError("mysql_unavailable") from None


@lru_cache(maxsize=4)
def _validated_engine(raw: str) -> Engine:
    engine = engine_from_url(raw)
    validate_private_schema(engine)
    return engine


def current_mysql_engine() -> Engine:
    raw = _configured_url()
    if not raw:
        raise StorageError("mysql_not_configured")
    return _validated_engine(raw)
