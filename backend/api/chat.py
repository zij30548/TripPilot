"""One bounded model selection per explicit turn; no travel tools or loop."""
import asyncio
import json
import logging
import time
from collections.abc import Callable, Coroutine
from datetime import datetime, timezone
from typing import Annotated, Any, TypeVar

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.routing import APIRoute
from pydantic import ValidationError

from api.explanation import get_qwen_http_client
from core.auth import current_user
from memory.chat_storage import ChatError, ChatStore
from core.config import Settings, get_settings
from core.business_usage import UsageError
from memory.identity_models import AccountRow
from tooling.providers.qwen import QwenError, strict_json
from tooling.providers.qwen_chat import QwenIntentClient, validation_diagnostic
from core.schemas.chat import (ConfirmConversation, Conversation, ConversationList,
                              CreateConversation, DeleteConversation, Identifier,
                              Intent, Selection, SubmitTurn)
from tooling.travel.chat import (PROMPT_VERSION, PROVIDER_SCHEMA_VERSION, ChatContextLimit,
                               apply_selection, model_payload, render_reply)


class ChatRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()
        async def bounded(request: Request) -> Response:
            current_user(request)
            if request.method == "POST":
                if request.query_params:
                    raise HTTPException(422, detail={"code": "chat_invalid_request"})
                chunks = bytearray()
                try:
                    async with asyncio.timeout(5):
                        async for chunk in request.stream():
                            if len(chunks) + len(chunk) > 16_384:
                                raise HTTPException(413, detail={"code": "chat_body_too_large"})
                            chunks.extend(chunk)
                except TimeoutError:
                    raise HTTPException(408, detail={"code": "chat_receive_timeout"}) from None
                request._body = bytes(chunks)
            return await handler(request)
        return bounded


router = APIRouter(prefix="/chat/conversations", tags=["chat"], route_class=ChatRoute)
User = Annotated[AccountRow, Depends(current_user)]
T = TypeVar("T")
_ACTIVE: dict[tuple[str, str, str], asyncio.Task] = {}
_LOG = logging.getLogger("trippilot.chat")


def get_chat_store() -> ChatStore:
    return ChatStore()


Store = Annotated[ChatStore, Depends(get_chat_store)]


