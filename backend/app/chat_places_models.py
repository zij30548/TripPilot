"""Explicit chat POI preparation extension; imports never issue DDL."""
from sqlalchemy import BigInteger, Column, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.chat_models import JSON_TEXT, OPTIONS

CHAT_PLACES_VERSION = "chat-places-storage-v1"
CHAT_PLACES_TABLES = frozenset({"chat_places_workspaces", "chat_places_operations"})


class ChatPlacesWorkspaceRow(SQLModel, table=True):
    __tablename__ = "chat_places_workspaces"
    __table_args__ = OPTIONS
    id: str = Field(primary_key=True, foreign_key="chat_conversations.id", max_length=36)
    owner_id: str = Field(foreign_key="accounts.id", index=True, max_length=32)
    intent_revision: int
    revision: int
    city_id: str | None = Field(default=None, max_length=6)
    accommodation_json: str = Field(sa_column=Column(JSON_TEXT, nullable=False))
    required_places_json: str = Field(sa_column=Column(JSON_TEXT, nullable=False))
    batch_json: str = Field(sa_column=Column(JSON_TEXT, nullable=False))
    clarification: str | None = Field(default=None, max_length=40)
    proposed_city_id: str | None = Field(default=None, max_length=6)
    request_text: str | None = Field(default=None, sa_column=Column(JSON_TEXT, nullable=True))
    active_operation_id: str | None = Field(default=None, max_length=36)
    created_at: int = Field(sa_column=Column(BigInteger, nullable=False))
    updated_at: int = Field(sa_column=Column(BigInteger, nullable=False))


class ChatPlacesOperationRow(SQLModel, table=True):
    __tablename__ = "chat_places_operations"
    __table_args__ = (UniqueConstraint("owner_id", "operation_id", name="uq_chat_places_owner_operation"), OPTIONS)
    id: str = Field(primary_key=True, max_length=36)
    owner_id: str = Field(foreign_key="accounts.id", index=True, max_length=32)
    conversation_id: str = Field(foreign_key="chat_conversations.id", index=True, max_length=36)
    operation_id: str = Field(max_length=36)
    kind: str = Field(max_length=24)
    input_hash: str = Field(max_length=64)
    frozen_revision: int
    intent_revision: int
    state: str = Field(max_length=16)
    error_code: str | None = Field(default=None, max_length=80)
    result_json: str | None = Field(default=None, sa_column=Column(JSON_TEXT, nullable=True))
    created_at: int = Field(sa_column=Column(BigInteger, nullable=False))
    updated_at: int = Field(sa_column=Column(BigInteger, nullable=False))
