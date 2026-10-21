"""One preview task per process; input/children cleaned before capacity release."""
import asyncio
from pathlib import Path
import re
import tempfile

from fastapi import APIRouter, HTTPException, Request, Response
from starlette.requests import ClientDisconnect

from tooling.travel.video import (MAX_RESPONSE, MAX_UPLOAD, PROCESS_SECONDS, VideoError,
                                executables, extract_frames, settled, validate_container)
from core.usage_runtime import media_usage

router = APIRouter(prefix="/media", tags=["media"])
busy = False
UPLOAD_SECONDS = 30


def cancelled() -> HTTPException:
    return HTTPException(499, detail={"code": "cancelled", "message": "已取消视频处理。"})


async def process_upload(raw: Request, body: bytearray, request_id: str, binaries: tuple[str, str]) -> bytes:
    # Exactly one ASGI receive owner, started only after raw.stream() finishes.
    async def disconnected() -> None:
        while True:
            if (await raw.receive())["type"] == "http.disconnect":
                return
            await asyncio.sleep(0)

    watch = asyncio.create_task(disconnected())
    async def work() -> bytes:
        await asyncio.sleep(0)
        if watch.done():
            raise cancelled()
        async with media_usage("media_video"):
            # Do not start temporary-file/decode work after quota I/O disconnect.
            if watch.done():
                raise cancelled()
            with tempfile.TemporaryDirectory(prefix="trippilot-video-") as directory:
                path = Path(directory) / "input.mp4"
                def write() -> None:
                    validate_container(body)
                    with path.open("xb") as output:
                        path.chmod(0o600)
                        output.write(body)
                await settled(asyncio.to_thread(write))
                body.clear()
                if watch.done():
                    raise cancelled()
                result = await extract_frames(path, request_id, binaries)
                encoded = result.model_dump_json().encode()
                if len(encoded) > MAX_RESPONSE:
                    raise VideoError("output_limit", 422, "画面响应超过安全上限，未截断为成功结果。")
                return encoded

    operation = asyncio.create_task(work())
    try:
        async with asyncio.timeout(PROCESS_SECONDS):
            await asyncio.wait((watch, operation), return_when=asyncio.FIRST_COMPLETED)
            await asyncio.sleep(0)
            if watch.done():
                raise cancelled()
            return operation.result()
    finally:
        for task in (watch, operation):
            if not task.done():
                task.cancel()
        await settled(asyncio.gather(watch, operation, return_exceptions=True))
        body.clear()


@router.post("/video-frames")
async def video_frames(raw: Request) -> Response:
    global busy
    pairs = list(raw.query_params.multi_items())
    if len(pairs) != 1 or pairs[0][0] != "request_id" or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", pairs[0][1]):
        raise HTTPException(422, detail={"code": "input", "message": "视频请求身份无效。"})
    if busy:
        raise HTTPException(429, detail={"code": "video_busy", "message": "另一个视频仍在处理，请稍后主动重试。"})
    busy = True
    body = bytearray()
    try:
        binaries = executables()
        async with asyncio.timeout(UPLOAD_SECONDS):
            async for chunk in raw.stream():
                if len(body)+len(chunk) > MAX_UPLOAD:
                    raise VideoError("upload_size", 413, "请选择非空且不超过 20 MiB 的视频。")
                body.extend(chunk)
        if not body:
            raise VideoError("upload_size", 413, "请选择非空且不超过 20 MiB 的视频。")
        try:
            encoded = await process_upload(raw, body, pairs[0][1], binaries)
        except TimeoutError:
            raise VideoError("processing_timeout", 504, "视频处理超过 20 秒截止，已停止抽帧并清理，请主动选择其他视频。") from None
        return Response(encoded, media_type="application/json", headers={"Cache-Control": "no-store"})
    except TimeoutError:
        raise HTTPException(408, detail={"code": "upload_timeout", "message": "视频上传超过 30 秒，请主动重试。"}) from None
    except ClientDisconnect:
        raise cancelled() from None
    except VideoError as error:
        raise HTTPException(error.status, detail={"code": error.code, "message": str(error)}) from None
    except OSError:
        raise HTTPException(503, detail={"code": "video_unavailable", "message": "视频临时处理不可用，其他功能不受影响。"}) from None
    finally:
        body.clear()
        busy = False