async def db_call(function: Callable[..., T], *args: Any) -> T:
    """Wait for DB thread even on cancellation; never hold transaction for HTTP."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    interrupted = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            interrupted = True
    if interrupted:
        task.exception()
        raise asyncio.CancelledError
    return task.result()


async def connected(raw: Request, key: tuple[str, str, str], call: Callable[[], Coroutine[Any, Any, T]]) -> T:
    """Exactly one receive reader, after bounded upload has fully finished."""
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
            raise HTTPException(499, detail={"code": "chat_cancelled"})
        operation = asyncio.create_task(call())
        _ACTIVE[key] = operation
        done, _ = await asyncio.wait((operation, watch), return_when=asyncio.FIRST_COMPLETED)
        if operation not in done:
            raise HTTPException(499, detail={"code": "chat_cancelled"})
        return operation.result()
    finally:
        if _ACTIVE.get(key) is operation:
            _ACTIVE.pop(key, None)
        tasks = [watch] + ([operation] if operation is not None else [])
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def body(raw: Request, schema: type[T]) -> T:
    try:
        return schema.model_validate(strict_json(await raw.body()))
    except (ValidationError, ValueError, TypeError, RecursionError):
        raise HTTPException(422, detail={"code": "chat_invalid_request", "message": "请求格式无效，未执行本轮查询。"}) from None


def safe_error(error: ChatError) -> HTTPException:
    return HTTPException(error.status, detail={"code": error.code,
        "message": "会话操作未完成；请核对当前状态。若需存储升级，只能由管理员显式迁移，不能重建原库。"})


@router.get("", response_model=ConversationList)
async def listing(raw: Request, store: Store, user: User,
                  offset: Annotated[int, Query(ge=0, le=1000)] = 0,
                  limit: Annotated[int, Query(ge=1, le=20)] = 20) -> dict:
    if set(raw.query_params) - {"offset", "limit"}:
        raise HTTPException(422, detail={"code": "chat_invalid_request"})
    try:
        return await db_call(store.list, user.id, offset, limit)
    except ChatError as error:
        raise safe_error(error) from None


@router.post("", response_model=Conversation)
async def create(raw: Request, store: Store, user: User) -> dict:
    request = await body(raw, CreateConversation)
    try:
        return await db_call(store.create, user.id, request.operation_id)
    except ChatError as error:
        raise safe_error(error) from None


@router.get("/{conversation_id}", response_model=Conversation)
async def read(conversation_id: Identifier, raw: Request, store: Store, user: User) -> dict:
    if raw.query_params:
        raise HTTPException(422, detail={"code": "chat_invalid_request"})
    try:
        return await db_call(store.get, user.id, conversation_id)
    except ChatError as error:
        raise safe_error(error) from None


@router.post("/{conversation_id}/turns", response_model=Conversation)
async def submit(conversation_id: Identifier, raw: Request, store: Store, user: User,
                 settings: Annotated[Settings, Depends(get_settings)],
                 client: Annotated[httpx.AsyncClient, Depends(get_qwen_http_client)]) -> dict:
    request = await body(raw, SubmitTurn)
    reservation = None
    started = time.monotonic()
    try:
        reservation = await db_call(store.begin_turn, user.id, conversation_id,
                                    request.operation_id, request.expected_revision, request.text)
        frozen = reservation["conversation"]
        version = reservation["revision"]

        async def choose_and_commit() -> dict:
            current = await db_call(store.get, user.id, conversation_id)
            if (current["revision"] != version or current["last_turn"] is None
                    or current["last_turn"]["operation_id"] != request.operation_id
                    or current["last_turn"]["state"] != "running"):
                raise ChatError("chat_turn_obsolete", 409)
            payload = model_payload(frozen, request.text, model=settings.qwen_model, now=datetime.now(timezone.utc))
            provider = QwenIntentClient(settings, client)
            try:
                selection, usage = await provider.generate(payload)
                try:
                    selected = Selection.model_validate(selection)
                except ValidationError as error:
                    provider.diagnostic["outcome"] = "selection_rejected"
                    provider.diagnostic["validation"] = validation_diagnostic(error)
                    raise QwenError("references") from None
                try:
                    intent, sources = apply_selection(Intent.model_validate(frozen["requirements"]), selected, request.text)
                except ValueError:
                    provider.diagnostic["outcome"] = "provenance_rejected"
                    raise QwenError("references") from None
                provider.diagnostic["outcome"] = "selection_validated"
            finally:
                provider.diagnostic.update(prompt_version=PROMPT_VERSION, schema_version=PROVIDER_SCHEMA_VERSION)
                _LOG.info("chat_response_diagnostic operation=%s version=%s metadata=%s",
                          request.operation_id, version, json.dumps(provider.diagnostic, separators=(",", ":")))
            result = await db_call(store.finish_turn, user.id, conversation_id, request.operation_id,
                                 version, intent.model_dump(), render_reply(intent, selected.issues),
                                 [source.model_dump() for source in sources], selected.issues)
            _LOG.info("chat_turn_finished operation=%s version=%s model=%s elapsed_ms=%d input_tokens=%s output_tokens=%s",
                      request.operation_id, version, settings.qwen_model, int((time.monotonic() - started) * 1000),
                      usage.input_tokens, usage.output_tokens)
            return result

        return await connected(raw, (user.id, conversation_id, request.operation_id), choose_and_commit)
    except ChatError as error:
        raise safe_error(error) from None
    except QwenError as error:
        if reservation is not None:
            await db_call(store.fail_turn, user.id, conversation_id, request.operation_id,
                          reservation["revision"], error.code)
        raise HTTPException(error.status, detail={"code": "chat_" + error.code,
            "message": "本轮需求整理未完成，未生成或修改行程；没有自动重试。请刷新查看会话收据后再决定下一步。"}) from None
    except ChatContextLimit:
        if reservation is not None:
            await db_call(store.fail_turn, user.id, conversation_id, request.operation_id,
                          reservation["revision"], "request_failed")
        raise HTTPException(409, detail={"code": "chat_context_limit",
            "message": "会话上下文已达到48 KiB上限，本次未发送模型；请新建会话，不会静默截断旧对话。"}) from None
    except (asyncio.CancelledError, HTTPException):
        if reservation is not None:
            await db_call(store.cancel_turn, user.id, conversation_id, request.operation_id)
        raise
    except UsageError as error:
        if reservation is not None:
            await db_call(store.fail_turn, user.id, conversation_id, request.operation_id,
                          reservation["revision"], error.code)
        raise
    except BaseException:
        if reservation is not None:
            await db_call(lambda: store.fail_turn(user.id, conversation_id, request.operation_id,
                          reservation["revision"], "request_uncertain", uncertain=True))
        raise


@router.post("/{conversation_id}/cancel", response_model=Conversation)
async def cancel(conversation_id: Identifier, raw: Request, store: Store, user: User) -> dict:
    request = await body(raw, CreateConversation)
    try:
        result = await db_call(store.cancel_turn, user.id, conversation_id, request.operation_id)
        task = _ACTIVE.get((user.id, conversation_id, request.operation_id))
        if task is not None and not task.done():
            task.cancel()
        return result
    except ChatError as error:
        raise safe_error(error) from None


@router.post("/{conversation_id}/confirm", response_model=Conversation)
async def confirm(conversation_id: Identifier, raw: Request, store: Store, user: User) -> dict:
    request = await body(raw, ConfirmConversation)
    try:
        return await db_call(store.confirm, user.id, conversation_id, request.expected_revision, request.operation_id)
    except ChatError as error:
        raise safe_error(error) from None


@router.post("/{conversation_id}/delete", status_code=204)
async def delete(conversation_id: Identifier, raw: Request, store: Store, user: User) -> Response:
    request = await body(raw, DeleteConversation)
    try:
        await db_call(store.delete, user.id, conversation_id, request.expected_revision)
    except ChatError as error:
        raise safe_error(error) from None
    return Response(status_code=204)
