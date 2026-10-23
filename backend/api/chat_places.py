"""chat-2: explicit city, one bounded search, server-owned preview/confirmation."""
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Annotated
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import TypeAdapter, ValidationError

from api.chat import ChatRoute, User, db_call, safe_error
from api.explanation import get_qwen_http_client
from core.chat_cities import search_cities
from memory.chat_places_storage import ChatPlacesStore
from memory.chat_storage import ChatError
from core.business_usage import UsageError
from core.config import Settings, get_settings
from tooling.providers.amap import AmapError, AmapTimeoutError
# Keep the former module export for existing internal callers/test seams;
# construction for both transports goes exclusively through the factory.
from tooling.providers.chat_places import ChatPlacesClient, ChatPlacesError
from tooling.providers.chat_places_factory import chat_places_configured, create_chat_places_client
from tooling.providers.qwen import QwenError, strict_json
from tooling.providers.qwen_chat_places import QwenChatPlaceClient
from core.schemas.chat import Identifier
from core.schemas.chat_places import (Ask, Batch, ChatPlacesRequest, ChatPlacesWorkspace,
                                    Search, SearchArguments)
from tooling.travel.chat_places import MAX_OPERATION_SECONDS


router = APIRouter(prefix="/chat", tags=["chat-places"], route_class=ChatRoute)
_ACTIVE: dict[tuple[str, str, str], asyncio.Task] = {}
_LOG = logging.getLogger("trippilot.chat_places")


def get_chat_places_store() -> ChatPlacesStore:
    return ChatPlacesStore()


Store = Annotated[ChatPlacesStore, Depends(get_chat_places_store)]


async def request_body(raw: Request) -> ChatPlacesRequest:
    try:
        return TypeAdapter(ChatPlacesRequest).validate_python(strict_json(await raw.body()))
    except (ValidationError, ValueError, TypeError, RecursionError):
        raise HTTPException(422, detail={"code": "chat_places_invalid_request", "message": "地点操作格式无效，未执行查询。"}) from None


@router.get("/cities")
async def cities(raw: Request, user: User,
                 keyword: Annotated[str, Query(min_length=0, max_length=80)] = "") -> dict:
    if set(raw.query_params) - {"keyword"} or len(raw.query_params.getlist("keyword")) > 1:
        raise HTTPException(422, detail={"code": "chat_places_invalid_request"})
    return {"schema_version": "chat-cities-v1", "items": [city.model_dump(mode="json") for city in search_cities(keyword)]}


@router.get("/conversations/{conversation_id}/places", response_model=ChatPlacesWorkspace)
async def read(conversation_id: Identifier, raw: Request, store: Store, user: User) -> dict:
    if raw.query_params:
        raise HTTPException(422, detail={"code": "chat_places_invalid_request"})
    try:
        return await db_call(store.get, user.id, conversation_id)
    except ChatError as error:
        raise safe_error(error) from None


