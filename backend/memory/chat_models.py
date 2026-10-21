"""Explicitly migrated chat extension; importing models never creates tables."""
from sqlalchemy import BigInteger, Column, Text, UniqueConstraint
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlmodel import Field, SQLModel

OPTIONS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_bin"}
JSON_TEXT = Text().with_variant(MEDIUMTEXT(), "mysql")
CHAT_VERSION = "chat-storage-v1"
CHAT_TABLES = frozenset({"chat_conversations", "chat_turns"})


class ConversationRow(SQLModel, table=True):
    __tablename__ = "chat_conversations"
    __table_args__ = (UniqueConstraint("owner_id", "create_operation_id", name="uq_chat_owner_create"), OPTIONS)
    id: str = Field(primary_key=True, max_length=36)
    owner_id: str = Field(foreign_key="accounts.id", index=True, max_length=32)
    create_operation_id: str = Field(max_length=36)
    title: str = Field(max_length=80)
    revision: int
    confirmed_revision: int | None = None
    confirm_operation_id: str | None = Field(default=None, max_length=36)
    active_turn_id: str | None = Field(default=None, max_length=36)
    requirements_json: str = Field(sa_column=Column(JSON_TEXT, nullable=False))
    sources_json: str = Field(sa_column=Column(JSON_TEXT, nullable=False))
    issues_json: str = Field(sa_column=Column(JSON_TEXT, nullable=False))
    messages_json: str = Field(sa_column=Column(JSON_TEXT, nullable=False))
    created_at: int = Field(sa_column=Column(BigInteger, nullable=False))
    updated_at: int = Field(sa_column=Column(BigInteger, nullable=False))
    deleted_at: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))


class TurnRow(SQLModel, table=True):
    __tablename__ = "chat_turns"
    __table_args__ = (UniqueConstraint("owner_id", "operation_id", name="uq_chat_owner_turn"), OPTIONS)
    id: str = Field(primary_key=True, max_length=36)
    owner_id: str = Field(foreign_key="accounts.id", index=True, max_length=32)
    conversation_id: str = Field(foreign_key="chat_conversations.id", index=True, max_length=36)
    operation_id: str = Field(max_length=36)
    input_hash: str = Field(max_length=64)
    frozen_revision: int
    state: str = Field(max_length=16)
    error_code: str | None = Field(default=None, max_length=64)
    created_at: int = Field(sa_column=Column(BigInteger, nullable=False))
    updated_at: int = Field(sa_column=Column(BigInteger, nullable=False))
