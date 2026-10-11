import asyncio
from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.routing import APIRoute
from sqlmodel import Session
from starlette.concurrency import run_in_threadpool

from app.integrations.qwen import strict_json
from app.saved_trips import get_record, get_session, listing, save
from app.schemas.saved_trip import MAX_BODY_BYTES, RecordId, SaveTrip, SavedDetail, SavedList


class ArchiveRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()
        async def bounded(request: Request) -> Response:
            if request.method == "POST":
                chunks = bytearray()
                try:
                    async with asyncio.timeout(10):
                        async for chunk in request.stream():
                            if len(chunks) + len(chunk) > MAX_BODY_BYTES:
                                raise HTTPException(413, detail={"code": "too_large"})
                            chunks.extend(chunk)
                except TimeoutError:
                    raise HTTPException(408, detail={"code": "receive_timeout"}) from None
                request._body = bytes(chunks)
            return await handler(request)
        return bounded


router = APIRouter(prefix="/saved-trips", tags=["saved-trips"], route_class=ArchiveRoute)
DB = Annotated[Session, Depends(get_session)]


@router.post("", response_model=SavedDetail)
async def create(raw: Request, session: DB) -> SavedDetail:
    try:
        body = await raw.body()
        strict_json(body)
        request = SaveTrip.model_validate_json(body, strict=True)
    except (ValueError, RecursionError, TypeError):
        raise HTTPException(422, detail={"code": "invalid_snapshot"}) from None
    # Local atomic transaction, independent of browser disconnect semantics.
    return await run_in_threadpool(save, session, request)


@router.get("", response_model=SavedList)
def list_saved(session: DB, offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
               limit: Annotated[int, Query(ge=1, le=20)] = 10) -> SavedList:
    return listing(session, offset, limit)


@router.get("/{record_id}", response_model=SavedDetail)
def read_saved(record_id: RecordId, session: DB) -> SavedDetail:
    return get_record(session, record_id)
