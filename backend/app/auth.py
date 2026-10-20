"""Opaque, revocable server sessions and an early ASGI authorization gate.

This module never consumes ASGI receive: unauthenticated media is rejected before
upload/decoding, leaving the media routes' single receive owner unchanged.
"""
import hashlib
import hmac
import re
import secrets
import time
from datetime import datetime, timezone
from http.cookies import CookieError, SimpleCookie
from threading import BoundedSemaphore
from typing import Annotated, Any
from uuid import uuid4

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import delete
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import BACKEND_ENV_FILE, normalize_map_page_origin
from app.identity_models import AccountRow, InvitationRow, LoginBucketRow, SessionRow
from app.mysql_storage import current_mysql_engine, mysql_configured
from app.storage import StorageError, database_path, engine_for
from app.usage_runtime import bind_usage_context, reset_usage_context

TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")
USERNAME_PATTERN = re.compile(r"[a-z0-9_.-]{3,48}")
PASSWORD_HASHER = PasswordHasher()  # Argon2id, library's RFC 9106 low-memory profile.
_DUMMY_HASH = PASSWORD_HASHER.hash(secrets.token_urlsafe(32))
_HASH_CAPACITY = BoundedSemaphore(2)
PUBLIC_ENDPOINTS = frozenset({("GET", "/health"), ("GET", "/auth/session"), ("POST", "/auth/login"), ("POST", "/auth/register")})


def identity_engine() -> Engine:
    """No fallback after a MySQL selection or validation failure."""
    return current_mysql_engine() if mysql_configured() else engine_for(str(database_path()))


class AuthSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_ENV_FILE, extra="ignore", env_file_encoding="utf-8")
    auth_cookie_secure: bool = True
    auth_allowed_origins: tuple[str, ...] = ("http://localhost:3000", "http://127.0.0.1:3000")
    auth_session_seconds: int = Field(default=28_800, ge=60, le=604_800)

    @field_validator("auth_allowed_origins", mode="before")
    @classmethod
    def origins(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, (tuple, list)) or not 1 <= len(value) <= 16:
            raise ValueError("AUTH_ALLOWED_ORIGINS must be a bounded JSON origin list")
        result = tuple(normalize_map_page_origin(origin) for origin in value)
        if len(set(result)) != len(result):
            raise ValueError("AUTH_ALLOWED_ORIGINS cannot repeat origins")
        return result

    @property
    def cookie_name(self) -> str:
        return "__Host-trippilot_session" if self.auth_cookie_secure else "trippilot_session"

    @property
    def preauth_cookie_name(self) -> str:
        return "__Host-trippilot_preauth" if self.auth_cookie_secure else "trippilot_preauth"


def get_auth_settings() -> AuthSettings:
    return AuthSettings()


def now_seconds() -> int:
    return int(time.time())


def auth_error(code: str, status: int, message: str = "身份验证未完成，请重新登录或稍后重试。") -> HTTPException:
    return HTTPException(status, detail={"code": code, "message": message})


def cookie_value(scope: Scope, name: str) -> str | None:
    # Reject duplicate names rather than let header parsing choose an identity.
    headers = Headers(scope=scope)
    raw = "; ".join(headers.getlist("cookie"))
    if len(raw) > 16_384 or len(re.findall(r"(?:^|;)\s*" + re.escape(name) + r"\s*=", raw)) != 1:
        return None
    try:
        values = SimpleCookie(raw)
        result = values[name].value
        return result if TOKEN_PATTERN.fullmatch(result) else None
    except (CookieError, ValueError, KeyError):
        return None


def resolve_identity(scope: Scope) -> tuple[AccountRow, SessionRow] | None:
    token = cookie_value(scope, get_auth_settings().cookie_name)
    if token is None:
        return None
    digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    with Session(identity_engine(), expire_on_commit=False) as db:
        session = db.exec(select(SessionRow).where(SessionRow.token_hash == digest)).first()
        if session is None or session.expires_at <= now_seconds():
            return None
        account = db.get(AccountRow, session.user_id)
        if account is None or account.disabled:
            return None
        return account, session


