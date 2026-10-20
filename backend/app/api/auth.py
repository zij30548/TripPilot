"""Invitation-only login endpoints; credentials are never returned or logged."""
import asyncio
import secrets

from argon2.exceptions import HashingError
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse

from app.auth import (anonymous_view, auth_error, cookie_value, get_auth_settings,
                      login, logout, register_account, resolve_identity, user_view, validate_csrf)
from app.integrations.qwen import strict_json
from app.storage import StorageError

router = APIRouter(prefix="/auth", tags=["identity"])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    username: str = Field(min_length=3, max_length=48, pattern=r"^[a-z0-9_.-]+$")
    password: str = Field(min_length=1, max_length=128)


class RegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    username: str = Field(min_length=3, max_length=48, pattern=r"^[a-z0-9_.-]+$")
    password: str = Field(min_length=12, max_length=128)
    invitation_code: str = Field(min_length=43, max_length=43, pattern=r"^[A-Za-z0-9_-]+$")


def set_cookie(response: JSONResponse, name: str, value: str, max_age: int) -> None:
    settings = get_auth_settings()
    response.set_cookie(name, value, max_age=max_age, path="/", httponly=True,
                        secure=settings.auth_cookie_secure, samesite="lax")


def anonymous_response(existing_csrf: str | None = None) -> JSONResponse:
    token = existing_csrf or secrets.token_urlsafe(32)
    response = JSONResponse(anonymous_view(token), headers={"Cache-Control": "no-store"})
    set_cookie(response, get_auth_settings().preauth_cookie_name, token, 600)
    return response


@router.get("/session")
async def session_info(request: Request) -> JSONResponse:
    try:
        identity = await run_in_threadpool(resolve_identity, request.scope)
    except (StorageError, SQLAlchemyError, OSError, ValueError, HashingError):
        raise auth_error("identity_unavailable", 503) from None
    if identity:
        return JSONResponse(user_view(*identity), headers={"Cache-Control": "no-store"})
    # Tabs share cookies: a read-only identity refresh must not invalidate a
    # different tab's already displayed login form. Logout still rotates below.
    response = anonymous_response(cookie_value(request.scope, get_auth_settings().preauth_cookie_name))
    response.delete_cookie(get_auth_settings().cookie_name, path="/", secure=get_auth_settings().auth_cookie_secure,
                           httponly=True, samesite="lax")
    return response


@router.post("/login")
async def sign_in(request: Request) -> JSONResponse:
    try:
        identity = await run_in_threadpool(resolve_identity, request.scope)
        if not validate_csrf(request.scope, identity[1] if identity else None):
            raise auth_error("csrf_invalid", 403, "请先刷新登录页面后重试。")
        data = bytearray()
        if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
            raise auth_error("invalid_login", 422)
        async with asyncio.timeout(10):
            async for chunk in request.stream():
                if len(data) + len(chunk) > 4096:
                    raise auth_error("login_too_large", 413)
                data.extend(chunk)
        strict_json(bytes(data))
        value = LoginRequest.model_validate_json(bytes(data), strict=True)
    except TimeoutError:
        raise auth_error("login_receive_timeout", 408) from None
    except ClientDisconnect:
        raise auth_error("login_cancelled", 499) from None
    except (StorageError, SQLAlchemyError, OSError):
        raise auth_error("identity_unavailable", 503) from None
    except (ValueError, TypeError, RecursionError):
        raise auth_error("invalid_login", 422) from None
    try:
        result, token = await run_in_threadpool(login, value.username, value.password,
                                              cookie_value(request.scope, get_auth_settings().cookie_name))
    except (StorageError, SQLAlchemyError, OSError, ValueError, HashingError):
        raise auth_error("identity_unavailable", 503) from None
    response = JSONResponse(result, headers={"Cache-Control": "no-store"})
    set_cookie(response, get_auth_settings().cookie_name, token, get_auth_settings().auth_session_seconds)
    response.delete_cookie(get_auth_settings().preauth_cookie_name, path="/", secure=get_auth_settings().auth_cookie_secure,
                           httponly=True, samesite="lax")
    return response


@router.post("/logout")
async def sign_out(request: Request) -> JSONResponse:
    try:
        await run_in_threadpool(logout, request.state.session.id)
    except (StorageError, SQLAlchemyError, OSError):
        raise auth_error("identity_unavailable", 503) from None
    response = anonymous_response()
    response.delete_cookie(get_auth_settings().cookie_name, path="/", secure=get_auth_settings().auth_cookie_secure,
                           httponly=True, samesite="lax")
    return response


@router.post("/register")
async def sign_up(request: Request) -> JSONResponse:
    """Public admission, not a public business/auth bypass. No automatic login."""
    try:
        identity = await run_in_threadpool(resolve_identity, request.scope)
        if not validate_csrf(request.scope, identity[1] if identity else None):
            raise auth_error("csrf_invalid", 403, "请先刷新登录页面后重试。")
        if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
            raise auth_error("invalid_registration", 422)
        data = bytearray()
        async with asyncio.timeout(10):
            async for chunk in request.stream():
                if len(data) + len(chunk) > 4096:
                    raise auth_error("registration_too_large", 413)
                data.extend(chunk)
        strict_json(bytes(data))
        value = RegistrationRequest.model_validate_json(bytes(data), strict=True)
    except TimeoutError:
        raise auth_error("registration_receive_timeout", 408) from None
    except ClientDisconnect:
        raise auth_error("registration_cancelled", 499) from None
    except (StorageError, SQLAlchemyError, OSError, HashingError):
        raise auth_error("identity_unavailable", 503) from None
    except (ValueError, TypeError, RecursionError):
        raise auth_error("invalid_registration", 422) from None
    try:
        await run_in_threadpool(register_account, value.username, value.password, value.invitation_code)
    except (StorageError, SQLAlchemyError, OSError, ValueError, HashingError):
        raise auth_error("identity_unavailable", 503) from None
    return JSONResponse({"registered": True}, headers={"Cache-Control": "no-store"})
