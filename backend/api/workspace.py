"""Prepare an isolated browser workspace; no account credentials or invitations."""
import asyncio
import secrets

from fastapi import APIRouter, Request
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse
from starlette.datastructures import Headers

from core.auth import allowed_origin, anonymous_view, cookie_value, get_auth_settings
from core.workspace_identity import (WORKSPACE_SEED_SECONDS, WORKSPACE_SESSION_SECONDS,
    bootstrap_workspace, cookie_name, resolve_workspace, validate_workspace_csrf,
    workspace_error, workspace_view)
from memory.storage import StorageError
from tooling.providers.qwen import strict_json

router = APIRouter(prefix="/workspace", tags=["workspace"])


def _set_cookie(response: JSONResponse, kind: str, value: str, age: int) -> None:
    response.set_cookie(cookie_name(kind), value, max_age=age, path="/", httponly=True,
                        secure=get_auth_settings().auth_cookie_secure, samesite="lax")


@router.get("/session")
async def session_info(request: Request) -> JSONResponse:
    if request.query_params:
        raise workspace_error("invalid_workspace_request", 422)
    headers = Headers(scope=request.scope)
    if headers.get("sec-fetch-site") == "cross-site" or (headers.getlist("origin") and not allowed_origin(request.scope)):
        raise workspace_error("csrf_invalid", 403, "工作区请求来源无效。")
    try:
        identity = await run_in_threadpool(resolve_workspace, request.scope)
    except (StorageError, SQLAlchemyError, OSError, ValueError):
        raise workspace_error() from None
    if identity:
        return JSONResponse(workspace_view(*identity), headers={"Cache-Control": "no-store"})
    csrf = cookie_value(request.scope, cookie_name("preauth")) or secrets.token_urlsafe(32)
    seed = cookie_value(request.scope, cookie_name("seed")) or secrets.token_urlsafe(32)
    response = JSONResponse(anonymous_view(csrf), headers={"Cache-Control": "no-store"})
    _set_cookie(response, "preauth", csrf, 600)
    _set_cookie(response, "seed", seed, WORKSPACE_SEED_SECONDS)
    return response


@router.post("/session")
async def prepare_session(request: Request) -> JSONResponse:
    if request.query_params or not validate_workspace_csrf(request.scope):
        raise workspace_error("csrf_invalid", 403, "工作区状态已变化，请刷新后重试。")
    seed = cookie_value(request.scope, cookie_name("seed"))
    if seed is None:
        raise workspace_error("workspace_seed_invalid", 403)
    if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
        raise workspace_error("invalid_workspace_request", 422)
    try:
        data = bytearray()
        async with asyncio.timeout(10):
            async for chunk in request.stream():
                if len(data) + len(chunk) > 128:
                    raise workspace_error("workspace_request_too_large", 413)
                data.extend(chunk)
        if strict_json(bytes(data)) != {}:
            raise ValueError("empty_object_required")
    except TimeoutError:
        raise workspace_error("workspace_receive_timeout", 408) from None
    except ClientDisconnect:
        raise workspace_error("workspace_cancelled", 499) from None
    except (ValueError, TypeError, RecursionError):
        raise workspace_error("invalid_workspace_request", 422) from None
    try:
        view, token = await run_in_threadpool(bootstrap_workspace, seed)
    except (StorageError, SQLAlchemyError, OSError, ValueError):
        raise workspace_error() from None
    response = JSONResponse(view, headers={"Cache-Control": "no-store"})
    _set_cookie(response, "session", token, WORKSPACE_SESSION_SECONDS)
    _set_cookie(response, "seed", seed, WORKSPACE_SEED_SECONDS)
    # Keep the short pre-CSRF stable for a sibling tab's in-flight bootstrap.
    # It cannot access any business endpoint without the private session cookie.
    return response