def allowed_origin(scope: Scope) -> bool:
    headers = Headers(scope=scope)
    values = headers.getlist("origin")
    return (len(values) == 1 and values[0] in get_auth_settings().auth_allowed_origins
            and headers.get("sec-fetch-site", "same-origin") not in {"cross-site", "none"})


def validate_csrf(scope: Scope, session: SessionRow | None = None) -> bool:
    if not allowed_origin(scope):
        return False
    values = Headers(scope=scope).getlist("x-csrf-token")
    supplied = values[0] if len(values) == 1 else ""
    expected = session.csrf_token if session else cookie_value(scope, get_auth_settings().preauth_cookie_name)
    return bool(expected and TOKEN_PATTERN.fullmatch(supplied) and hmac.compare_digest(expected, supplied))


class AuthenticationMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def private_send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = [(key, value) for key, value in message.get("headers", []) if key.lower() != b"cache-control"]
                vary_values = [value.decode("latin-1") for key, value in headers if key.lower() == b"vary"]
                headers = [(key, value) for key, value in headers if key.lower() != b"vary"]
                vary = ", ".join(vary_values + ["Cookie"])
                message = {**message, "headers": headers + [(b"cache-control", b"no-store"), (b"vary", vary.encode("latin-1"))]}
            await send(message)
        if scope["type"] != "http" or scope["method"] == "OPTIONS" or (scope["method"], scope["path"]) in PUBLIC_ENDPOINTS:
            await self.app(scope, receive, private_send if scope["type"] == "http" and scope["path"] != "/health" else send)
            return
        try:
            identity = await run_in_threadpool(resolve_identity, scope)
            if identity is None:
                response = JSONResponse({"detail": {"code": "authentication_required", "message": "请先登录邀请账号。"}}, 401)
            elif scope["method"] not in {"GET", "HEAD"} and not validate_csrf(scope, identity[1]):
                response = JSONResponse({"detail": {"code": "csrf_invalid", "message": "请求身份已变化，请刷新身份后重试。"}}, 403)
            else:
                scope.setdefault("state", {}).update(user=identity[0], session=identity[1])
                response = None
        except (StorageError, SQLAlchemyError, OSError, ValueError):
            response = JSONResponse({"detail": {"code": "identity_unavailable", "message": "身份服务暂不可用。"}}, 503)
        if response is None:
            token = bind_usage_context(identity[0].id, scope["path"])
            try:
                await self.app(scope, receive, private_send)
            finally:
                reset_usage_context(token)
            return
        # No request body is read and no route dependency is entered on rejection.
        await response(scope, receive, private_send)


def current_user(request: Request) -> AccountRow:
    account = getattr(request.state, "user", None)
    if not isinstance(account, AccountRow):
        raise auth_error("authentication_required", 401)
    return account


CurrentUser = Annotated[AccountRow, Depends(current_user)]


def user_view(account: AccountRow, session: SessionRow) -> dict[str, Any]:
    return {"authenticated": True, "user": {"id": account.id, "username": account.username},
            "csrf_token": session.csrf_token, "expires_at": datetime.fromtimestamp(session.expires_at, timezone.utc).isoformat(),
            "session_id": session.id}


def anonymous_view(csrf: str) -> dict[str, Any]:
    return {"authenticated": False, "user": None, "csrf_token": csrf, "expires_at": None, "session_id": None}


def reserve_login_attempt(username: str) -> None:
    """Persistent bounded username/global windows, reserved before password work.

    No forwarded IP is trusted. SQLite serializes reservation across workers;
    global admission also bounds attacker-chosen username bucket growth.
    """
    reserve_identity_attempt("login", username)


