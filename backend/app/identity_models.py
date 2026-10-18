"""Private identity tables; archive ownership references accounts, never client data."""
from sqlmodel import Field, SQLModel


class AccountRow(SQLModel, table=True):
    __tablename__ = "accounts"
    id: str = Field(primary_key=True)
    username: str = Field(unique=True, index=True)
    password_hash: str
    disabled: bool = False
    created_at: int


class SessionRow(SQLModel, table=True):
    __tablename__ = "auth_sessions"
    id: str = Field(primary_key=True)
    token_hash: str = Field(unique=True, index=True)
    user_id: str = Field(foreign_key="accounts.id", index=True)
    csrf_token: str
    expires_at: int = Field(index=True)
    created_at: int


class LoginBucketRow(SQLModel, table=True):
    __tablename__ = "login_buckets"
    id: str = Field(primary_key=True)
    window_start: int
    attempts: int
