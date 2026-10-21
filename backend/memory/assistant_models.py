"""Explicit assistant-storage-v1 tables, independent of archive versions."""
from sqlalchemy import BigInteger, Column, UniqueConstraint
from sqlmodel import Field, SQLModel

from memory.chat_models import JSON_TEXT, OPTIONS

ASSISTANT_VERSION = "assistant-storage-v1"
ASSISTANT_TABLES = frozenset({"assistant_tasks", "assistant_preferences"})


class AssistantTaskRow(SQLModel, table=True):
    __tablename__ = "assistant_tasks"
    __table_args__ = (UniqueConstraint("owner_id", "operation_id", name="uq_assistant_owner_operation"),
                      UniqueConstraint("conversation_id", "sequence", name="uq_assistant_conversation_sequence"), OPTIONS)
    id: str = Field(primary_key=True, max_length=36)
    owner_id: str = Field(foreign_key="accounts.id", index=True, max_length=32)
    conversation_id: str = Field(foreign_key="chat_conversations.id", index=True, max_length=36)
    operation_id: str = Field(max_length=36)
    conversation_revision: int
    sequence: int
    revision: int
    state: str = Field(max_length=16)
    checkpoint_json: str = Field(sa_column=Column(JSON_TEXT, nullable=False))
    created_at: int = Field(sa_column=Column(BigInteger, nullable=False))
    updated_at: int = Field(sa_column=Column(BigInteger, nullable=False))


class AssistantPreferenceRow(SQLModel, table=True):
    __tablename__ = "assistant_preferences"
    __table_args__ = (UniqueConstraint("owner_id", "key", name="uq_assistant_owner_preference"), OPTIONS)
    id: str = Field(primary_key=True, max_length=36)
    owner_id: str = Field(foreign_key="accounts.id", index=True, max_length=32)
    key: str = Field(max_length=40)
    value: str = Field(max_length=500)
    version: int
    source: str = Field(max_length=16)
    consent: bool
    created_at: int = Field(sa_column=Column(BigInteger, nullable=False))
    updated_at: int = Field(sa_column=Column(BigInteger, nullable=False))