def reserve_identity_attempt(kind: str, username: str) -> None:
    """Global row first: serialize admission and bound arbitrary subject growth.

    MySQL uses an InnoDB locking read, SQLite retains BEGIN IMMEDIATE. Password
    hashing happens later, outside this short persistent limiter transaction.
    """
    now = now_seconds()
    with Session(identity_engine()) as db:
        mysql = db.bind.dialect.name == "mysql"
        if not mysql:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        plans = ([("global", 300, 30), ("username:" + username, 900, 5)] if kind == "login"
                 else [("registration:global", 900, 10), ("registration:username:" + username, 3600, 3)])
        buckets: list[LoginBucketRow] = []
        for key, seconds, maximum in plans:
            identifier = hashlib.sha256(key.encode()).hexdigest()
            if mysql:
                create = mysql_insert(LoginBucketRow).values(id=identifier, window_start=now, attempts=0)
                db.execute(create.on_duplicate_key_update(id=create.inserted.id))
                bucket = db.exec(select(LoginBucketRow).where(LoginBucketRow.id == identifier).with_for_update()).one()
            else:
                bucket = db.get(LoginBucketRow, identifier)
            if bucket is None:
                bucket = LoginBucketRow(id=identifier, window_start=now, attempts=0)
            elif now - bucket.window_start >= seconds:
                bucket.window_start, bucket.attempts = now, 0
            if bucket.attempts >= maximum:
                code = "login_rate_limited" if kind == "login" else "registration_rate_limited"
                raise auth_error(code, 429, "尝试过多，请稍后主动重试。")
            buckets.append(bucket)
        for bucket in buckets:
            bucket.attempts += 1
            db.add(bucket)
        db.exec(delete(LoginBucketRow).where(LoginBucketRow.window_start < now - 86_400))
        db.commit()


def login(username: str, password: str, old_token: str | None) -> tuple[dict[str, Any], str]:
    reserve_login_attempt(username)
    if not _HASH_CAPACITY.acquire(blocking=False):
        raise auth_error("login_busy", 429, "登录验证忙，请稍后主动重试。")
    try:
        with Session(identity_engine(), expire_on_commit=False) as db:
            account = db.exec(select(AccountRow).where(AccountRow.username == username)).first()
            encoded = account.password_hash if account is not None else _DUMMY_HASH
            try:
                valid = PASSWORD_HASHER.verify(encoded, password)
            except (VerificationError, InvalidHashError):
                valid = False
            if not valid or account is None or account.disabled:
                raise auth_error("invalid_credentials", 401, "账号或密码不正确，或账号不可用。")
            mysql = db.bind.dialect.name == "mysql"
            if mysql:
                # Password work is intentionally outside the row lock. Once
                # acquired, re-read current values (not the identity-map copy)
                # before publishing a session. This serializes same-user login
                # admission with revocation/disable without trusting a stale
                # password check performed while another transaction committed.
                account = db.exec(select(AccountRow).where(AccountRow.id == account.id)
                                  .with_for_update().execution_options(populate_existing=True)).first()
                if account is None or account.disabled or account.password_hash != encoded:
                    raise auth_error("invalid_credentials", 401, "账号或密码不正确，或账号不可用。")
            if PASSWORD_HASHER.check_needs_rehash(account.password_hash):
                account.password_hash = PASSWORD_HASHER.hash(password)
                db.add(account)
            if old_token:
                db.exec(delete(SessionRow).where(SessionRow.token_hash == hashlib.sha256(old_token.encode()).hexdigest()))
            now = now_seconds()
            # Bounded retention: expired sessions are never accepted and can be removed.
            db.exec(delete(SessionRow).where(SessionRow.expires_at <= now))
            session_query = select(SessionRow).where(SessionRow.user_id == account.id).order_by(SessionRow.created_at.desc(), SessionRow.id.desc())
            if mysql:
                # A locking read is also a current read under a server's default
                # REPEATABLE READ, so a waiter sees the preceding login's session.
                session_query = session_query.with_for_update()
            sessions = db.exec(session_query).all()
            for obsolete in sessions[7:]:
                db.delete(obsolete)
            token = secrets.token_urlsafe(32)
            session = SessionRow(id=uuid4().hex, token_hash=hashlib.sha256(token.encode()).hexdigest(), user_id=account.id,
                                 csrf_token=secrets.token_urlsafe(32), created_at=now,
                                 expires_at=now + get_auth_settings().auth_session_seconds)
            db.add(session)
            db.commit()
            return user_view(account, session), token
    finally:
        _HASH_CAPACITY.release()


def logout(session_id: str) -> None:
    with Session(identity_engine()) as db:
        db.exec(delete(SessionRow).where(SessionRow.id == session_id))
        db.commit()


