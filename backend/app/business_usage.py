"""Durable, process-shared business budgets, independent from identity storage.

Every reservation consumes one conservative attempt before any paid dispatch or
media work. The ledger, including reservations not yet dispatched, is the single
counter source; there is deliberately no refund/reset API. Runtime never creates
a missing database. An independently stored identity anchor prevents a lost file
from silently becoming a fresh allowance after an application restart.
"""
from __future__ import annotations

import math
import json
import os
import re
import sqlite3
import time
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Iterator, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.config import BACKEND_ENV_FILE
from app.storage import DEFAULT_PATH as PRIVATE_PATH, LEGACY_PATH, StorageSettings

VERSION = "business-usage-v1"
ZONE = ZoneInfo("Asia/Shanghai")
DEFAULT_PATH = Path(__file__).resolve().parents[1] / "data" / "business-usage.sqlite3"
Service = Literal["amap_poi", "amap_route", "amap_weather", "amap_proxy", "qwen_text", "qwen_vision", "media_photo", "media_video"]
SERVICES: tuple[Service, ...] = ("amap_poi", "amap_route", "amap_weather", "amap_proxy", "qwen_text", "qwen_vision", "media_photo", "media_video")
State = Literal["reserved", "dispatching", "received", "failed", "cancelled"]
STATES = frozenset({"reserved", "dispatching", "received", "failed", "cancelled"})
IDENTIFIER = re.compile(r"[0-9a-f]{32}")
SAFE_MAXIMUM = 2_147_483_647


class DailyLimit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    account: StrictInt = Field(ge=0, le=SAFE_MAXIMUM)
    global_: StrictInt = Field(alias="global", ge=0, le=SAFE_MAXIMUM)


def _default_limits() -> dict[str, DailyLimit]:
    return {service: DailyLimit(account=account, **{"global": global_limit}) for service, account, global_limit in (
        ("amap_poi", 200, 2000), ("amap_route", 600, 6000),
        ("amap_weather", 20, 200), ("amap_proxy", 40, 400),
        ("qwen_text", 10, 50), ("qwen_vision", 5, 25),
        ("media_photo", 10, 100), ("media_video", 5, 25),
    )}


class UsageSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_ENV_FILE, env_file_encoding="utf-8", extra="ignore")
    business_usage_path: str = str(DEFAULT_PATH)
    # MySQL retains this independently stored anchor. It is not a fresh quota
    # identity and runtime must never recreate it when absent.
    business_usage_identity_path: str | None = None
    business_rate_window_seconds: StrictInt = Field(default=60, ge=1, le=3600)
    business_rate_account_limit: StrictInt = Field(default=60, ge=0, le=SAFE_MAXIMUM)
    business_rate_global_limit: StrictInt = Field(default=300, ge=0, le=SAFE_MAXIMUM)
    business_daily_limits: Annotated[dict[str, DailyLimit], NoDecode] = Field(default_factory=_default_limits)

    @field_validator("business_daily_limits", mode="before")
    @classmethod
    def strict_json(cls, value: object) -> object:
        if isinstance(value, str):
            if len(value) > 8192:
                raise ValueError("business daily limits are too large")

            def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
                output: dict[str, object] = {}
                for key, item in pairs:
                    if key in output:
                        raise ValueError("duplicate business daily limit key")
                    output[key] = item
                return output

            def nonfinite(_value: str) -> None:
                raise ValueError("nonfinite business daily limit")

            return json.loads(value, object_pairs_hook=unique, parse_constant=nonfinite)
        return value

    @field_validator("business_daily_limits")
    @classmethod
    def exact_services(cls, value: dict[str, DailyLimit]) -> dict[str, DailyLimit]:
        if set(value) != set(SERVICES):
            raise ValueError("business daily limits require the eight explicit services")
        return value

    @field_validator("business_rate_window_seconds", "business_rate_account_limit", "business_rate_global_limit", mode="before")
    @classmethod
    def environment_integer(cls, value: object) -> object:
        # Environment values are text; accept canonical decimal integers only,
        # while preserving StrictInt's rejection of bools/floats in code/tests.
        if isinstance(value, str) and re.fullmatch(r"0|[1-9][0-9]{0,9}", value):
            return int(value)
        return value


