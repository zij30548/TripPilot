"""Request-scoped SQLModel transactions; no SQL outside this module."""
import json
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import Depends
from sqlalchemy import UniqueConstraint
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Field, Session, SQLModel, select

from app.integrations.qwen import strict_json
from app.schemas.saved_trip import MAX_BODY_BYTES, SaveTrip, SavedDetail, SavedList, SavedSnapshot, SavedSummary, snapshot_adapter

from app.storage import DEFAULT_PATH, StorageError as ArchiveError, database_path, engine_for


class SavedTripRow(SQLModel, table=True):
    __tablename__ = "saved_trips"
    __table_args__ = (UniqueConstraint("owner_id", "operation_id", name="uq_saved_trips_owner_operation"),)
    id: str = Field(primary_key=True)
    owner_id: str | None = Field(default=None, foreign_key="accounts.id", index=True)
    operation_id: str
    name: str
    saved_at: str = Field(index=True)
    schema_version: str
    start_date: str
    end_date: str
    transport_mode: str
    status: str
    snapshot_json: str


def get_session(path: Annotated[Path, Depends(database_path)]) -> Iterator[Session]:
    try:
        with Session(engine_for(str(path)), expire_on_commit=False) as session:
            yield session
    except (SQLAlchemyError, OSError):
        raise ArchiveError("storage_unavailable") from None


def canonical(snapshot: SavedSnapshot) -> str:
    return json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def summary(row: SavedTripRow) -> SavedSummary:
    try:
        return SavedSummary.model_validate_json(json.dumps({k: getattr(row, k) for k in SavedSummary.model_fields}))
    except ValueError:
        raise ArchiveError("invalid_record", 422) from None


def detail(row: SavedTripRow) -> SavedDetail:
    try:
        if len(row.snapshot_json.encode()) > MAX_BODY_BYTES:
            raise ValueError("oversized")
        strict_json(row.snapshot_json)
        snapshot = snapshot_adapter.validate_json(row.snapshot_json, strict=True)
        result = summary(row)
        request = snapshot.schedule.request
        if (result.schema_version != snapshot.schema_version or result.start_date != request.start_date.isoformat()
                or result.end_date != request.end_date.isoformat() or result.transport_mode != request.transport_mode
                or result.status != snapshot.schedule.status):
            raise ValueError("metadata mismatch")
        return SavedDetail(**result.model_dump(), snapshot=snapshot)
    except (ValueError, RecursionError, TypeError):
        raise ArchiveError("invalid_record", 422) from None


def save(session: Session, request: SaveTrip, owner_id: str) -> SavedDetail:
    if not isinstance(owner_id, str) or not owner_id:
        raise ArchiveError("authentication_required", 401)
    content = canonical(request.snapshot)
    def existing() -> SavedTripRow | None:
        return session.exec(select(SavedTripRow).where(SavedTripRow.owner_id == owner_id, SavedTripRow.operation_id == request.operation_id)).first()
    def retry(row: SavedTripRow) -> SavedDetail:
        if row.name != request.name or row.snapshot_json != content:
            raise ArchiveError("operation_conflict", 409)
        return detail(row)
    try:
        previous = existing()
        if previous:
            return retry(previous)
        sr = request.snapshot.schedule
        row = SavedTripRow(id=uuid4().hex, owner_id=owner_id, operation_id=request.operation_id, name=request.name,
                           saved_at=datetime.now(timezone.utc).isoformat(), schema_version=request.snapshot.schema_version,
                           start_date=sr.request.start_date.isoformat(), end_date=sr.request.end_date.isoformat(),
                           transport_mode=sr.request.transport_mode, status=sr.status, snapshot_json=content)
        session.add(row)
        # Prepare the response before COMMIT; success is returned only after COMMIT.
        result = detail(row)
        session.commit()
        return result
    except IntegrityError:
        session.rollback()
        previous = existing()  # concurrent same-operation writer committed first
        if previous:
            return retry(previous)
        raise ArchiveError("storage_unavailable") from None
    except SQLAlchemyError:
        session.rollback()
        raise ArchiveError("storage_unavailable") from None


def listing(session: Session, offset: int, limit: int, owner_id: str) -> SavedList:
    if not isinstance(owner_id, str) or not owner_id:
        raise ArchiveError("authentication_required", 401)
    rows = session.exec(select(SavedTripRow).where(SavedTripRow.owner_id == owner_id).order_by(SavedTripRow.saved_at.desc(), SavedTripRow.id.desc()).offset(offset).limit(limit + 1)).all()
    # Also validate stored contents on list reads, but transmit summaries only.
    items = [SavedSummary(**detail(row).model_dump(exclude={"snapshot"})) for row in rows[:limit]]
    return SavedList(items=items, offset=offset, limit=limit, has_more=len(rows) > limit)


def get_record(session: Session, record_id: str, owner_id: str) -> SavedDetail:
    if not isinstance(owner_id, str) or not owner_id:
        raise ArchiveError("authentication_required", 401)
    row = session.exec(select(SavedTripRow).where(SavedTripRow.id == record_id, SavedTripRow.owner_id == owner_id)).first()
    if row is None:
        raise ArchiveError("not_found", 404)
    return detail(row)
