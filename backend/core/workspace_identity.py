"""Private browser workspaces without login, passwords or shared ownership.

Only a server-issued opaque cookie grants access. A separate, HttpOnly seed lets
an interrupted bootstrap resume the same owner; the public CSRF token never
derives an owner credential. Existing accounts, sessions and quotas are retained.
No schema creation or external request occurs in this module.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from typing import Any

from fastapi import HTTPException
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.engine import Engine
from sqlmodel import Session, select
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from core.auth import (TOKEN_PATTERN, allowed_origin, cookie_value, get_auth_settings,
                       now_seconds, user_view)
from core.usage_runtime import bind_usage_context, reset_usage_context
from core.workspace_contract import WORKSPACE_PASSWORD_MARKER, WORKSPACE_USERNAME_PREFIX, workspace_fields
from memory.identity_models import AccountRow, LoginBucketRow, SessionRow
from memory.mysql_storage import current_mysql_engine, mysql_configured
from memory.storage import StorageError
from sqlalchemy.exc import SQLAlchemyError

WORKSPACE_SESSION_SECONDS = 30 * 24 * 60 * 60
WORKSPACE_SEED_SECONDS = 365 * 24 * 60 * 60
BOOTSTRAP_WINDOW_SECONDS = 300
BOOTSTRAP_GLOBAL_LIMIT = 30
BOOTSTRAP_SEED_LIMIT = 6
PUBLIC_ENDPOINTS = frozenset({("GET", "/health"), ("GET", "/workspace/session"),
                              ("POST", "/workspace/session")})


def workspace_error(code: str = "workspace_unavailable", status: int = 503,
                    message: str = "工作区暂不可用，请稍后主动重试。", *, retry_after: int | None = None) -> HTTPException:
    return HTTPException(status, detail={"code": code, "message": message},
                         headers={"Retry-After": str(retry_after)} if retry_after is not None else None)


def cookie_name(kind: str) -> str:
    if kind not in {"session", "seed", "preauth"}:
        raise ValueError("invalid_workspace_cookie_kind")
    prefix = "__Host-" if get_auth_settings().auth_cookie_secure else ""
    return f"{prefix}trippilot_workspace_{kind}"


def workspace_engine() -> Engine:
    if not mysql_configured():
        raise StorageError("workspace_requires_mysql")
    return current_mysql_engine()


def _derived(seed: str, purpose: str) -> str:
    return hmac.new(seed.encode("ascii"), ("trippilot-workspace-v1:" + purpose).encode("ascii"),
                    hashlib.sha256).hexdigest()


def _token(seed: str) -> str:
    return base64.urlsafe_b64encode(bytes.fromhex(_derived(seed, "session-token"))).decode("ascii").rstrip("=")


def is_workspace(account: AccountRow) -> bool:
    return workspace_fields(account.id, account.username, account.password_hash)


def resolve_workspace(scope: Scope) -> tuple[AccountRow, SessionRow] | None:
    token = cookie_value(scope, cookie_name("session"))
    if token is None:
        return None
    digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    with Session(workspace_engine(), expire_on_commit=False) as db:
        session = db.exec(select(SessionRow).where(SessionRow.token_hash == digest)).first()
        if session is None or session.expires_at <= now_seconds():
            return None
        account = db.get(AccountRow, session.user_id)
        if account is None or account.disabled or not is_workspace(account):
            return None
        return account, session


def workspace_view(account: AccountRow, session: SessionRow) -> dict[str, Any]:
    result = user_view(account, session)
    result["user"]["username"] = "当前工作区"
    return result


def validate_workspace_csrf(scope: Scope, session: SessionRow | None = None) -> bool:
    if not allowed_origin(scope):
        return False
    values = Headers(scope=scope).getlist("x-csrf-token")
    supplied = values[0] if len(values) == 1 else ""
    expected = session.csrf_token if session else cookie_value(scope, cookie_name("preauth"))
    return bool(expected and TOKEN_PATTERN.fullmatch(supplied) and hmac.compare_digest(expected, supplied))


def bootstrap_workspace(seed: str) -> tuple[dict[str, Any], str]:
    """Atomically reserve admission and create/resume one private seed owner.

    The persistent global bucket is locked first, so separate processes cannot
    race bootstrap admission or insert two owners for one seed. The owner and
    session have deterministic, secret-seed-derived IDs for safe response-loss
    recovery. Database commit uncertainty is surfaced, never automatically retried.
    """
    if TOKEN_PATTERN.fullmatch(seed) is None:
        raise workspace_error("workspace_seed_invalid", 403)
    owner_id = _derived(seed, "owner")[:32]
    session_id = _derived(seed, "session")[:32]
    token = _token(seed)
    with Session(workspace_engine(), expire_on_commit=False) as db:
        now = now_seconds()
        for label, maximum in (("global", BOOTSTRAP_GLOBAL_LIMIT), (_derived(seed, "limiter"), BOOTSTRAP_SEED_LIMIT)):
            identifier = hashlib.sha256(("workspace-bootstrap-v1:" + label).encode("ascii")).hexdigest()
            create = mysql_insert(LoginBucketRow).values(id=identifier, window_start=now, attempts=0)
            db.execute(create.on_duplicate_key_update(id=create.inserted.id))
            bucket = db.exec(select(LoginBucketRow).where(LoginBucketRow.id == identifier).with_for_update()).one()
            if now - bucket.window_start >= BOOTSTRAP_WINDOW_SECONDS:
                bucket.window_start, bucket.attempts = now, 0
            if bucket.attempts >= maximum:
                wait = max(1, BOOTSTRAP_WINDOW_SECONDS - (now - bucket.window_start))
                raise workspace_error("workspace_rate_limited", 429, "工作区准备过于频繁，请等待后主动重试。", retry_after=wait)
            bucket.attempts += 1
            db.add(bucket)
        account = db.exec(select(AccountRow).where(AccountRow.id == owner_id).with_for_update()).first()
        if account is None:
            account = AccountRow(id=owner_id, username=WORKSPACE_USERNAME_PREFIX + owner_id,
                                 password_hash=WORKSPACE_PASSWORD_MARKER, disabled=False, created_at=now)
            db.add(account)
            db.flush()
        elif account.disabled or not is_workspace(account):
            raise workspace_error("workspace_unavailable", 403)
        session = db.exec(select(SessionRow).where(SessionRow.id == session_id).with_for_update()).first()
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        if session is None:
            session = SessionRow(id=session_id, token_hash=digest, user_id=owner_id,
                                 csrf_token=secrets.token_urlsafe(32), created_at=now,
                                 expires_at=now + WORKSPACE_SESSION_SECONDS)
        elif session.user_id != owner_id or session.token_hash != digest:
            raise workspace_error()
        else:
            session.expires_at = now + WORKSPACE_SESSION_SECONDS
        db.add(session)
        db.commit()
        return workspace_view(account, session), token


class WorkspaceMiddleware:
    """Session/CSRF gate before parsing, media processing or external dispatch."""
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
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if scope["path"] == "/auth" or scope["path"].startswith("/auth/"):
            await JSONResponse({"detail": {"code": "endpoint_removed", "message": "此接口已移除。"}}, 404)(scope, receive, private_send)
            return
        if scope["method"] == "OPTIONS" or (scope["method"], scope["path"]) in PUBLIC_ENDPOINTS:
            await self.app(scope, receive, send if scope["path"] == "/health" else private_send)
            return
        try:
            identity = await run_in_threadpool(resolve_workspace, scope)
            if identity is None:
                response = JSONResponse({"detail": {"code": "workspace_required", "message": "请先准备当前浏览器的工作区。"}}, 401)
            elif scope["method"] not in {"GET", "HEAD"} and not validate_workspace_csrf(scope, identity[1]):
                response = JSONResponse({"detail": {"code": "csrf_invalid", "message": "工作区状态已变化，请刷新后重试。"}}, 403)
            else:
                scope.setdefault("state", {}).update(user=identity[0], session=identity[1])
                response = None
        except (StorageError, SQLAlchemyError, OSError, ValueError):
            response = JSONResponse({"detail": {"code": "workspace_unavailable", "message": "工作区暂不可用。"}}, 503)
        if response is None:
            usage_token = bind_usage_context(identity[0].id, scope["path"])
            try:
                await self.app(scope, receive, private_send)
            finally:
                reset_usage_context(usage_token)
            return
        await response(scope, receive, private_send)