class UsageError(Exception):
    """Safe public error, deliberately distinct from parser/network failures."""
    def __init__(self, code: str = "business_usage_unavailable", *, service: str | None = None,
                 scope: str | None = None, retry_after_seconds: int | None = None,
                 reset_at: str | None = None):
        self.code, self.service, self.scope = code, service, scope
        self.retry_after_seconds, self.reset_at = retry_after_seconds, reset_at
        self.status = 503 if code == "business_usage_unavailable" else 429
        self.message = {
            "business_rate_limited": "业务请求过于频繁，请等待后主动重试。",
            "business_quota_exhausted": "本类服务今日调用额度已用尽，请等待重置后主动重试。",
            "business_service_disabled": "本类服务的调用额度当前已关闭，请联系管理员。",
            "business_usage_unavailable": "调用计数暂不可用，已阻止新增外部调用或媒体处理；只读历史仍可使用。",
        }.get(code, "调用计数暂不可用，已阻止新增外部调用或媒体处理。")
        super().__init__("business usage unavailable" if self.status == 503 else code)

    def detail(self) -> dict[str, object]:
        return {"code": self.code, "message": self.message, "service": self.service,
                "scope": self.scope, "retry_after_seconds": self.retry_after_seconds,
                "reset_at": self.reset_at}


def get_usage_settings() -> UsageSettings:
    try:
        return UsageSettings()
    except (ValueError, OSError):
        raise UsageError() from None


_TABLES = {
    "usage_metadata": "CREATE TABLE usage_metadata (key TEXT PRIMARY KEY NOT NULL, value TEXT NOT NULL)",
    "usage_attempts": "CREATE TABLE usage_attempts (id TEXT PRIMARY KEY NOT NULL CHECK(length(id)=32), owner_id TEXT NOT NULL CHECK(length(owner_id)=32), service TEXT NOT NULL CHECK(service IN ('amap_poi','amap_route','amap_weather','amap_proxy','qwen_text','qwen_vision','media_photo','media_video')), created_at INTEGER NOT NULL CHECK(created_at>=0), state TEXT NOT NULL CHECK(state IN ('reserved','dispatching','received','failed','cancelled')), dispatched_at INTEGER CHECK(dispatched_at>=0))",
}
_INDEXES = {
    "usage_created": "CREATE INDEX usage_created ON usage_attempts (created_at)",
    "usage_owner_created": "CREATE INDEX usage_owner_created ON usage_attempts (owner_id, created_at)",
    "usage_service_created": "CREATE INDEX usage_service_created ON usage_attempts (service, created_at)",
    "usage_owner_service_created": "CREATE INDEX usage_owner_service_created ON usage_attempts (owner_id, service, created_at)",
}


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).astimezone(ZONE).isoformat()


