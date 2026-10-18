"""Authenticated, own-account-only business allowance summary (no dispatch)."""
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.auth import CurrentUser
from app.business_usage import get_usage_store

router = APIRouter(tags=["usage"])


@router.get("/usage")
async def usage(request: Request, user: CurrentUser) -> dict[str, Any]:
    # Identity is exclusively server-derived. There is no administrator or
    # arbitrary-owner read API, including through ostensibly harmless queries.
    if request.query_params:
        raise HTTPException(422, detail={"code": "invalid_usage_query", "message": "额度查询不接受账号参数。"})
    return await run_in_threadpool(get_usage_store().own_usage, user.id)
