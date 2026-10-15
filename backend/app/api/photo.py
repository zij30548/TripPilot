"""Binary upload: bounded before decoding; no multipart spool or image URLs."""
import asyncio
import re
from typing import Annotated
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from app.api.explanation import get_qwen_http_client, while_connected
from app.config import Settings, get_settings
from app.destinations import DESTINATIONS
from app.integrations.qwen import QwenError
from app.integrations.qwen_vision import QwenVisionClient
from app.schemas.photo import PhotoResponse
from app.services.photo import MAX_BYTES, PhotoError, normalize_photo

router = APIRouter(prefix="/media", tags=["media"])
# One accepted upload/decode/model operation per process, no waiting queue.
busy = False


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
        # Cancellation waits for the bounded decode worker before releasing capacity.
        worker = asyncio.create_task(run_in_threadpool(normalize_photo, bytes(body)))
        body.clear()
        try:
            image = await asyncio.shield(worker)
        finally:
            if not worker.done():
                await worker
        selection, usage = await while_connected(raw, QwenVisionClient(settings, client).recognize(image))
        return PhotoResponse(**selection.model_dump(), request_id=params["request_id"],
                             destination_id=params["destination_id"], model=settings.qwen_vision_model, usage=usage)
    except PhotoError as error:
        raise HTTPException(error.status, detail={"code": error.code, "message": str(error)}) from None
    except QwenError as error:
        raise HTTPException(error.status, detail={"code": error.code,
            "message": "照片识别未完成（" + error.code + "）；已确认地点未改变，未切换其他模型。"}) from None
    finally:
        busy = False
