"""MySQL/InnoDB quota ledger, reusing the established allowance/state rules.

The immutable identity row is the transaction serialization point for all
account/global conditions. Counts remain derived from conservative attempts,
not a second mutable counter. Runtime never creates schema or an identity.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
import re
import time

from sqlalchemy import (BigInteger, CheckConstraint, Column, Index, MetaData,
                        String, Table, inspect, text)
from sqlalchemy.engine import Connection, CursorResult, Engine
from sqlalchemy.exc import SQLAlchemyError

from core.business_usage import (IDENTIFIER, SERVICES, STATES, UsageError,
                                UsageSettings, UsageStore)

MYSQL_USAGE_VERSION = "business-usage-mysql-v1"
metadata = MetaData()
usage_metadata = Table(
    "usage_metadata", metadata,
    Column("key", String(64, collation="ascii_bin"), primary_key=True),
    Column("value", String(128, collation="ascii_bin"), nullable=False),
    mysql_engine="InnoDB", mysql_charset="ascii", mysql_collate="ascii_bin",
)
usage_attempts = Table(
    "usage_attempts", metadata,
    Column("id", String(32, collation="ascii_bin"), primary_key=True),
    Column("owner_id", String(32, collation="ascii_bin"), nullable=False),
    Column("service", String(32, collation="ascii_bin"), nullable=False),
    Column("created_at", BigInteger, nullable=False),
    Column("state", String(16, collation="ascii_bin"), nullable=False),
    Column("dispatched_at", BigInteger, nullable=True),
    CheckConstraint("length(id) = 32", name="usage_id_length"),
    CheckConstraint("length(owner_id) = 32", name="usage_owner_length"),
    CheckConstraint("created_at >= 0", name="usage_created_nonnegative"),
    CheckConstraint("dispatched_at >= 0", name="usage_dispatch_nonnegative"),
    CheckConstraint("service IN (" + ",".join("'" + value + "'" for value in SERVICES) + ")", name="usage_service_allowed"),
    CheckConstraint("state IN ('reserved','dispatching','received','failed','cancelled')", name="usage_state_allowed"),
    mysql_engine="InnoDB", mysql_charset="ascii", mysql_collate="ascii_bin",
)
Index("usage_created", usage_attempts.c.created_at)
Index("usage_owner_created", usage_attempts.c.owner_id, usage_attempts.c.created_at)
Index("usage_service_created", usage_attempts.c.service, usage_attempts.c.created_at)
Index("usage_owner_service_created", usage_attempts.c.owner_id, usage_attempts.c.service, usage_attempts.c.created_at)
MYSQL_USAGE_TABLES = frozenset(metadata.tables)


def _canonical_check(value: str) -> str:
    value = re.sub(r"_[a-zA-Z0-9]+(?=')", "", value)
    return re.sub(r"[\s`()]+", "", value).lower()


def validate_usage_schema(connection: Connection, identity: str) -> None:
    """Verify this component without rejecting unrelated private-store tables."""
    if connection.dialect.name != "mysql" or not isinstance(identity, str) or IDENTIFIER.fullmatch(identity) is None:
        raise UsageError()
    # Acknowledged reservations must not intentionally remain only in InnoDB's
    # periodic log flush buffer. Never alter operator configuration or fall back
    # to a fresh store; unsafe durability disables new metered work instead.
    durability = connection.execute(text("SELECT @@GLOBAL.innodb_flush_log_at_trx_commit")).scalar_one()
    if type(durability) is not int or durability != 1:
        raise UsageError()
    schema = inspect(connection)
    if not MYSQL_USAGE_TABLES <= set(schema.get_table_names()):
        raise UsageError()
    for table in metadata.sorted_tables:
        actual = schema.get_columns(table.name)
        if {column["name"] for column in actual} != set(table.c.keys()):
            raise UsageError()
        for column in actual:
            expected = table.c[column["name"]]
            if column["nullable"] != expected.nullable:
                raise UsageError()
            if isinstance(expected.type, String):
                if (not isinstance(column["type"], String)
                        or column["type"].length != expected.type.length
                        or column["type"].collation != "ascii_bin"):
                    raise UsageError()
            elif not isinstance(column["type"], BigInteger):
                raise UsageError()
        if schema.get_pk_constraint(table.name).get("constrained_columns") != [list(table.primary_key.columns)[0].name]:
            raise UsageError()
        if schema.get_table_options(table.name).get("mysql_engine") != "InnoDB":
            raise UsageError()
        expected_checks = {constraint.name: _canonical_check(str(constraint.sqltext))
                           for constraint in table.constraints if isinstance(constraint, CheckConstraint)}
        actual_checks = {item["name"]: _canonical_check(item["sqltext"])
                         for item in schema.get_check_constraints(table.name)}
        if actual_checks != expected_checks:
            raise UsageError()
        expected_indexes = {index.name: tuple(column.name for column in index.columns) for index in table.indexes}
        actual_indexes = {index["name"]: tuple(index["column_names"])
                          for index in schema.get_indexes(table.name) if not index.get("unique")}
        if actual_indexes != expected_indexes or schema.get_unique_constraints(table.name) or schema.get_foreign_keys(table.name):
            raise UsageError()
    values = {row[0]: row[1] for row in connection.execute(text("SELECT `key`, value FROM usage_metadata"))}
    if values != {"version": MYSQL_USAGE_VERSION, "identity": identity}:
        raise UsageError()


def initialize_usage_schema(engine: Engine, identity: str) -> None:
    """Explicit provisioning only. MySQL DDL can partially commit: never retry
    over existing component tables or reinterpret them as a fresh allowance.
    The caller preserves or exclusively creates the external identity anchor.
    """
    if engine.dialect.name != "mysql" or not isinstance(identity, str) or IDENTIFIER.fullmatch(identity) is None:
        raise UsageError()
    try:
        with engine.connect() as connection:
            if MYSQL_USAGE_TABLES & set(inspect(connection).get_table_names()):
                raise UsageError()
        metadata.create_all(engine, checkfirst=False)
        with engine.begin() as connection:
            connection.execute(usage_metadata.insert(), [
                {"key": "version", "value": MYSQL_USAGE_VERSION},
                {"key": "identity", "value": identity},
            ])
            validate_usage_schema(connection, identity)
    except (SQLAlchemyError, OSError, ValueError):
        raise UsageError() from None


def _checked_attempt(row: Mapping[str, object]) -> dict[str, object]:
    if set(row) != set(usage_attempts.c.keys()):
        raise UsageError()
    for name in ("id", "owner_id"):
        if not isinstance(row[name], str) or IDENTIFIER.fullmatch(row[name]) is None:
            raise UsageError()
    if (not isinstance(row["service"], str) or row["service"] not in SERVICES
            or not isinstance(row["state"], str) or row["state"] not in STATES):
        raise UsageError()
    created, dispatched = row["created_at"], row["dispatched_at"]
    if (type(created) is not int or not 0 <= created <= 2 ** 63 - 1
            or dispatched is not None and (type(dispatched) is not int or not created <= dispatched <= 2 ** 63 - 1)
            or row["state"] == "reserved" and dispatched is not None
            or row["state"] in {"dispatching", "received"} and dispatched is None):
        raise UsageError()
    return dict(row)


def import_usage_rows(connection: Connection, rows: Iterable[Mapping[str, object]]) -> int:
    """Copy source attempts in the caller's transaction; never reset or refund.
    Schema must have been explicitly provisioned, and the target must be empty.
    Returns imported row count. The migration owns source/schema/hash checks.
    """
    if connection.execute(text("SELECT count(*) FROM usage_attempts")).scalar_one() != 0:
        raise UsageError()
    count = 0
    batch: list[dict[str, object]] = []
    for row in rows:
        batch.append(_checked_attempt(row))
        if len(batch) == 500:
            connection.execute(usage_attempts.insert(), batch)
            count += len(batch)
            batch = []
    if batch:
        connection.execute(usage_attempts.insert(), batch)
        count += len(batch)
    return count


class _Rows:
    def __init__(self, result: CursorResult):
        self.result = result

    def fetchone(self) -> tuple | None:
        row = self.result.fetchone()
        return None if row is None else tuple(row)

    def __iter__(self) -> Iterator[tuple]:
        return (tuple(row) for row in self.result)


class _RuleConnection:
    """Bind the existing fixed SQL statements to SQLAlchemy, never interpolate
    user text. This preserves the SQLite allowance/state algorithm verbatim.
    """
    def __init__(self, connection: Connection):
        self.connection = connection

    def execute(self, statement: str, parameters: Sequence[object] = ()) -> _Rows:
        parts = statement.split("?")
        if len(parts) != len(parameters) + 1:
            raise UsageError()
        query = parts[0]
        for index, part in enumerate(parts[1:]):
            query += f":v{index}" + part
        return _Rows(self.connection.execute(text(query), {f"v{index}": value for index, value in enumerate(parameters)}))


class MySQLUsageStore(UsageStore):
    def __init__(self, settings: UsageSettings | None = None, *, clock: Callable[[], float] = time.time,
                 engine: Engine | None = None):
        super().__init__(settings, clock=clock)
        self.engine = engine

    def _engine(self) -> Engine:
        if self.engine is not None:
            if self.engine.dialect.name != "mysql":
                raise UsageError()
            return self.engine
        from memory.mysql_storage import current_mysql_engine
        try:
            return current_mysql_engine()
        except Exception:
            # Storage/configuration details must not leak through quota errors.
            raise UsageError() from None

    def _anchor_identity(self) -> str:
        from pathlib import Path
        try:
            if self.settings.business_usage_identity_path is not None:
                anchor = Path(self.settings.business_usage_identity_path)
                if not anchor.is_absolute():
                    raise UsageError()
                # _identity reads only the anchor associated with this basename.
                # Retain the exact hardened format shared with legacy ledgers.
                if anchor.is_symlink() or not anchor.is_file() or anchor.stat().st_nlink != 1 or anchor.stat().st_size != 33:
                    raise UsageError()
                value = anchor.read_text(encoding="ascii")
                if not value.endswith("\n") or IDENTIFIER.fullmatch(value[:-1]) is None:
                    raise UsageError()
                return value[:-1]
            return self._identity(self._path())
        except (OSError, ValueError, UnicodeError):
            raise UsageError() from None

    @contextmanager
    def _connection(self) -> Iterator[_RuleConnection]:
        try:
            identity = self._anchor_identity()
            with self._engine().connect() as connection:
                # Reflection/metadata reads before a lock must not establish a
                # stale REPEATABLE READ snapshot. After waiting for the common
                # lock every count must include the previous owner's commit.
                connection = connection.execution_options(isolation_level="READ COMMITTED")
                with connection.begin():
                    connection.execute(text("SET SESSION innodb_lock_wait_timeout = 3"))
                    validate_usage_schema(connection, identity)
                    locked = connection.execute(text("SELECT value FROM usage_metadata WHERE `key`='identity' FOR UPDATE")).scalar_one_or_none()
                    if locked != identity:
                        raise UsageError()
                    yield _RuleConnection(connection)
        except UsageError:
            raise
        except (SQLAlchemyError, OSError, ValueError, OverflowError):
            raise UsageError() from None

    def initialize(self) -> None:
        # A migration must preserve an existing allowance identity and copy all
        # attempts. Runtime/legacy CLI cannot accidentally create a fresh one.
        raise UsageError()

    def verify(self) -> None:
        with self._connection():
            pass