def create_account(username: str, password: str) -> str:
    if USERNAME_PATTERN.fullmatch(username) is None or not 12 <= len(password) <= 128:
        raise ValueError("invalid_account_input")
    identifier = uuid4().hex
    account = AccountRow(id=identifier, username=username, password_hash=PASSWORD_HASHER.hash(password),
                         created_at=now_seconds())
    with Session(identity_engine()) as db:
        db.add(account)
        db.commit()
    return identifier


def revoke_account(username: str, *, disable: bool = False) -> int:
    with Session(identity_engine()) as db:
        mysql = db.bind.dialect.name == "mysql"
        query = select(AccountRow).where(AccountRow.username == username)
        if mysql:
            query = query.with_for_update().execution_options(populate_existing=True)
        account = db.exec(query).first()
        if account is None:
            raise ValueError("account_not_found")
        if disable:
            account.disabled = True
            db.add(account)
        session_query = select(SessionRow).where(SessionRow.user_id == account.id)
        if mysql:
            session_query = session_query.with_for_update()
        rows = db.exec(session_query).all()
        for session in rows:
            db.delete(session)
        db.commit()
        return len(rows)


def register_account(username: str, password: str, invitation_code: str) -> None:
    """Hash first, then consume exactly one invite in the account transaction.

    The caller never receives the invitation record or whether a username exists.
    Commit uncertainty is not an invitation to retry automatically: subsequent
    manual login/registration is authoritative, not a fabricated success.
    """
    if (USERNAME_PATTERN.fullmatch(username) is None or not 12 <= len(password) <= 128
            or TOKEN_PATTERN.fullmatch(invitation_code) is None):
        raise auth_error("invalid_registration", 422)
    if not mysql_configured():
        raise auth_error("registration_unavailable", 503, "自助注册暂不可用，请联系管理员。")
    reserve_identity_attempt("registration", username)
    if not _HASH_CAPACITY.acquire(blocking=False):
        raise auth_error("registration_busy", 429, "账号处理忙，请稍后主动重试。")
    try:
        encoded = PASSWORD_HASHER.hash(password)
        with Session(identity_engine()) as db:
            digest = hashlib.sha256(invitation_code.encode("ascii")).hexdigest()
            invitation = db.exec(select(InvitationRow).where(InvitationRow.token_hash == digest).with_for_update()).first()
            now = now_seconds()
            if (invitation is None or invitation.revoked or invitation.expires_at <= now
                    or invitation.consumed_by is not None or invitation.consumed_at is not None):
                raise auth_error("registration_rejected", 400, "无法完成注册，请检查账号及邀请码，或联系管理员。")
            account = AccountRow(id=uuid4().hex, username=username, password_hash=encoded, created_at=now)
            db.add(account)
            db.flush()  # Unique username and FK are checked before consuming.
            invitation.consumed_by, invitation.consumed_at = account.id, now
            db.add(invitation)
            db.commit()
    except IntegrityError:
        raise auth_error("registration_rejected", 400, "无法完成注册，请检查账号及邀请码，或联系管理员。") from None
    finally:
        _HASH_CAPACITY.release()


def create_invitation(valid_hours: int = 24) -> tuple[str, str]:
    if not isinstance(valid_hours, int) or isinstance(valid_hours, bool) or not 1 <= valid_hours <= 720:
        raise ValueError("invalid_invitation_expiry")
    engine = current_mysql_engine()
    token, identifier, now = secrets.token_urlsafe(32), uuid4().hex, now_seconds()
    row = InvitationRow(id=identifier, token_hash=hashlib.sha256(token.encode("ascii")).hexdigest(),
                        created_at=now, expires_at=now + valid_hours * 3600)
    with Session(engine) as db:
        db.add(row)
        db.commit()
    return identifier, token


def revoke_invitation(identifier: str) -> None:
    if re.fullmatch(r"[0-9a-f]{32}", identifier) is None:
        raise ValueError("invalid_invitation_id")
    with Session(current_mysql_engine()) as db:
        row = db.exec(select(InvitationRow).where(InvitationRow.id == identifier).with_for_update()).first()
        if row is None:
            raise ValueError("invitation_not_found")
        row.revoked = True
        db.add(row)
        db.commit()
