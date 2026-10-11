import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine
from datetime import datetime, timezone
from typing import Annotated, Any, TypeVar

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.routing import APIRoute
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.integrations.qwen import QwenClient, QwenError, strict_json
from app.schemas.explanation import ExplanationRequest, ExplanationResponse
from app.services.explanation import build_facts, fixed_limitations, render_selection, validate_snapshot

MAX_BODY_BYTES = 65_536


class BoundedExplanationRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()
        async def bounded(request: Request) -> Response:
            chunks = bytearray()
            try:
                async with asyncio.timeout(5):
                    async for chunk in request.stream():
                        if len(chunks) + len(chunk) > MAX_BODY_BYTES:
                            raise HTTPException(413, detail="解读快照超过允许大小。")
                        chunks.extend(chunk)
            except TimeoutError:
                raise HTTPException(408, detail="接收解读请求超时。") from None
            # Read-once bounded body before FastAPI/JSON parsing, including chunked input.
            request._body = bytes(chunks)
            return await handler(request)
        return bounded


router = APIRouter(prefix="/trips", tags=["trips"], route_class=BoundedExplanationRoute)


async def get_qwen_http_client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(follow_redirects=False, timeout=20) as client:
        yield client


T = TypeVar("T")


async def while_connected(raw: Request, call: Coroutine[Any, Any, T]) -> T:
    operation = asyncio.create_task(call)
    async def disconnected() -> None:
        while True:
            if (await raw.receive())["type"] == "http.disconnect":
                return
            await asyncio.sleep(0)
    watch = asyncio.create_task(disconnected())
    try:
        done, _ = await asyncio.wait((operation, watch), return_when=asyncio.FIRST_COMPLETED)
        if operation not in done:
            raise HTTPException(499, detail="已取消等待；上游是否停止处理或计费无法保证。")
        return operation.result()
    finally:
        for task in (operation, watch):
            if not task.done():
                task.cancel()
        await asyncio.gather(operation, watch, return_exceptions=True)


@router.post("/schedule-explanation", response_model=ExplanationResponse)
async def explain_schedule(
    raw: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    client: Annotated[httpx.AsyncClient, Depends(get_qwen_http_client)],
) -> ExplanationResponse:
    try:
        body = await raw.body()
        strict_json(body)  # reject duplicate keys/non-finite literals before Pydantic
        request = ExplanationRequest.model_validate_json(body)
        schedule = validate_snapshot(request.snapshot)
    except (ValidationError, ValueError, RecursionError):
        raise HTTPException(422, detail="行程解读快照不完整或与当前排程规则不一致。") from None
    facts = build_facts(schedule)
    try:
        selection, usage = await while_connected(raw, QwenClient(settings, client).select(facts))
        try:
            highlights = render_selection(selection, facts)
        except ValueError:
            raise QwenError("references") from None
        return ExplanationResponse(request_id=request.request_id, schedule_generated_at=schedule.generated_at,
                                   explained_at=datetime.now(timezone.utc), model=settings.qwen_model,
                                   highlights=highlights, limitations=fixed_limitations(schedule), usage=usage)
    except QwenError as error:
        raise HTTPException(error.status, detail={"code": error.code, "message": str(error)}) from None
