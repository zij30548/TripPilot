"""Private identity tables; archive ownership references accounts, never client data."""
from sqlalchemy import BigInteger, Column
from sqlmodel import Field, SQLModel

MYSQL_TABLE_OPTIONS = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_bin"}


class AccountRow(SQLModel, table=True):
    __tablename__ = "accounts"
    __table_args__ = MYSQL_TABLE_OPTIONS
    id: str = Field(primary_key=True, max_length=32)
    username: str = Field(unique=True, index=True, max_length=48)
    password_hash: str = Field(max_length=256)
    disabled: bool = False
    created_at: int = Field(sa_column=Column(BigInteger, nullable=False))


class SessionRow(SQLModel, table=True):
    __tablename__ = "auth_sessions"
    __table_args__ = MYSQL_TABLE_OPTIONS
    id: str = Field(primary_key=True, max_length=32)
    token_hash: str = Field(unique=True, index=True, max_length=64)
    user_id: str = Field(foreign_key="accounts.id", index=True, max_length=32)
    csrf_token: str = Field(max_length=43)
    expires_at: int = Field(sa_column=Column(BigInteger, nullable=False, index=True))
    created_at: int = Field(sa_column=Column(BigInteger, nullable=False))


class LoginBucketRow(SQLModel, table=True):
    __tablename__ = "login_buckets"
    __table_args__ = MYSQL_TABLE_OPTIONS
    id: str = Field(primary_key=True, max_length=64)
    window_start: int = Field(sa_column=Column(BigInteger, nullable=False))
    attempts: int


class InvitationRow(SQLModel, table=True):
    __tablename__ = "invitations"
    __table_args__ = MYSQL_TABLE_OPTIONS
    id: str = Field(primary_key=True, max_length=32)
    token_hash: str = Field(unique=True, max_length=64)
    created_at: int = Field(sa_column=Column(BigInteger, nullable=False))
    expires_at: int = Field(sa_column=Column(BigInteger, nullable=False))
    revoked: bool = False
    consumed_by: str | None = Field(default=None, foreign_key="accounts.id", max_length=32)
    consumed_at: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