async def connected(raw: Request, key: tuple[str, str, str], call) -> dict:
    """Single receive owner after bounded body; drain HTTP/DB work on cancellation."""
    async def disconnected() -> None:
        while True:
            if (await raw.receive())["type"] == "http.disconnect":
                return
            await asyncio.sleep(0)
    watch = asyncio.create_task(disconnected())
    operation = None
    try:
        await asyncio.sleep(0)
        if watch.done():
            raise HTTPException(499, detail={"code": "chat_places_cancelled"})
        operation = asyncio.create_task(call())
        _ACTIVE[key] = operation
        done, _ = await asyncio.wait((operation, watch), return_when=asyncio.FIRST_COMPLETED)
        if operation not in done:
            raise HTTPException(499, detail={"code": "chat_places_cancelled"})
        return operation.result()
    finally:
        if _ACTIVE.get(key) is operation:
            _ACTIVE.pop(key, None)
        tasks = [watch] + ([operation] if operation is not None else [])
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@router.post("/conversations/{conversation_id}/places", response_model=ChatPlacesWorkspace)
async def command(conversation_id: Identifier, raw: Request, store: Store, user: User,
                  settings: Annotated[Settings, Depends(get_settings)],
                  client: Annotated[httpx.AsyncClient, Depends(get_qwen_http_client)]) -> dict:
    request = await request_body(raw)
    base = (user.id, conversation_id, request.operation_id,
            request.expected_intent_revision, request.expected_revision)
    reservation = None
    started = time.monotonic()
    try:
        if request.action == "sync":
            return await db_call(store.sync, *base)
        if request.action == "set_city":
            return await db_call(store.set_city, *base, request.city_id)
        if request.action == "confirm":
            return await db_call(store.confirm, *base, request.batch_id, request.place_id, request.role)
        if request.action == "remove":
            return await db_call(store.remove, *base, request.place_id, request.role)
        if request.action == "cancel":
            result = await db_call(store.cancel, user.id, conversation_id, request.operation_id, request.expected_revision)
            task = _ACTIVE.get((user.id, conversation_id, request.operation_id))
            if task is not None and not task.done():
                task.cancel()
            return result
        reservation = await db_call(store.begin_operation, *base, request.action, request.model_dump())
        if not reservation["dispatch"]:
            return reservation["workspace"]
        version = reservation["revision"]

        async def current() -> ChatPlacesWorkspace:
            view = ChatPlacesWorkspace.model_validate(await db_call(store.get, user.id, conversation_id))
            if (view.stale or view.intent_revision != request.expected_intent_revision or view.revision != version
                    or view.last_operation is None or view.last_operation.operation_id != request.operation_id
                    or view.last_operation.state != "running"):
                raise ChatError("chat_places_operation_obsolete", 409)
            return view

        async def finish(**values) -> dict:
            return await db_call(lambda: store.finish_operation(user.id, conversation_id, request.operation_id,
                version, request.expected_intent_revision, **values))

        async def execute() -> dict:
            async with asyncio.timeout(MAX_OPERATION_SECONDS):
                workspace = await current()
                if isinstance(request, Ask):
                    call_id, tool, usage = await QwenChatPlaceClient(settings, client).choose(workspace, request.text)
                    workspace = await current()
                    _LOG.info("chat_place_tool operation=%s version=%d tool=%s call_id=%s model=%s elapsed_ms=%d input_tokens=%s output_tokens=%s",
                              request.operation_id, version, tool.name, call_id, settings.qwen_model,
                              int((time.monotonic() - started) * 1000), usage.input_tokens, usage.output_tokens)
                    if tool.name == "request_clarification":
                        return await finish(clarification=tool.arguments.reason, request_text=request.text)
                    arguments = tool.arguments
                    if workspace.city_id is None or arguments.city_id != workspace.city_id:
                        return await finish(clarification="city_confirmation_required",
                                            proposed_city_id=arguments.city_id, request_text=request.text)
                else:
                    assert isinstance(request, Search)
                    if workspace.city_id is None:
                        return await finish(clarification="city_confirmation_required")
                    arguments = SearchArguments(city_id=workspace.city_id, keyword=request.keyword, role=request.role)
                await current()
                if not chat_places_configured(settings):
                    return await finish(error_code="places_not_configured")
                places = await create_chat_places_client(settings, client, before_send=current).search(arguments.keyword, arguments.city_id)
                await current()
                batch = Batch(id=str(uuid4()), city_id=arguments.city_id, keyword=arguments.keyword,
                    role=arguments.role, queried_at=datetime.now(timezone.utc).isoformat(), places=places)
                return await finish(batch=batch.model_dump(), request_text=request.text if isinstance(request, Ask) else None)

        return await connected(raw, (user.id, conversation_id, request.operation_id), execute)
    except ChatError as error:
        raise safe_error(error) from None
    except (QwenError, AmapError, TimeoutError) as error:
        if reservation is not None:
            code = ("operation_timeout" if isinstance(error, TimeoutError) else
                    "places_timeout" if isinstance(error, AmapTimeoutError) else
                    error.code if isinstance(error, ChatPlacesError) else
                    "places_upstream" if isinstance(error, AmapError) else "model_" + error.code)
            return await db_call(lambda: store.finish_operation(user.id, conversation_id, request.operation_id,
                reservation["revision"], request.expected_intent_revision, error_code=code))
        raise
    except UsageError as error:
        if reservation is not None:
            await db_call(lambda: store.finish_operation(user.id, conversation_id, request.operation_id,
                reservation["revision"], request.expected_intent_revision, error_code=error.code))
        raise
    except (asyncio.CancelledError, HTTPException):
        if reservation is not None:
            await db_call(store.cancel, user.id, conversation_id, request.operation_id, reservation["revision"])
        raise
    except BaseException:
        if reservation is not None:
            await db_call(lambda: store.finish_operation(user.id, conversation_id, request.operation_id,
                reservation["revision"], request.expected_intent_revision, error_code="request_uncertain"))
        raise
