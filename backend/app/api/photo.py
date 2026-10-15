"""Binary upload: bounded before decoding; no multipart spool or image URLs."""
import asyncio
import re
from typing import Annotated
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect
from app.api.explanation import get_qwen_http_client
from app.config import Settings, get_settings
from app.destinations import DESTINATIONS
from app.integrations.qwen import QwenError
from app.integrations.qwen_vision import QwenVisionClient
from app.schemas.explanation import ExplanationUsage
from app.schemas.photo import PhotoResponse, PhotoSelection
from app.services.photo import MAX_BYTES, PhotoError, normalize_photo

router = APIRouter(prefix="/media", tags=["media"])
# One accepted upload/decode/model operation per process, no waiting queue.
busy = False


def cancelled_photo() -> HTTPException:
    return HTTPException(499, detail="已取消等待；若请求已发出，上游是否停止处理或计费无法保证。")


async def decode_and_recognize(raw: Request, body: bytearray, vision: QwenVisionClient
                               ) -> tuple[PhotoSelection, ExplanationUsage]:
    """Own receive only AFTER the upload. Keep the slot until the thread exits."""
    async def disconnected() -> None:
        while True:
            if (await raw.receive())["type"] == "http.disconnect":
                return
            await asyncio.sleep(0)

    watch = asyncio.create_task(disconnected())
    worker: asyncio.Task[bytes] | None = None
    operation: asyncio.Task[tuple[PhotoSelection, ExplanationUsage]] | None = None

    async def checkpoint() -> None:
        # Let the sole receive owner observe an already queued disconnect before
        # starting decode / sending. Receive errors also fail closed, not as 500s.
        await asyncio.sleep(0)
        if watch.done():
            raise cancelled_photo()

    try:
        await checkpoint()
        worker = asyncio.create_task(run_in_threadpool(normalize_photo, bytes(body)))
        body.clear()
        await asyncio.wait((worker, watch), return_when=asyncio.FIRST_COMPLETED)
        await checkpoint()
        image = worker.result()

        async def send() -> tuple[PhotoSelection, ExplanationUsage]:
            await checkpoint()
            return await vision.recognize(image)

        operation = asyncio.create_task(send())
        await asyncio.wait((operation, watch), return_when=asyncio.FIRST_COMPLETED)
        await checkpoint()
        return operation.result()
    finally:
        body.clear()
        # Do NOT cancel the decode task: cancelling its wrapper cannot stop the
        # running thread. Repeated outer cancellation must not release busy early.
        for task in (watch, operation):
            if task is not None and not task.done():
                task.cancel()
        cleanup = asyncio.gather(*(task for task in (worker, watch, operation) if task is not None),
                                 return_exceptions=True)
        interrupted = False
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                interrupted = True
        if interrupted:
            raise asyncio.CancelledError


@router.post("/landmark", response_model=PhotoResponse)
async def recognize_photo(raw: Request, settings: Annotated[Settings, Depends(get_settings)],
                          client: Annotated[httpx.AsyncClient, Depends(get_qwen_http_client)]) -> PhotoResponse:
    global busy
    pairs = list(raw.query_params.multi_items())
    params = dict(pairs)
    if (len(pairs) != 2 or set(params) != {"destination_id", "request_id"}
            or params["destination_id"] not in DESTINATIONS
            or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", params["request_id"])):
        raise HTTPException(422, detail={"code": "input", "message": "照片请求身份或目的地无效。"})
    if busy:
        raise HTTPException(429, detail={"code": "busy", "message": "另一张照片仍在处理，请稍后主动重试。"})
    busy = True
    try:
        body = bytearray()
        try:
            async with asyncio.timeout(10):
                async for chunk in raw.stream():
                    if len(body) + len(chunk) > MAX_BYTES:
                        raise PhotoError("image_size", 413, "图片不能超过 5 MiB。")
                    body.extend(chunk)
        except TimeoutError:
            raise PhotoError("upload_timeout", 408, "接收照片超时。") from None
        selection, usage = await decode_and_recognize(raw, body, QwenVisionClient(settings, client))
        return PhotoResponse(**selection.model_dump(), request_id=params["request_id"],
                             destination_id=params["destination_id"], model=settings.qwen_vision_model, usage=usage)
    except ClientDisconnect:
        raise cancelled_photo() from None
    except PhotoError as error:
        raise HTTPException(error.status, detail={"code": error.code, "message": str(error)}) from None
    except QwenError as error:
        raise HTTPException(error.status, detail={"code": error.code,
            "message": "照片识别未完成（" + error.code + "）；已确认地点未改变，未切换其他模型。"}) from None
    finally:
        busy = False
