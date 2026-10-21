"""Durable tasks and explicitly confirmed preferences. No runtime DDL/network.

Task checkpoints are not authorization to replay tools. In particular a running
or uncertain receipt surviving restart never creates another send allowance.
The caller validates travel snapshots before placing them in a checkpoint.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import re
import time
from typing import Any, Callable, Iterator
from uuid import uuid4

from sqlalchemy import BigInteger, Boolean, Integer, Text, UniqueConstraint, inspect, select, text, update
from sqlalchemy.engine import Connection, Engine
from sqlmodel import SQLModel

from memory.chat_storage import ChatError, ChatStore, _ID
from memory.chat_models import TurnRow
from memory.assistant_models import (ASSISTANT_TABLES, ASSISTANT_VERSION,
                                     AssistantPreferenceRow as Preference, AssistantTaskRow as Task)

MAX_CHECKPOINT_BYTES = 2 * 1024 * 1024
MAX_TASKS_PER_CONVERSATION = 100
MAX_PREFERENCES = 30
STATES = frozenset({"running", "waiting", "succeeded", "failed", "cancelled", "uncertain"})
TERMINAL = frozenset({"succeeded", "failed", "cancelled", "uncertain"})
CHECKPOINT_KEYS = frozenset({"workspace", "preview", "plan", "drawing", "trace", "waiting_for", "stop_reason", "result", "continuation"})
TRACE_KEYS = frozenset({"step", "tool", "operation_id", "state", "error_code", "elapsed_ms", "input_tokens", "output_tokens", "model", "version"})
_PRIVATE_KEYS = frozenset({"prompt", "system_prompt", "raw_prompt", "messages", "authorization", "cookie", "password", "api_key", "secret", "base64", "image_bytes", "chain_of_thought", "reasoning_content"})
_PREFERENCE_KEY = re.compile(r"[a-z][a-z0-9_]{0,39}")


class AssistantError(ChatError):
    pass


def assistant_tables() -> list:
    return [SQLModel.metadata.tables[name] for name in sorted(ASSISTANT_TABLES)]


def validate_assistant_extension(db: Connection, *, required: bool = True) -> bool:
    """Validate this exact optional extension; never create or repair tables."""
    schema = inspect(db)
    tables = set(schema.get_table_names())
    present = ASSISTANT_TABLES & tables
    marker = db.execute(text("SELECT value FROM storage_metadata WHERE `key`='assistant_version'")).scalar()
    if not present and marker is None:
        if required:
            raise AssistantError("assistant_migration_required")
        return False
    if present != ASSISTANT_TABLES or marker != ASSISTANT_VERSION or not {"accounts", "chat_conversations", "chat_turns"} <= tables:
        raise AssistantError("invalid_assistant_schema")
    for table in assistant_tables():
        columns = {item["name"]: item for item in schema.get_columns(table.name)}
        if set(columns) != set(table.c.keys()):
            raise AssistantError("invalid_assistant_schema")
        for column in table.c:
            actual, expected = columns[column.name], column.type.dialect_impl(db.dialect)
            compatible = actual["type"]._type_affinity is expected._type_affinity
            if db.dialect.name == "mysql" and isinstance(expected, Boolean):
                from sqlalchemy.dialects.mysql import TINYINT
                compatible = isinstance(actual["type"], TINYINT) and actual["type"].display_width == 1
            if (actual["nullable"] != column.nullable or not compatible
                    or getattr(actual["type"], "length", None) != getattr(column.type, "length", None)):
                raise AssistantError("invalid_assistant_schema")
            if db.dialect.name == "mysql" and isinstance(expected, Text) and type(actual["type"]) is not type(expected):
                raise AssistantError("invalid_assistant_schema")
            if db.dialect.name == "mysql" and isinstance(expected, Integer):
                from sqlalchemy.dialects.mysql import BIGINT, TINYINT
                if (getattr(actual["type"], "unsigned", False) or isinstance(expected, BigInteger) != isinstance(actual["type"], BIGINT)
                        or isinstance(actual["type"], TINYINT)):
                    raise AssistantError("invalid_assistant_schema")
        if schema.get_pk_constraint(table.name).get("constrained_columns") != ["id"]:
            raise AssistantError("invalid_assistant_schema")
        expected_unique = {tuple(c.name for c in item.columns) for item in table.constraints if isinstance(item, UniqueConstraint)}
        actual_unique = {tuple(item["column_names"]) for item in schema.get_unique_constraints(table.name)}
        actual_unique |= {tuple(item["column_names"]) for item in schema.get_indexes(table.name) if item.get("unique")}
        expected_fks = {(tuple(c.parent.name for c in item.elements), item.referred_table.name,
                         tuple(c.column.name for c in item.elements)) for item in table.foreign_key_constraints}
        actual_fks = {(tuple(item["constrained_columns"]), item["referred_table"], tuple(item["referred_columns"]))
                      for item in schema.get_foreign_keys(table.name)}
        if actual_unique != expected_unique or actual_fks != expected_fks:
            raise AssistantError("invalid_assistant_schema")
        if db.dialect.name == "mysql":
            options = schema.get_table_options(table.name)
            if options.get("mysql_engine") != "InnoDB" or options.get("mysql_collate") != "utf8mb4_bin":
                raise AssistantError("invalid_assistant_schema")
    return True


def _json(value: object) -> str:
    """Bounds include geometry, but never truncate an accepted snapshot."""
    count = 0
    def walk(item: Any, depth: int = 0) -> None:
        nonlocal count
        count += 1
        if count > 100_000 or depth > 24:
            raise AssistantError("assistant_checkpoint_limit", 422)
        if isinstance(item, dict):
            if len(item) > 100:
                raise AssistantError("assistant_checkpoint_limit", 422)
            for key, child in item.items():
                if not isinstance(key, str) or len(key) > 80 or key.lower() in _PRIVATE_KEYS:
                    raise AssistantError("assistant_checkpoint_invalid", 422)
                walk(child, depth + 1)
        elif isinstance(item, list):
            if len(item) > 30_000:
                raise AssistantError("assistant_checkpoint_limit", 422)
            for child in item:
                walk(child, depth + 1)
        elif isinstance(item, str):
            if len(item) > 200_000 or any(ord(c) < 32 and c not in "\n\r\t" for c in item):
                raise AssistantError("assistant_checkpoint_invalid", 422)
        elif item is not None and type(item) not in {bool, int, float}:
            raise AssistantError("assistant_checkpoint_invalid", 422)
        elif type(item) is int and abs(item) > 9_007_199_254_740_991:
            raise AssistantError("assistant_checkpoint_invalid", 422)
    walk(value)
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode()) > MAX_CHECKPOINT_BYTES:
            raise AssistantError("assistant_checkpoint_limit", 422)
        return encoded
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise AssistantError("assistant_checkpoint_invalid", 422) from None


def validate_checkpoint(value: object) -> dict:
    if not isinstance(value, dict) or set(value) - CHECKPOINT_KEYS:
        raise AssistantError("assistant_checkpoint_invalid", 422)
    trace = value.get("trace", [])
    if (not isinstance(trace, list) or len(trace) > 100 or any(not isinstance(row, dict) or set(row) - TRACE_KEYS for row in trace)):
        raise AssistantError("assistant_trace_invalid", 422)
    for row in trace:
        for key, item in row.items():
            if key in {"elapsed_ms", "input_tokens", "output_tokens", "step"}:
                if item is not None and (type(item) is not int or not 0 <= item <= 1_000_000_000):
                    raise AssistantError("assistant_trace_invalid", 422)
            elif item is not None and (not isinstance(item, str) or len(item) > 120 or re.fullmatch(r"[a-zA-Z0-9_.:/-]*", item) is None):
                raise AssistantError("assistant_trace_invalid", 422)
    return json.loads(_json(value))


def _decoded(raw: str) -> dict:
    if not isinstance(raw, str) or len(raw.encode()) > MAX_CHECKPOINT_BYTES:
        raise AssistantError("assistant_record_invalid")
    def unique(pairs: list) -> dict:
        result: dict = {}
        for key, value in pairs:
            if key in result:
                raise ValueError()
            result[key] = value
        return result
    try:
        return validate_checkpoint(json.loads(raw, object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError())))
    except (ValueError, TypeError, RecursionError, AssistantError):
        raise AssistantError("assistant_record_invalid") from None


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


class AssistantStore:
    def __init__(self, engine: Engine | None = None, *, clock: Callable[[], float] = time.time):
        if engine is None:
            from memory.mysql_storage import current_mysql_engine
            engine = current_mysql_engine()
        self.engine, self.clock = engine, clock
        self.chat = ChatStore(engine, clock=clock)

    @contextmanager
    def _transaction(self, owner: str, *, write: bool = False) -> Iterator[Connection]:
        with self.chat._transaction(owner, write=write) as db:
            validate_assistant_extension(db)
            yield db

    def _conversation(self, db: Connection, owner: str, cid: str, *, write: bool = False, operation_id: str | None = None) -> dict:
        row = self.chat._row(db, owner, cid)
        self.chat._view(db, row)
        if write and row["active_turn_id"] is not None:
            active = db.execute(select(TurnRow.operation_id).where(TurnRow.id == row["active_turn_id"], TurnRow.owner_id == owner)).scalar()
            if operation_id is None or active != operation_id:
                raise AssistantError("assistant_conversation_busy", 409)
        return row

    @staticmethod
    def _row(db: Connection, owner: str, op: str) -> dict:
        if not isinstance(op, str) or _ID.fullmatch(op) is None:
            raise AssistantError("assistant_not_found", 404)
        row = db.execute(select(Task.__table__).where(Task.id == op, Task.owner_id == owner)).mappings().first()
        if row is None:
            raise AssistantError("assistant_not_found", 404)
        return dict(row)

    @staticmethod
    def _view(row: dict, conversation: dict) -> dict:
        if (row["id"] != row["operation_id"] or row["state"] not in STATES
                or not isinstance(row["id"], str) or _ID.fullmatch(row["id"]) is None
                or row["conversation_id"] != conversation["id"]
                or type(row["revision"]) is not int or not 0 <= row["revision"] <= 1000
                or type(row["conversation_revision"]) is not int or not 0 <= row["conversation_revision"] <= conversation["revision"]
                or type(row["sequence"]) is not int or not 1 <= row["sequence"] <= MAX_TASKS_PER_CONVERSATION
                or any(type(row[key]) is not int or not 0 <= row[key] <= 2**63 - 1 for key in ("created_at", "updated_at"))
                or row["owner_id"] != conversation["owner_id"] or row["updated_at"] < row["created_at"]):
            raise AssistantError("assistant_record_invalid")
        return {"schema_version": ASSISTANT_VERSION, "id": row["id"], "operation_id": row["operation_id"],
                "conversation_id": row["conversation_id"], "conversation_revision": row["conversation_revision"],
                "revision": row["revision"], "state": row["state"], "checkpoint": _decoded(row["checkpoint_json"]),
                "created_at": _iso(row["created_at"]), "updated_at": _iso(row["updated_at"]),
                "stale": row["conversation_revision"] != conversation["revision"]}

    def begin(self, owner: str, conversation_id: str, operation_id: str, revision: int,
              *, expected_latest_task_id: str | None = None) -> dict:
        if not isinstance(operation_id, str) or _ID.fullmatch(operation_id) is None or type(revision) is not int or revision < 0:
            raise AssistantError("assistant_invalid_request", 422)
        with self._transaction(owner, write=True) as db:
            conversation = self._conversation(db, owner, conversation_id, write=True, operation_id=operation_id)
            # IDs remain burned even after terminal states or process restart.
            if db.execute(select(Task.id).where(Task.id == operation_id)).scalar() is not None:
                raise AssistantError("assistant_operation_already_used", 409)
            if conversation["revision"] != revision:
                raise AssistantError("assistant_revision_conflict", 409)
            tasks = list(db.execute(select(Task.__table__).where(Task.owner_id == owner, Task.conversation_id == conversation_id)).mappings())
            latest = max(tasks, key=lambda row: row["sequence"]) if tasks else None
            if expected_latest_task_id is not None and (latest is None or latest["id"] != expected_latest_task_id):
                raise AssistantError("assistant_task_obsolete", 409)
            if any(row["state"] == "running" and row["conversation_revision"] == revision for row in tasks):
                raise AssistantError("assistant_task_busy", 409)
            if len(tasks) >= MAX_TASKS_PER_CONVERSATION:
                raise AssistantError("assistant_task_limit", 409)
            now = int(self.clock())
            values = {"id": operation_id, "operation_id": operation_id, "owner_id": owner,
                      "conversation_id": conversation_id, "conversation_revision": revision, "revision": 0,
                      "sequence": (latest["sequence"] + 1) if latest else 1,
                      "state": "running", "checkpoint_json": "{}", "created_at": now, "updated_at": now}
            db.execute(Task.__table__.insert().values(**values))
            return self._view(values, conversation)

    def checkpoint(self, owner: str, operation_id: str, expected_revision: int, data: dict, state: str) -> dict:
        if state not in STATES or type(expected_revision) is not int or not 0 <= expected_revision < 1000:
            raise AssistantError("assistant_invalid_request", 422)
        encoded = _json(validate_checkpoint(data))
        with self._transaction(owner, write=True) as db:
            row = self._row(db, owner, operation_id)
            conversation = self._conversation(db, owner, row["conversation_id"], write=True, operation_id=operation_id)
            if row["revision"] != expected_revision or row["state"] in TERMINAL:
                raise AssistantError("assistant_task_obsolete", 409)
            if conversation["revision"] != row["conversation_revision"]:
                raise AssistantError("assistant_revision_conflict", 409)
            values = {"state": state, "revision": expected_revision + 1, "checkpoint_json": encoded,
                      "updated_at": max(int(self.clock()), row["updated_at"])}
            changed = db.execute(update(Task).where(Task.id == operation_id, Task.owner_id == owner,
                Task.revision == expected_revision, Task.state.in_(["running", "waiting"])).values(**values))
            if changed.rowcount != 1:
                raise AssistantError("assistant_task_obsolete", 409)
            return self._view({**row, **values}, conversation)

    def finalize(self, owner: str, operation_id: str, expected_revision: int, conversation_revision: int,
                 data: dict, state: str) -> dict:
        """Bind a finished chat turn without accepting an arbitrary new version.

        Finalization never executes tools. If the chat commit succeeded but this
        commit is uncertain, read the receipt; do not repeat the external work.
        """
        if (state not in STATES - {"running"} or type(expected_revision) is not int
                or not 0 <= expected_revision < 1000 or type(conversation_revision) is not int):
            raise AssistantError("assistant_invalid_request", 422)
        encoded = _json(validate_checkpoint(data))
        with self._transaction(owner, write=True) as db:
            row = self._row(db, owner, operation_id)
            conversation = self._conversation(db, owner, row["conversation_id"], write=True)
            latest = db.execute(select(TurnRow.__table__).where(TurnRow.owner_id == owner,
                TurnRow.conversation_id == row["conversation_id"]).order_by(TurnRow.frozen_revision.desc())).mappings().first()
            allowed_turn_states = {"succeeded"} if state in {"succeeded", "waiting"} else {state}
            # A chat reply can commit before candidate publication or task
            # finalization fails. That is uncertain follow-up work, never a
            # successful wait/cancel and never permission to resend a model.
            # This narrow marker is internal; all owner/op/frozen-version CAS
            # conditions below still apply, including the latest exact turn.
            if (state == "uncertain" and data.get("stop_reason") == "post_commit_uncertain"
                    and data.get("waiting_for") == "readback"
                    and data.get("continuation") == {"kind": "read_receipts_only", "operation_id": operation_id}):
                allowed_turn_states = {"uncertain", "succeeded"}
            if (row["revision"] != expected_revision or row["state"] in TERMINAL or latest is None
                    or latest["operation_id"] != operation_id or latest["state"] not in allowed_turn_states
                    or latest["frozen_revision"] != row["conversation_revision"]
                    or conversation["revision"] != conversation_revision
                    or conversation_revision != row["conversation_revision"] + 1):
                raise AssistantError("assistant_task_obsolete", 409)
            values = {"state": state, "revision": expected_revision + 1, "conversation_revision": conversation_revision,
                      "checkpoint_json": encoded, "updated_at": max(int(self.clock()), row["updated_at"])}
            changed = db.execute(update(Task).where(Task.id == operation_id, Task.owner_id == owner,
                Task.revision == expected_revision, Task.state.in_(["running", "waiting"])).values(**values))
            if changed.rowcount != 1:
                raise AssistantError("assistant_task_obsolete", 409)
            return self._view({**row, **values}, conversation)

    def get(self, owner: str, operation_id: str) -> dict:
        with self._transaction(owner) as db:
            row = self._row(db, owner, operation_id)
            return self._view(row, self._conversation(db, owner, row["conversation_id"]))

    def latest(self, owner: str, conversation_id: str) -> dict | None:
        with self._transaction(owner) as db:
            conversation = self._conversation(db, owner, conversation_id)
            row = db.execute(select(Task.__table__).where(Task.owner_id == owner, Task.conversation_id == conversation_id)
                .order_by(Task.sequence.desc())).mappings().first()
            return self._view(dict(row), conversation) if row else None

    @staticmethod
    def _preference_view(row: dict) -> dict:
        if (not isinstance(row["key"], str) or _PREFERENCE_KEY.fullmatch(row["key"]) is None
                or not isinstance(row["value"], str) or len(row["value"]) > 500
                or row["value"].strip() != row["value"] or row["source"] != "user" or row["consent"] not in (True, False, 0, 1)
                or (bool(row["consent"]) and not row["value"]) or (not row["consent"] and row["value"] != "")
                or any(ord(c) < 32 for c in row["value"]) or row["key"] in _PRIVATE_KEYS
                or type(row["version"]) is not int or not 1 <= row["version"] <= 1000
                or any(type(row[key]) is not int or not 0 <= row[key] <= 2**63 - 1 for key in ("created_at", "updated_at"))
                or row["updated_at"] < row["created_at"]):
            raise AssistantError("assistant_preference_invalid")
        return {"key": row["key"], "value": row["value"], "version": row["version"], "source": "user", "consent": bool(row["consent"]),
                "created_at": _iso(row["created_at"]), "updated_at": _iso(row["updated_at"])}

    def list_preferences(self, owner: str) -> list[dict]:
        with self._transaction(owner) as db:
            return [self._preference_view(dict(row)) for row in db.execute(select(Preference.__table__)
                .where(Preference.owner_id == owner, Preference.consent.is_(True)).order_by(Preference.key)).mappings()]

    def preference_versions(self, owner: str) -> dict[str, int]:
        """Expose only this owner's CAS tokens, including erased tombstones."""
        with self._transaction(owner) as db:
            rows = [self._preference_view(dict(row)) for row in db.execute(select(Preference.__table__)
                    .where(Preference.owner_id == owner).order_by(Preference.key)).mappings()]
            return {row["key"]: row["version"] for row in rows}

    def set_preference(self, owner: str, key: str, value: str, expected_version: int = 0, *, source: str = "user", consent: bool = False) -> dict:
        if (not isinstance(key, str) or _PREFERENCE_KEY.fullmatch(key) is None or key in _PRIVATE_KEYS
                or not isinstance(value, str) or not 1 <= len(value) <= 500 or value.strip() != value
                or any(ord(c) < 32 for c in value) or source != "user" or consent is not True
                or type(expected_version) is not int or not 0 <= expected_version < 1000):
            raise AssistantError("assistant_preference_invalid", 422)
        with self._transaction(owner, write=True) as db:
            rows = list(db.execute(select(Preference.__table__).where(Preference.owner_id == owner)).mappings())
            existing = next((dict(row) for row in rows if row["key"] == key), None)
            if (existing["version"] if existing else 0) != expected_version:
                raise AssistantError("assistant_preference_conflict", 409)
            if existing and existing["version"] >= 1000:
                raise AssistantError("assistant_preference_limit", 409)
            if existing is None and len(rows) >= MAX_PREFERENCES:
                raise AssistantError("assistant_preference_limit", 409)
            now = int(self.clock())
            values = {"value": value, "version": (existing["version"] if existing else 0) + 1, "source": "user", "consent": True,
                      "updated_at": max(now, existing["updated_at"] if existing else now)}
            if existing:
                db.execute(update(Preference).where(Preference.id == existing["id"], Preference.owner_id == owner).values(**values))
                return self._preference_view({**existing, **values})
            row = {"id": str(uuid4()), "owner_id": owner, "key": key, "created_at": now, **values}
            db.execute(Preference.__table__.insert().values(**row))
            return self._preference_view(row)

    def delete_preference(self, owner: str, key: str, expected_version: int) -> None:
        if not isinstance(key, str) or _PREFERENCE_KEY.fullmatch(key) is None or type(expected_version) is not int:
            raise AssistantError("assistant_preference_invalid", 422)
        with self._transaction(owner, write=True) as db:
            # Erase the text but retain a monotonically versioned tombstone.
            # Otherwise delete/recreate could make an old version=1 callback
            # delete an unrelated newly confirmed value (A -> B -> A).
            changed = db.execute(update(Preference).where(Preference.owner_id == owner, Preference.key == key,
                Preference.version == expected_version, Preference.consent.is_(True), Preference.version < 1000)
                .values(value="", consent=False, version=expected_version + 1, updated_at=int(self.clock())))
            if changed.rowcount != 1:
                raise AssistantError("assistant_preference_conflict", 409)
