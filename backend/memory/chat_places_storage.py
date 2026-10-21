"""Account/version bound POI working copies. No network and no runtime DDL.

Completed operations are idempotent only while their frozen result is current.
Running/uncertain receipts are never permission to resend an external request.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import re
import time
from typing import Callable, Iterator
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import BigInteger, Integer, Text, UniqueConstraint, delete, inspect, select, text, update
from sqlalchemy.engine import Connection, Engine
from sqlmodel import SQLModel

from memory.chat_models import CHAT_TABLES, CHAT_VERSION
from memory.chat_places_models import (CHAT_PLACES_TABLES, CHAT_PLACES_VERSION,
                                   ChatPlacesOperationRow as Operation, ChatPlacesWorkspaceRow as Workspace)
from memory.chat_storage import ChatError, ChatStore, _decoded, _encoded

MAX_OPERATIONS = 100
_ERROR_CODE = re.compile(r"[a-z][a-z0-9_]{0,79}")


class ChatPlacesError(ChatError):
    pass


@dataclass(frozen=True)
class SearchBatchCarry:
    """Internal continuity proof for one unchanged v2 conversation turn.

    This is not a request field. It captures the currently visible batch before
    a turn starts; sync still checks the actual persisted row and normal CAS.
    It cannot recover a batch hidden by an earlier requirements/city change.
    """
    conversation_id: str
    intent_revision: int
    workspace_revision: int
    city_id: str
    batch_id: str
    batch_hash: str
    requirements_hash: str

    @classmethod
    def capture(cls, conversation: dict, workspace: dict) -> "SearchBatchCarry | None":
        if (workspace["stale"] or workspace["batch"] is None
                or workspace["intent_revision"] != conversation["revision"]
                or workspace["conversation_id"] != conversation["id"]
                or workspace["last_operation"] is None
                or workspace["last_operation"]["state"] != "succeeded"):
            return None
        return cls(conversation["id"], conversation["revision"], workspace["revision"], workspace["city_id"],
                   workspace["batch"]["id"], hashlib.sha256(_encoded(workspace["batch"]).encode()).hexdigest(),
                   hashlib.sha256(_encoded(conversation["requirements"]).encode()).hexdigest())


def chat_places_tables() -> list:
    return [SQLModel.metadata.tables[name] for name in sorted(CHAT_PLACES_TABLES)]


def validate_chat_places_extension(connection: Connection, *, required: bool = True) -> bool:
    """Independently versioned, exact schema; no ignoring arbitrary extras."""
    schema = inspect(connection)
    tables = set(schema.get_table_names())
    present = CHAT_PLACES_TABLES & tables
    marker = connection.execute(text("SELECT value FROM storage_metadata WHERE `key`='chat_places_version'")).scalar()
    if not present and marker is None:
        if required:
            raise ChatPlacesError("chat_places_migration_required")
        return False
    chat_marker = connection.execute(text("SELECT value FROM storage_metadata WHERE `key`='chat_version'")).scalar()
    if present != CHAT_PLACES_TABLES or marker != CHAT_PLACES_VERSION or not CHAT_TABLES <= tables or chat_marker != CHAT_VERSION:
        raise ChatPlacesError("invalid_chat_places_schema")
    for table in chat_places_tables():
        columns = {item["name"]: item for item in schema.get_columns(table.name)}
        if set(columns) != set(table.c.keys()):
            raise ChatPlacesError("invalid_chat_places_schema")
        for column in table.c:
            actual, expected = columns[column.name], column.type.dialect_impl(connection.dialect)
            if (actual["nullable"] != column.nullable or actual["type"]._type_affinity is not expected._type_affinity
                    or getattr(actual["type"], "length", None) != getattr(column.type, "length", None)):
                raise ChatPlacesError("invalid_chat_places_schema")
            if connection.dialect.name == "mysql" and isinstance(expected, Text) and type(actual["type"]) is not type(expected):
                raise ChatPlacesError("invalid_chat_places_schema")
            if connection.dialect.name == "mysql" and isinstance(expected, Integer):
                from sqlalchemy.dialects.mysql import BIGINT, TINYINT
                if (getattr(actual["type"], "unsigned", False)
                        or isinstance(expected, BigInteger) != isinstance(actual["type"], BIGINT)
                        or isinstance(actual["type"], TINYINT)):
                    raise ChatPlacesError("invalid_chat_places_schema")
        if schema.get_pk_constraint(table.name).get("constrained_columns") != ["id"]:
            raise ChatPlacesError("invalid_chat_places_schema")
        expected_unique = {tuple(c.name for c in item.columns) for item in table.constraints if isinstance(item, UniqueConstraint)}
        actual_unique = {tuple(item["column_names"]) for item in schema.get_unique_constraints(table.name)}
        actual_unique |= {tuple(item["column_names"]) for item in schema.get_indexes(table.name) if item.get("unique")}
        expected_fks = {(tuple(c.parent.name for c in item.elements), item.referred_table.name,
                         tuple(c.column.name for c in item.elements)) for item in table.foreign_key_constraints}
        actual_fks = {(tuple(item["constrained_columns"]), item["referred_table"], tuple(item["referred_columns"]))
                      for item in schema.get_foreign_keys(table.name)}
        if actual_unique != expected_unique or actual_fks != expected_fks:
            raise ChatPlacesError("invalid_chat_places_schema")
        if connection.dialect.name == "mysql":
            options = schema.get_table_options(table.name)
            if options.get("mysql_engine") != "InnoDB" or options.get("mysql_collate") != "utf8mb4_bin":
                raise ChatPlacesError("invalid_chat_places_schema")
    return True


def _checked(view: dict) -> dict:
    from core.schemas.chat_places import ChatPlacesWorkspace
    return ChatPlacesWorkspace.model_validate(view).model_dump(mode="json")


def _input_hash(cid: str, intent_revision: int, expected_revision: int, kind: str, payload: dict) -> str:
    return hashlib.sha256(_encoded({"conversation_id": cid, "intent_revision": intent_revision,
        "expected_revision": expected_revision, "kind": kind, "payload": payload}).encode()).hexdigest()


def erase_conversation_places(db: Connection, owner: str, conversation_id: str, now: int) -> None:
    """Part of the caller's owner-locked conversation-delete transaction.

    Erase private POI/request/result text; retain one-way input hashes and
    operation identities so deletion cannot authorize an old external resend.
    """
    if not validate_chat_places_extension(db, required=False):
        return
    db.execute(delete(Workspace).where(Workspace.owner_id == owner, Workspace.id == conversation_id))
    matching = (Operation.owner_id == owner, Operation.conversation_id == conversation_id)
    db.execute(update(Operation).where(*matching).values(result_json=None, updated_at=now))
    db.execute(update(Operation).where(*matching, Operation.state == "running").values(state="cancelled", error_code="context_deleted"))


class ChatPlacesStore:
    def __init__(self, engine: Engine | None = None, *, clock: Callable[[], float] = time.time):
        # A SQLite engine may only be explicitly injected by compatibility tests.
        # The real new feature never initializes a fallback/default SQLite file.
        if engine is None:
            from memory.mysql_storage import current_mysql_engine
            engine = current_mysql_engine()
        self.engine, self.clock = engine, clock
        self.chat = ChatStore(engine, clock=clock)

    @contextmanager
    def _transaction(self, owner: str, *, write: bool = False) -> Iterator[Connection]:
        with self.chat._transaction(owner, write=write) as connection:
            validate_chat_places_extension(connection)
            yield connection

    def _conversation(self, db: Connection, owner: str, cid: str, *, write: bool = False) -> dict:
        row = self.chat._row(db, owner, cid)
        self.chat._view(db, row)  # Revalidate persisted intent/message structure.
        if write and row["active_turn_id"] is not None:
            raise ChatPlacesError("chat_turn_busy", 409)
        return row

    @staticmethod
    def _row(db: Connection, owner: str, cid: str) -> dict | None:
        row = db.execute(select(Workspace.__table__).where(Workspace.owner_id == owner, Workspace.id == cid)).mappings().first()
        return dict(row) if row else None

    @staticmethod
    def _empty(cid: str, intent_revision: int, *, stale: bool = False) -> dict:
        return {"schema_version": "chat-places-v1", "conversation_id": cid, "intent_revision": intent_revision,
                "revision": 0, "city_id": None, "accommodation": None, "required_places": [], "batch": None,
                "last_operation": None, "clarification": None, "proposed_city_id": None, "request_text": None, "stale": stale}

    def _view(self, db: Connection, owner: str, conversation: dict, row: dict | None) -> dict:
        if row is None:
            return _checked(self._empty(conversation["id"], conversation["revision"], stale=conversation["active_turn_id"] is not None))
        latest = db.execute(select(Operation.__table__).where(Operation.owner_id == owner, Operation.conversation_id == row["id"])
                            .order_by(Operation.frozen_revision.desc())).mappings().first()
        if row["active_operation_id"] is not None and (latest is None or latest["id"] != row["active_operation_id"] or latest["state"] != "running"):
            raise ChatPlacesError("chat_places_record_invalid")
        stale = row["intent_revision"] != conversation["revision"] or conversation["active_turn_id"] is not None
        view = {"schema_version": "chat-places-v1", "conversation_id": row["id"], "intent_revision": row["intent_revision"],
                "revision": row["revision"], "city_id": row["city_id"], "accommodation": _decoded(row["accommodation_json"]),
                "required_places": _decoded(row["required_places_json"]), "batch": _decoded(row["batch_json"]),
                "last_operation": None if latest is None else {"operation_id": latest["operation_id"], "state": latest["state"], "error_code": latest["error_code"]},
                "clarification": row["clarification"], "proposed_city_id": row["proposed_city_id"], "request_text": row["request_text"], "stale": False}
        view = _checked(view)
        if stale:
            # Old confirmed places are explicitly historical/read-only. In
            # particular old candidate IDs never retain confirmation authority.
            view.update(stale=True, batch=None, clarification=None, proposed_city_id=None, request_text=None)
        return view

    def get(self, owner: str, conversation_id: str) -> dict:
        with self._transaction(owner) as db:
            conversation = self._conversation(db, owner, conversation_id)
            return self._view(db, owner, conversation, self._row(db, owner, conversation_id))

    def _ensure(self, db: Connection, owner: str, conversation: dict) -> dict:
        row = self._row(db, owner, conversation["id"])
        if row is not None:
            self._view(db, owner, conversation, row)
            return row
        now = int(self.clock())
        db.execute(Workspace.__table__.insert().values(id=conversation["id"], owner_id=owner,
            intent_revision=conversation["revision"], revision=0, city_id=None, accommodation_json="null",
            required_places_json="[]", batch_json="null", clarification=None, proposed_city_id=None, request_text=None,
            active_operation_id=None, created_at=now, updated_at=now))
        return self._row(db, owner, conversation["id"])

    @staticmethod
    def _versions(conversation: dict, row: dict, intent_revision: int, expected_revision: int, *, stale_allowed: bool = False) -> None:
        if type(intent_revision) is not int or intent_revision != conversation["revision"]:
            raise ChatPlacesError("chat_intent_revision_conflict", 409)
        if type(expected_revision) is not int or expected_revision != row["revision"]:
            raise ChatPlacesError("chat_places_revision_conflict", 409)
        if not stale_allowed and row["intent_revision"] != intent_revision:
            raise ChatPlacesError("chat_places_stale", 409)

    def _reserve(self, db: Connection, owner: str, conversation: dict, row: dict, operation_id: str,
                 intent_revision: int, expected_revision: int, kind: str, payload: dict, *, stale_allowed: bool = False) -> tuple[dict, dict | None]:
        self.chat._operation(operation_id)
        fingerprint = _input_hash(conversation["id"], intent_revision, expected_revision, kind, payload)
        prior = db.execute(select(Operation.__table__).where(Operation.owner_id == owner, Operation.operation_id == operation_id)).mappings().first()
        if prior is not None:
            if prior["input_hash"] != fingerprint or prior["conversation_id"] != conversation["id"]:
                raise ChatPlacesError("chat_places_operation_conflict", 409)
            if prior["state"] == "running" or prior["result_json"] is None:
                raise ChatPlacesError("chat_places_operation_pending", 409)
            result = _checked(_decoded(prior["result_json"]))
            if result["revision"] != row["revision"] or result["intent_revision"] != conversation["revision"]:
                raise ChatPlacesError("chat_places_operation_obsolete", 409)
            return row, result
        self._versions(conversation, row, intent_revision, expected_revision, stale_allowed=stale_allowed)
        if row["active_operation_id"] is not None and not stale_allowed:
            raise ChatPlacesError("chat_places_busy", 409)
        count = db.execute(select(text("COUNT(*)")).select_from(Operation.__table__).where(Operation.conversation_id == row["id"])).scalar_one()
        if count >= MAX_OPERATIONS:
            raise ChatPlacesError("chat_places_operation_limit", 409)
        now, op_id = int(self.clock()), str(uuid4())
        db.execute(Operation.__table__.insert().values(id=op_id, owner_id=owner, conversation_id=row["id"],
            operation_id=operation_id, kind=kind, input_hash=fingerprint, frozen_revision=row["revision"] + 1,
            intent_revision=intent_revision, state="running", error_code=None, result_json=None, created_at=now, updated_at=now))
        return {**row, "_operation_id": op_id}, None

    def _complete(self, db: Connection, owner: str, conversation: dict, row: dict, changes: dict,
                  *, state: str = "succeeded", error_code: str | None = None) -> dict:
        revision, now = row["revision"] + 1, int(self.clock())
        db.execute(update(Workspace).where(Workspace.id == row["id"], Workspace.owner_id == owner).values(
            **changes, revision=revision, active_operation_id=None, updated_at=now))
        db.execute(update(Operation).where(Operation.id == row["_operation_id"]).values(state=state, error_code=error_code, updated_at=now))
        result = self._view(db, owner, conversation, self._row(db, owner, row["id"]))
        db.execute(update(Operation).where(Operation.id == row["_operation_id"]).values(result_json=_encoded(result)))
        return result

    def sync(self, owner: str, conversation_id: str, operation_id: str, intent_revision: int, expected_revision: int,
             *, carry_batch: SearchBatchCarry | None = None) -> dict:
        from core.chat_cities import city_for, legacy_city_id
        with self._transaction(owner, write=True) as db:
            conversation = self._conversation(db, owner, conversation_id, write=True)
            row = self._ensure(db, owner, conversation)
            payload = {} if carry_batch is None else {"carry_batch": asdict(carry_batch)}
            row, replay = self._reserve(db, owner, conversation, row, operation_id, intent_revision, expected_revision, "sync", payload, stale_allowed=True)
            if replay is not None:
                return replay
            raw_city = _decoded(conversation["requirements_json"])["destination_id"]
            city_id = legacy_city_id(raw_city)
            if city_id is None and raw_city is not None:
                try:
                    city_id = city_for(raw_city).id
                except ValueError:
                    city_id = None
            compatible = city_id is not None and city_id == row["city_id"]
            carried_batch = "null"
            if carry_batch is not None:
                stored_batch = _decoded(row["batch_json"])
                # No equality-only ABA restoration, no receipt from another
                # workspace, no concurrent search takeover, no caller-supplied
                # replacement candidate data. Keep the exact persisted JSON.
                if (not compatible or carry_batch.conversation_id != conversation_id
                        or carry_batch.city_id != city_id or row["active_operation_id"] is not None
                        or carry_batch.workspace_revision != row["revision"]
                        or carry_batch.intent_revision != row["intent_revision"]
                        # begin_turn and finish_turn each advance exactly once.
                        or carry_batch.intent_revision + 2 != intent_revision
                        or hashlib.sha256(conversation["requirements_json"].encode()).hexdigest() != carry_batch.requirements_hash
                        or not isinstance(stored_batch, dict) or stored_batch.get("id") != carry_batch.batch_id
                        or hashlib.sha256(_encoded(stored_batch).encode()).hexdigest() != carry_batch.batch_hash):
                    raise ChatPlacesError("chat_places_continuity_conflict", 409)
                carried_batch = row["batch_json"]
            if row["active_operation_id"]:
                db.execute(update(Operation).where(Operation.id == row["active_operation_id"]).values(state="cancelled", error_code="context_changed", updated_at=int(self.clock())))
            return self._complete(db, owner, conversation, row, {"intent_revision": intent_revision, "city_id": city_id,
                "accommodation_json": row["accommodation_json"] if compatible else "null",
                "required_places_json": row["required_places_json"] if compatible else "[]", "batch_json": carried_batch,
                "clarification": None, "proposed_city_id": None, "request_text": None})

    def set_city(self, owner: str, conversation_id: str, operation_id: str, intent_revision: int,
                 expected_revision: int, city_id: str) -> dict:
        from core.chat_cities import city_for
        city_for(city_id)
        with self._transaction(owner, write=True) as db:
            conversation = self._conversation(db, owner, conversation_id, write=True)
            row = self._ensure(db, owner, conversation)
            row, replay = self._reserve(db, owner, conversation, row, operation_id, intent_revision, expected_revision, "set_city", {"city_id": city_id})
            if replay is not None:
                return replay
            same = city_id == row["city_id"]
            return self._complete(db, owner, conversation, row, {"city_id": city_id, "batch_json": "null",
                "accommodation_json": row["accommodation_json"] if same else "null",
                "required_places_json": row["required_places_json"] if same else "[]",
                "clarification": None, "proposed_city_id": None, "request_text": None})

    def begin_operation(self, owner: str, conversation_id: str, operation_id: str, intent_revision: int,
                        expected_revision: int, kind: str, request_payload: dict) -> dict:
        if kind not in {"search", "ask"} or not isinstance(request_payload, dict):
            raise ChatPlacesError("chat_places_invalid_operation", 422)
        from core.schemas.chat_places import Ask, Search
        arguments = {**request_payload, "operation_id": operation_id, "expected_intent_revision": intent_revision,
                     "expected_revision": expected_revision, "action": kind}
        checked = (Search if kind == "search" else Ask).model_validate(arguments)
        request_payload = checked.model_dump(exclude={"action", "operation_id", "expected_intent_revision", "expected_revision"})
        with self._transaction(owner, write=True) as db:
            conversation = self._conversation(db, owner, conversation_id, write=True)
            row = self._ensure(db, owner, conversation)
            row, replay = self._reserve(db, owner, conversation, row, operation_id, intent_revision, expected_revision, kind, request_payload)
            if replay is not None:
                return {"revision": replay["revision"], "operation_id": operation_id, "workspace": replay, "dispatch": False}
            if kind == "search" and row["city_id"] is None:
                raise ChatPlacesError("chat_places_city_required", 409)
            revision = row["revision"] + 1
            db.execute(update(Workspace).where(Workspace.id == row["id"]).values(revision=revision,
                active_operation_id=row["_operation_id"], batch_json="null", clarification=None, proposed_city_id=None,
                request_text=request_payload.get("text"), updated_at=int(self.clock())))
            return {"revision": revision, "operation_id": operation_id,
                    "workspace": self._view(db, owner, conversation, self._row(db, owner, row["id"])), "dispatch": True}

    def _running(self, db: Connection, owner: str, conversation: dict, operation_id: str, revision: int, intent_revision: int) -> tuple[dict, dict]:
        row = self._row(db, owner, conversation["id"])
        if row is None:
            raise ChatPlacesError("chat_places_operation_obsolete", 409)
        self._versions(conversation, row, intent_revision, revision)
        operation = db.execute(select(Operation.__table__).where(Operation.owner_id == owner,
            Operation.conversation_id == row["id"], Operation.operation_id == operation_id)).mappings().first()
        if (operation is None or operation["state"] != "running" or row["active_operation_id"] != operation["id"]
                or operation["frozen_revision"] != revision or operation["intent_revision"] != intent_revision):
            raise ChatPlacesError("chat_places_operation_obsolete", 409)
        return {**row, "_operation_id": operation["id"]}, dict(operation)

    def check_running(self, owner: str, conversation_id: str, operation_id: str, revision: int, intent_revision: int) -> dict:
        """Execution-time recheck, including cancel-before-task-registration gap."""
        with self._transaction(owner) as db:
            conversation = self._conversation(db, owner, conversation_id, write=True)
            row, _ = self._running(db, owner, conversation, operation_id, revision, intent_revision)
            return self._view(db, owner, conversation, row)

    def finish_operation(self, owner: str, conversation_id: str, operation_id: str, revision: int, intent_revision: int,
                         *, batch: dict | None = None, clarification: str | None = None,
                         proposed_city_id: str | None = None, request_text: str | None = None, error_code: str | None = None) -> dict:
        from core.schemas.chat_places import Batch
        checked_batch = Batch.model_validate(batch).model_dump(mode="json") if batch is not None else None
        if error_code is not None and (not isinstance(error_code, str) or _ERROR_CODE.fullmatch(error_code) is None):
            error_code = "request_failed"
        with self._transaction(owner, write=True) as db:
            conversation = self._conversation(db, owner, conversation_id, write=True)
            row, operation = self._running(db, owner, conversation, operation_id, revision, intent_revision)
            if checked_batch is not None and checked_batch["city_id"] != row["city_id"]:
                raise ChatPlacesError("chat_places_city_mismatch", 409)
            if checked_batch is not None and operation["kind"] == "search" and operation["input_hash"] != _input_hash(
                    conversation_id, intent_revision, operation["frozen_revision"] - 1, "search",
                    {"keyword": checked_batch["keyword"], "role": checked_batch["role"]}):
                raise ChatPlacesError("chat_places_batch_mismatch", 409)
            if error_code is not None and (batch is not None or clarification is not None or proposed_city_id is not None):
                raise ChatPlacesError("chat_places_invalid_result", 422)
            state = "uncertain" if error_code == "request_uncertain" else "failed" if error_code else "succeeded"
            return self._complete(db, owner, conversation, row, {"batch_json": _encoded(checked_batch),
                "clarification": clarification, "proposed_city_id": proposed_city_id, "request_text": request_text}, state=state, error_code=error_code)

    def cancel(self, owner: str, conversation_id: str, operation_id: str, expected_revision: int) -> dict:
        self.chat._operation(operation_id)
        with self._transaction(owner, write=True) as db:
            conversation = self._conversation(db, owner, conversation_id)
            row = self._row(db, owner, conversation_id)
            operation = db.execute(select(Operation.__table__).where(Operation.owner_id == owner,
                Operation.conversation_id == conversation_id, Operation.operation_id == operation_id)).mappings().first()
            if row is None or operation is None or type(expected_revision) is not int:
                raise ChatPlacesError("chat_places_operation_obsolete", 409)
            if (operation["state"] == "cancelled" and operation["result_json"] is not None
                    and row["revision"] == expected_revision + 1 and operation["frozen_revision"] == expected_revision):
                return self._view(db, owner, conversation, row)
            if (row["revision"] != expected_revision or operation["state"] != "running"
                    or row["active_operation_id"] != operation["id"] or operation["frozen_revision"] != expected_revision):
                raise ChatPlacesError("chat_places_operation_obsolete", 409)
            return self._complete(db, owner, conversation, {**row, "_operation_id": operation["id"]},
                                  {"batch_json": "null", "clarification": None, "proposed_city_id": None, "request_text": None},
                                  state="cancelled", error_code="cancelled")

    def confirm(self, owner: str, conversation_id: str, operation_id: str, intent_revision: int,
                expected_revision: int, batch_id: str, place_id: str, role: str) -> dict:
        if role not in {"accommodation", "required"}:
            raise ChatPlacesError("chat_places_invalid_role", 422)
        with self._transaction(owner, write=True) as db:
            conversation = self._conversation(db, owner, conversation_id, write=True)
            row = self._ensure(db, owner, conversation)
            row, replay = self._reserve(db, owner, conversation, row, operation_id, intent_revision, expected_revision,
                                        "confirm", {"batch_id": batch_id, "place_id": place_id, "role": role})
            if replay is not None:
                return replay
            view = self._view(db, owner, conversation, row)
            batch = view["batch"]
            if batch is None or batch["id"] != batch_id or batch["role"] != role or batch["city_id"] != row["city_id"]:
                raise ChatPlacesError("chat_places_batch_obsolete", 409)
            place = next((item for item in batch["places"] if item["id"] == place_id), None)
            if place is None:
                raise ChatPlacesError("chat_places_unknown_place", 409)
            known = [*view["required_places"], *([view["accommodation"]] if view["accommodation"] is not None else [])]
            if any(item["id"] == place_id and item != place for item in known):
                raise ChatPlacesError("chat_places_snapshot_conflict", 409)
            changes = {"clarification": None, "proposed_city_id": None, "request_text": None}
            if role == "accommodation":
                changes["accommodation_json"] = _encoded(place)
            else:
                if any(item["id"] == place_id for item in view["required_places"]):
                    raise ChatPlacesError("chat_places_duplicate_place", 409)
                if len(view["required_places"]) >= 6:
                    raise ChatPlacesError("chat_places_required_limit", 409)
                changes["required_places_json"] = _encoded([*view["required_places"], place])
            return self._complete(db, owner, conversation, row, changes)

    def remove(self, owner: str, conversation_id: str, operation_id: str, intent_revision: int,
               expected_revision: int, place_id: str, role: str) -> dict:
        if role not in {"accommodation", "required"}:
            raise ChatPlacesError("chat_places_invalid_role", 422)
        with self._transaction(owner, write=True) as db:
            conversation = self._conversation(db, owner, conversation_id, write=True)
            row = self._ensure(db, owner, conversation)
            row, replay = self._reserve(db, owner, conversation, row, operation_id, intent_revision, expected_revision, "remove", {"place_id": place_id, "role": role})
            if replay is not None:
                return replay
            view = self._view(db, owner, conversation, row)
            if role == "accommodation":
                if view["accommodation"] is None or view["accommodation"]["id"] != place_id:
                    raise ChatPlacesError("chat_places_unknown_place", 409)
                changes = {"accommodation_json": "null"}
            else:
                if not any(item["id"] == place_id for item in view["required_places"]):
                    raise ChatPlacesError("chat_places_unknown_place", 409)
                changes = {"required_places_json": _encoded([item for item in view["required_places"] if item["id"] != place_id])}
            return self._complete(db, owner, conversation, row, changes)