class UsageStore:
    def __init__(self, settings: UsageSettings | None = None, *, clock: Callable[[], float] = time.time):
        self.settings, self.clock = settings or get_usage_settings(), clock

    def _path(self) -> Path:
        try:
            path = Path(self.settings.business_usage_path)
            private = Path(StorageSettings().trippilot_database_path)
            bases = (LEGACY_PATH, PRIVATE_PATH, private)
            protected = tuple(item.with_name(item.name + suffix) for item in bases
                              for suffix in ("", "-wal", "-shm", "-journal"))
            if (not path.is_absolute() or path.is_symlink()
                    or path.resolve() in {item.resolve() for item in protected}
                    or path.exists() and any(item.exists() and path.samefile(item) for item in protected)):
                raise UsageError()
            return path.resolve()
        except (OSError, ValueError):
            raise UsageError() from None

    @staticmethod
    def _anchor(path: Path) -> Path:
        return path.with_name(path.name + ".identity")

    def _identity(self, path: Path) -> str:
        anchor = self._anchor(path)
        try:
            if anchor.is_symlink() or not anchor.is_file() or anchor.stat().st_size != 33:
                raise UsageError()
            identity = anchor.read_text(encoding="ascii")
            if not identity.endswith("\n") or IDENTIFIER.fullmatch(identity[:-1]) is None:
                raise UsageError()
            return identity[:-1]
        except (OSError, ValueError, UnicodeError):
            raise UsageError() from None

    def _validate(self, connection: sqlite3.Connection, identity: str) -> None:
        # Exact SQL and explicit index definitions also verify CHECK constraints,
        # rather than trusting a version marker in an arbitrary existing file.
        objects = connection.execute("SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
        expected = {("table", name): sql for name, sql in _TABLES.items()}
        expected.update({("index", name): sql for name, sql in _INDEXES.items()})
        if {(kind, name): sql for kind, name, sql in objects} != expected:
            raise UsageError()
        metadata = dict(connection.execute("SELECT key, value FROM usage_metadata"))
        if metadata != {"version": VERSION, "identity": identity} or connection.execute("PRAGMA user_version").fetchone() != (1,):
            raise UsageError()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = None
        try:
            path = self._path()
            identity = self._identity(path)
            if not path.is_file():
                raise UsageError()
            connection = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=3, isolation_level=None)
            connection.execute("PRAGMA busy_timeout=3000")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            self._validate(connection, identity)
            yield connection
            connection.commit()
        except UsageError:
            if connection is not None:
                self._rollback(connection)
            raise
        except (sqlite3.Error, OSError, ValueError, OverflowError):
            if connection is not None:
                self._rollback(connection)
            raise UsageError() from None
        finally:
            if connection is not None:
                self._close(connection)

    @staticmethod
    def _rollback(connection: sqlite3.Connection) -> None:
        try:
            connection.rollback()
        except sqlite3.Error:
            raise UsageError() from None

    @staticmethod
    def _close(connection: sqlite3.Connection) -> None:
        try:
            connection.close()
        except sqlite3.Error:
            raise UsageError() from None

    def initialize(self) -> None:
        """Only an explicit operator command may initialize a fresh allowance.

        On partial failure the anchor is intentionally retained. An operator
        must investigate and restore a matched pair, not silently reset quotas.
        """
        path = self._path()
        anchor = self._anchor(path)
        connection = None
        try:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if path.exists() or anchor.exists():
                raise UsageError()
            identity = uuid4().hex
            descriptor = os.open(anchor, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "w", encoding="ascii") as output:
                output.write(identity + "\n")
                output.flush()
                os.fsync(output.fileno())
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
            connection = sqlite3.connect(path, isolation_level=None)
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("BEGIN IMMEDIATE")
            for statement in (*_TABLES.values(), *_INDEXES.values()):
                connection.execute(statement)
            connection.executemany("INSERT INTO usage_metadata (key, value) VALUES (?, ?)", (("version", VERSION), ("identity", identity)))
            connection.execute("PRAGMA user_version=1")
            connection.commit()
            self._validate(connection, identity)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except UsageError:
            raise
        except (sqlite3.Error, OSError, ValueError):
            raise UsageError() from None
        finally:
            if connection is not None:
                self._close(connection)

    def verify(self) -> None:
        with self._connection() as connection:
            if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise UsageError()

    def _boundaries(self) -> tuple[int, int, int, int, int]:
        try:
            raw = self.clock()
            if not isinstance(raw, (float, int)) or isinstance(raw, bool) or not math.isfinite(raw) or raw < 0:
                raise UsageError()
            now = int(raw)
            current = datetime.fromtimestamp(now, timezone.utc).astimezone(ZONE)
            day = current.replace(hour=0, minute=0, second=0, microsecond=0)
            start, end = int(day.timestamp()), int((day + timedelta(days=1)).timestamp())
            window = self.settings.business_rate_window_seconds
            window_start = now // window * window
            return now, start, end, window_start, window_start + window
        except (ValueError, OverflowError, OSError):
            raise UsageError() from None

    @staticmethod
    def _owner(owner_id: str) -> None:
        if not isinstance(owner_id, str) or IDENTIFIER.fullmatch(owner_id) is None:
            raise UsageError()

    def reserve(self, owner_id: str, service: Service | str) -> str:
        self._owner(owner_id)
        if service not in SERVICES:
            raise UsageError()
        limits = self.settings.business_daily_limits[service]
        with self._connection() as connection:
            # Acquire the shared write lock before reading the clock: waiting
            # across a window/midnight must not consume yesterday's allowance.
            now, day_start, day_end, window_start, window_end = self._boundaries()
            self._clock_not_backwards(connection, now)
            for scope, limit in (("account", limits.account), ("global", limits.global_),
                                 ("account", self.settings.business_rate_account_limit),
                                 ("global", self.settings.business_rate_global_limit)):
                if limit == 0:
                    raise UsageError("business_service_disabled", service=service, scope=scope)
            for scope, limit in (("account", self.settings.business_rate_account_limit), ("global", self.settings.business_rate_global_limit)):
                where = "created_at >= ? AND created_at < ?" + (" AND owner_id = ?" if scope == "account" else "")
                parameters = (window_start, window_end, owner_id) if scope == "account" else (window_start, window_end)
                count = connection.execute("SELECT count(*) FROM usage_attempts WHERE " + where, parameters).fetchone()[0]
                if count >= limit:
                    raise UsageError("business_rate_limited", service=service, scope=scope,
                                     retry_after_seconds=max(1, window_end - now), reset_at=_iso(window_end))
            for scope, limit in (("account", limits.account), ("global", limits.global_)):
                where = "service = ? AND created_at >= ? AND created_at < ?" + (" AND owner_id = ?" if scope == "account" else "")
                parameters = (service, day_start, day_end, owner_id) if scope == "account" else (service, day_start, day_end)
                count = connection.execute("SELECT count(*) FROM usage_attempts WHERE " + where, parameters).fetchone()[0]
                if count >= limit:
                    raise UsageError("business_quota_exhausted", service=service, scope=scope,
                                     retry_after_seconds=max(1, day_end - now), reset_at=_iso(day_end))
            attempt = uuid4().hex
            connection.execute("INSERT INTO usage_attempts (id, owner_id, service, created_at, state) VALUES (?, ?, ?, ?, 'reserved')", (attempt, owner_id, service, now))
        return attempt

    def mark(self, attempt_id: str, state: State | str) -> None:
        if not isinstance(attempt_id, str) or IDENTIFIER.fullmatch(attempt_id) is None or state not in STATES or state == "reserved":
            raise UsageError()
        with self._connection() as connection:
            row = connection.execute("SELECT state FROM usage_attempts WHERE id = ?", (attempt_id,)).fetchone()
            if row is None:
                raise UsageError()
            # Terminal writes are idempotent only for that exact terminal state.
            if row[0] == state:
                if state != "dispatching":
                    return
                raise UsageError()
            allowed = {"reserved": {"dispatching", "failed", "cancelled"}, "dispatching": {"received", "failed", "cancelled"}}
            if state not in allowed.get(row[0], set()):
                raise UsageError()
            if state == "dispatching":
                now = self._boundaries()[0]
                self._clock_not_backwards(connection, now)
                connection.execute("UPDATE usage_attempts SET state = ?, dispatched_at = ? WHERE id = ?", (state, now, attempt_id))
            else:
                connection.execute("UPDATE usage_attempts SET state = ? WHERE id = ?", (state, attempt_id))

    def own_usage(self, owner_id: str) -> dict[str, object]:
        self._owner(owner_id)
        with self._connection() as connection:
            now, day_start, day_end, window_start, window_end = self._boundaries()
            self._clock_not_backwards(connection, now)
            rate = connection.execute("SELECT count(*) FROM usage_attempts WHERE owner_id = ? AND created_at >= ? AND created_at < ?", (owner_id, window_start, window_end)).fetchone()[0]
            counts = dict(connection.execute("SELECT service, count(*) FROM usage_attempts WHERE owner_id = ? AND created_at >= ? AND created_at < ? GROUP BY service", (owner_id, day_start, day_end)))
        return {"version": VERSION, "timezone": "Asia/Shanghai", "now": _iso(now), "reset_at": _iso(day_end),
                "rate": {"used": rate, "limit": self.settings.business_rate_account_limit,
                         "window_seconds": self.settings.business_rate_window_seconds, "reset_at": _iso(window_end)},
                "services": {service: {"used": counts.get(service, 0), "limit": self.settings.business_daily_limits[service].account,
                                       "remaining": max(0, self.settings.business_daily_limits[service].account - counts.get(service, 0))}
                             for service in SERVICES}}

    @staticmethod
    def _clock_not_backwards(connection: sqlite3.Connection, now: int) -> None:
        latest = connection.execute("SELECT max(created_at) FROM usage_attempts").fetchone()[0]
        # Wall-clock manipulation must not revive an earlier day's allowance.
        # Fail closed until the operator repairs the clock / it catches up;
        # neither an automatic reset nor an earlier-time reservation is allowed.
        if latest is not None and latest > now:
            raise UsageError()


def get_usage_store() -> UsageStore:
    # Selection is configuration-based, never an availability fallback. A
    # configured MySQL failure must not silently replenish the legacy ledger.
    from app.mysql_storage import mysql_configured
    if mysql_configured():
        from app.mysql_usage import MySQLUsageStore
        return MySQLUsageStore()
    return UsageStore()
