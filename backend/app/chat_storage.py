"""Owner-scoped chat intent storage with explicit schema and short transactions.

No network work occurs here. A running receipt survives process restart and is
never treated as permission to resend. Explicit cancellation invalidates it;
later completion cannot overwrite the newer revision.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import re
import time
from typing import Callable
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import Text, UniqueConstraint, inspect, select, text, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import SQLModel

from app.chat_models import CHAT_TABLES, CHAT_VERSION, ConversationRow, TurnRow
from app.schemas.chat import Conversation, ConversationList, Intent, Source
from app.storage import StorageError, current_engine

MAX_CONVERSATIONS = 50
MAX_TOTAL_CONVERSATIONS = 200
MAX_TURNS = 20
MAX_JSON_BYTES = 256 * 1024
_OWNER = re.compile(r"[0-9a-f]{32}")
_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
_ERRORS = frozenset({"cancelled", "model_unavailable", "model_invalid_output", "model_timeout", "model_error",
                     "business_usage_unavailable", "business_quota_exhausted", "business_rate_limited",
                     "business_service_disabled", "request_failed", "request_uncertain"})


class ChatError(StorageError):
    pass


def chat_tables() -> list:
    return [SQLModel.metadata.tables[name] for name in sorted(CHAT_TABLES)]


def validate_chat_extension(connection: Connection, *, required: bool = True) -> bool:
    """Exact extra tables/columns/keys, not a permissive unknown-field escape."""
    schema = inspect(connection)
    present = CHAT_TABLES & set(schema.get_table_names())
    marker = connection.execute(text("SELECT value FROM storage_metadata WHERE `key`='chat_version'")).scalar()
    if not present and marker is None:
        if required:
            raise ChatError("chat_migration_required")
        return False
    if present != CHAT_TABLES or marker != CHAT_VERSION:
        raise ChatError("invalid_chat_schema")
    if connection.dialect.name == "sqlite" and set(schema.get_table_names()) - {
            "storage_metadata", "accounts", "auth_sessions", "login_buckets", "saved_trips", "sqlite_sequence"} - CHAT_TABLES:
        raise ChatError("invalid_chat_schema")
    for table in chat_tables():
        columns = {item["name"]: item for item in schema.get_columns(table.name)}
        if set(columns) != set(table.c.keys()):
            raise ChatError("invalid_chat_schema")
        for column in table.c:
            actual = columns[column.name]
            expected_type = column.type.dialect_impl(connection.dialect)
            if (actual["nullable"] != column.nullable
                    or actual["type"]._type_affinity is not expected_type._type_affinity
                    or getattr(actual["type"], "length", None) != getattr(column.type, "length", None)):
                raise ChatError("invalid_chat_schema")
            if connection.dialect.name == "mysql" and isinstance(column.type, Text):
                from sqlalchemy.dialects.mysql import MEDIUMTEXT
                if not isinstance(actual["type"], MEDIUMTEXT):
                    raise ChatError("invalid_chat_schema")
        if schema.get_pk_constraint(table.name).get("constrained_columns") != ["id"]:
            raise ChatError("invalid_chat_schema")
        expected_unique = {tuple(c.name for c in constraint.columns) for constraint in table.constraints
                           if isinstance(constraint, UniqueConstraint)}
        actual_unique = {tuple(item["column_names"]) for item in schema.get_unique_constraints(table.name)}
        actual_unique |= {tuple(item["column_names"]) for item in schema.get_indexes(table.name) if item.get("unique")}
        expected_fks = {(tuple(c.parent.name for c in constraint.elements), constraint.referred_table.name,
                         tuple(c.column.name for c in constraint.elements)) for constraint in table.foreign_key_constraints}
        actual_fks = {(tuple(fk["constrained_columns"]), fk["referred_table"], tuple(fk["referred_columns"]))
                      for fk in schema.get_foreign_keys(table.name)}
        if actual_unique != expected_unique or actual_fks != expected_fks:
            raise ChatError("invalid_chat_schema")
        if connection.dialect.name == "mysql":
            options = schema.get_table_options(table.name)
            if options.get("mysql_engine") != "InnoDB" or options.get("mysql_collate") != "utf8mb4_bin":
                raise ChatError("invalid_chat_schema")
    return True


def _encoded(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > MAX_JSON_BYTES:
        raise ChatError("chat_storage_limit", 409)
    return encoded


def _decoded(value: str) -> object:
    if not isinstance(value, str) or len(value.encode("utf-8")) > MAX_JSON_BYTES:
        raise ChatError("chat_record_invalid")
    def unique(pairs: list) -> dict:
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError()
            result[key] = item
        return result
    return json.loads(value, object_pairs_hook=unique, parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


class ChatStore:
    def __init__(self, engine: Engine | None = None, *, clock: Callable[[], float] = time.time):
        self.engine = engine if engine is not None else current_engine()
        self.clock = clock

    @contextmanager
    def _transaction(self, owner: str, *, write: bool = False) -> Iterator[Connection]:
        if not isinstance(owner, str) or _OWNER.fullmatch(owner) is None:
            raise ChatError("authentication_required", 401)
        try:
            with self.engine.connect() as connection:
                if write and connection.dialect.name == "sqlite":
                    connection.exec_driver_sql("BEGIN IMMEDIATE")
                else:
                    connection.begin()
                try:
                    validate_chat_extension(connection)
                    # Serialize each owner's creation/turn transitions even
                    # across processes. No network is done under this lock.
                    account = text("SELECT id FROM accounts WHERE id=:owner AND disabled=0" +
                                   (" FOR UPDATE" if write and connection.dialect.name == "mysql" else ""))
                    if connection.execute(account, {"owner": owner}).scalar() is None:
                        raise ChatError("authentication_required", 401)
                    yield connection
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
        except ChatError:
            raise
        except (SQLAlchemyError, OSError, ValueError, TypeError, OverflowError, ValidationError, RecursionError):
            raise ChatError("chat_storage_unavailable") from None

    @staticmethod
    def _row(connection: Connection, owner: str, conversation_id: str) -> dict:
        if not isinstance(conversation_id, str) or _ID.fullmatch(conversation_id) is None:
            raise ChatError("chat_not_found", 404)
        row = connection.execute(select(ConversationRow.__table__).where(
            ConversationRow.id == conversation_id, ConversationRow.owner_id == owner, ConversationRow.deleted_at.is_(None))).mappings().first()
        if row is None:
            raise ChatError("chat_not_found", 404)
        return dict(row)

    @staticmethod
    def _view(connection: Connection, row: dict) -> dict:
        last = connection.execute(select(TurnRow.__table__).where(TurnRow.conversation_id == row["id"])
                                  .order_by(TurnRow.frozen_revision.desc())).mappings().first()
        view = {"schema_version": "chat-session-v1", "id": row["id"], "title": row["title"],
                "revision": row["revision"], "requirements": _decoded(row["requirements_json"]),
                "confirmed_revision": row["confirmed_revision"], "sources": _decoded(row["sources_json"]), "issues": _decoded(row["issues_json"]),
                "messages": _decoded(row["messages_json"]), "created_at": _iso(row["created_at"]),
                "updated_at": _iso(row["updated_at"]), "last_turn": None if last is None else {
                    "operation_id": last["operation_id"], "state": last["state"], "error_code": last["error_code"]}}
        if (row["active_turn_id"] is not None and (last is None or last["id"] != row["active_turn_id"] or last["state"] != "running")
                or row["confirmed_revision"] is not None and row["confirmed_revision"] != row["revision"]):
            raise ChatError("chat_record_invalid")
        return Conversation.model_validate(view).model_dump(mode="json")

    def create(self, owner: str, operation_id: str) -> dict:
        self._operation(operation_id)
        with self._transaction(owner, write=True) as db:
            previous = db.execute(select(ConversationRow.__table__).where(
                ConversationRow.owner_id == owner, ConversationRow.create_operation_id == operation_id)).mappings().first()
            if previous:
                if previous["deleted_at"] is not None:
                    raise ChatError("chat_operation_already_used", 409)
                return self._view(db, dict(previous))
            total = db.execute(text("SELECT COUNT(*) FROM chat_conversations WHERE owner_id=:owner"), {"owner": owner}).scalar_one()
            if total >= MAX_TOTAL_CONVERSATIONS:
                raise ChatError("chat_storage_limit", 409)
            count = db.execute(text("SELECT COUNT(*) FROM chat_conversations WHERE owner_id=:owner AND deleted_at IS NULL"), {"owner": owner}).scalar_one()
            if count >= MAX_CONVERSATIONS:
                raise ChatError("chat_conversation_limit", 409)
            now, identifier = int(self.clock()), str(uuid4())
            db.execute(ConversationRow.__table__.insert().values(
                id=identifier, owner_id=owner, create_operation_id=operation_id, title="新的旅行需求",
                revision=0, confirmed_revision=None, confirm_operation_id=None, active_turn_id=None,
                requirements_json=_encoded(Intent().model_dump(mode="json")), sources_json="[]", issues_json="[]", messages_json="[]",
                created_at=now, updated_at=now, deleted_at=None))
            return self._view(db, self._row(db, owner, identifier))

    def get(self, owner: str, conversation_id: str) -> dict:
        with self._transaction(owner) as db:
            return self._view(db, self._row(db, owner, conversation_id))

    def list(self, owner: str, offset: int = 0, limit: int = 20) -> dict:
        if type(offset) is not int or not 0 <= offset <= 1000 or type(limit) is not int or not 1 <= limit <= 20:
            raise ChatError("chat_invalid_pagination", 422)
        with self._transaction(owner) as db:
            rows = db.execute(select(ConversationRow.__table__).where(ConversationRow.owner_id == owner, ConversationRow.deleted_at.is_(None))
                              .order_by(ConversationRow.updated_at.desc(), ConversationRow.id.desc())
                              .offset(offset).limit(limit + 1)).mappings().all()
            result = {"schema_version": "chat-list-v1", "items": [{"id": row["id"], "title": row["title"],
                      "revision": row["revision"], "updated_at": _iso(row["updated_at"])} for row in rows[:limit]],
                      "next_offset": offset + limit if len(rows) > limit else None}
            return ConversationList.model_validate(result).model_dump(mode="json")

    @staticmethod
    def _operation(operation_id: str) -> None:
        if not isinstance(operation_id, str) or _ID.fullmatch(operation_id) is None:
            raise ChatError("chat_invalid_operation", 422)

    @staticmethod
    def _revision(row: dict, expected_revision: int) -> None:
        if type(expected_revision) is not int or row["revision"] != expected_revision:
            raise ChatError("chat_revision_conflict", 409)

    def begin_turn(self, owner: str, conversation_id: str, operation_id: str, expected_revision: int, message: str) -> dict:
        from app.schemas.chat import SubmitTurn
        self._operation(operation_id)
        SubmitTurn(operation_id=operation_id, expected_revision=expected_revision, text=message)
        with self._transaction(owner, write=True) as db:
            row = self._row(db, owner, conversation_id)
            repeated = db.execute(select(TurnRow.id).where(TurnRow.owner_id == owner, TurnRow.operation_id == operation_id)).first()
            if repeated is not None:
                # Even an uncertain prior reservation is occupied, not a retry.
                raise ChatError("chat_operation_already_used", 409)
            self._revision(row, expected_revision)
            if row["active_turn_id"] is not None:
                raise ChatError("chat_turn_busy", 409)
            count = db.execute(text("SELECT COUNT(*) FROM chat_turns WHERE conversation_id=:id"), {"id": conversation_id}).scalar_one()
            messages = _decoded(row["messages_json"])
            if count >= MAX_TURNS or len(messages) + 2 > 40:
                raise ChatError("chat_turn_limit", 409)
            now, turn_id, revision = int(self.clock()), str(uuid4()), row["revision"] + 1
            messages.append({"id": str(uuid4()), "role": "user", "text": message, "created_at": _iso(now)})
            db.execute(TurnRow.__table__.insert().values(id=turn_id, owner_id=owner, conversation_id=conversation_id,
                operation_id=operation_id, input_hash=hashlib.sha256(_encoded({"revision": expected_revision, "text": message}).encode()).hexdigest(),
                frozen_revision=revision, state="running", error_code=None, created_at=now, updated_at=now))
            db.execute(update(ConversationRow).where(ConversationRow.id == conversation_id).values(
                title=message.strip()[:80] if not messages[:-1] else row["title"], revision=revision,
                confirmed_revision=None, confirm_operation_id=None, active_turn_id=turn_id,
                messages_json=_encoded(messages), updated_at=now))
            return {"turn_id": turn_id, "revision": revision,
                    "conversation": self._view(db, self._row(db, owner, conversation_id))}

    @staticmethod
    def _running(db: Connection, owner: str, row: dict, operation_id: str, revision: int) -> dict:
        turn = db.execute(select(TurnRow.__table__).where(TurnRow.owner_id == owner,
            TurnRow.conversation_id == row["id"], TurnRow.operation_id == operation_id)).mappings().first()
        if (turn is None or turn["state"] != "running" or row["active_turn_id"] != turn["id"]
                or turn["frozen_revision"] != revision or row["revision"] != revision):
            raise ChatError("chat_turn_obsolete", 409)
        return dict(turn)

    def finish_turn(self, owner: str, conversation_id: str, operation_id: str, revision: int,
                    requirements: dict, assistant_text: str, sources: list, issues: list[str] | None = None) -> dict:
        intent = Intent.model_validate(requirements).model_dump(mode="json")
        checked_sources = [Source.model_validate(item).model_dump(mode="json") for item in sources]
        from app.schemas.chat import Selection
        checked_issues = Selection(requirements=intent, sources=checked_sources, issues=issues or []).issues
        if len(checked_sources) > 9 or not isinstance(assistant_text, str) or not 1 <= len(assistant_text) <= 3000:
            raise ChatError("chat_invalid_result", 422)
        with self._transaction(owner, write=True) as db:
            row = self._row(db, owner, conversation_id)
            turn = self._running(db, owner, row, operation_id, revision)
            now, messages = int(self.clock()), _decoded(row["messages_json"])
            messages.append({"id": str(uuid4()), "role": "assistant", "text": assistant_text, "created_at": _iso(now)})
            db.execute(update(TurnRow).where(TurnRow.id == turn["id"]).values(state="succeeded", updated_at=now))
            db.execute(update(ConversationRow).where(ConversationRow.id == conversation_id).values(
                revision=revision + 1, requirements_json=_encoded(intent), sources_json=_encoded(checked_sources), issues_json=_encoded(checked_issues),
                messages_json=_encoded(messages), active_turn_id=None, updated_at=now))
            return self._view(db, self._row(db, owner, conversation_id))

    def fail_turn(self, owner: str, conversation_id: str, operation_id: str, revision: int,
                  error_code: str = "request_failed", *, uncertain: bool = False) -> dict:
        code = error_code if error_code in _ERRORS else "request_failed"
        with self._transaction(owner, write=True) as db:
            row = self._row(db, owner, conversation_id)
            turn = self._running(db, owner, row, operation_id, revision)
            now = int(self.clock())
            db.execute(update(TurnRow).where(TurnRow.id == turn["id"]).values(
                state="uncertain" if uncertain else "failed", error_code=code, updated_at=now))
            db.execute(update(ConversationRow).where(ConversationRow.id == conversation_id).values(
                revision=revision + 1, active_turn_id=None, updated_at=now))
            return self._view(db, self._row(db, owner, conversation_id))

    def cancel_turn(self, owner: str, conversation_id: str, operation_id: str) -> dict:
        self._operation(operation_id)
        with self._transaction(owner, write=True) as db:
            row = self._row(db, owner, conversation_id)
            turn = db.execute(select(TurnRow.__table__).where(TurnRow.owner_id == owner,
                TurnRow.conversation_id == conversation_id, TurnRow.operation_id == operation_id)).mappings().first()
            if turn is None:
                raise ChatError("chat_turn_obsolete", 409)
            if turn["state"] == "cancelled" and row["active_turn_id"] is None:
                return self._view(db, row)
            if turn["state"] != "running" or row["active_turn_id"] != turn["id"]:
                raise ChatError("chat_turn_obsolete", 409)
            now = int(self.clock())
            db.execute(update(TurnRow).where(TurnRow.id == turn["id"]).values(state="cancelled", error_code="cancelled", updated_at=now))
            db.execute(update(ConversationRow).where(ConversationRow.id == conversation_id).values(
                revision=row["revision"] + 1, active_turn_id=None, updated_at=now))
            return self._view(db, self._row(db, owner, conversation_id))

    def confirm(self, owner: str, conversation_id: str, expected_revision: int, operation_id: str) -> dict:
        self._operation(operation_id)
        with self._transaction(owner, write=True) as db:
            row = self._row(db, owner, conversation_id)
            if row["confirm_operation_id"] == operation_id:
                return self._view(db, row)
            self._revision(row, expected_revision)
            if row["active_turn_id"] is not None:
                raise ChatError("chat_turn_busy", 409)
            latest = db.execute(select(TurnRow.__table__).where(TurnRow.conversation_id == conversation_id)
                                .order_by(TurnRow.frozen_revision.desc())).mappings().first()
            if (latest is None or latest["state"] != "succeeded" or latest["frozen_revision"] != row["revision"] - 1
                    or set(_decoded(row["issues_json"])) & {"ambiguous", "unsupported_scope", "unsupported_action"}):
                raise ChatError("chat_requirements_incomplete", 409)
            intent = Intent.model_validate(_decoded(row["requirements_json"]))
            if any(getattr(intent, key) is None for key in ("destination_id", "start_date", "end_date", "travelers", "budget_cny", "accommodation_hint")):
                raise ChatError("chat_requirements_incomplete", 409)
            revision = row["revision"] + 1
            db.execute(update(ConversationRow).where(ConversationRow.id == conversation_id).values(
                revision=revision, confirmed_revision=revision, confirm_operation_id=operation_id, updated_at=int(self.clock())))
            return self._view(db, self._row(db, owner, conversation_id))

    def delete(self, owner: str, conversation_id: str, expected_revision: int) -> None:
        with self._transaction(owner, write=True) as db:
            row = self._row(db, owner, conversation_id)
            self._revision(row, expected_revision)
            if row["active_turn_id"] is not None:
                raise ChatError("chat_turn_busy", 409)
            # Erase private conversation content, but retain receipt tombstones:
            # delete/recreate must not turn an uncertain prior send into a new
            # dispatch permission. There is no transcript in the turn receipts.
            now = int(self.clock())
            db.execute(update(ConversationRow).where(ConversationRow.id == conversation_id, ConversationRow.owner_id == owner).values(
                title="已删除的会话", requirements_json=_encoded(Intent().model_dump(mode="json")), sources_json="[]",
                issues_json="[]", messages_json="[]", confirmed_revision=None, confirm_operation_id=None,
                revision=row["revision"] + 1, deleted_at=now, updated_at=now))
