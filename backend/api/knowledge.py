import asyncio
from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from tooling.providers.qwen import strict_json
from core.schemas.knowledge import KnowledgeRequest, KnowledgeResponse
from tooling.travel.knowledge import get_index

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.post("/search", response_model=KnowledgeResponse)
async def search(raw: Request) -> KnowledgeResponse:
    body = bytearray()
    try:
        async with asyncio.timeout(5):
            async for chunk in raw.stream():
                if len(body) + len(chunk) > 4096:
                    raise HTTPException(413, detail="查询内容过长。")
                body.extend(chunk)
        strict_json(bytes(body))
        request = KnowledgeRequest.model_validate_json(bytes(body))
    except TimeoutError:
        raise HTTPException(408, detail="接收查询超时。") from None
    except (ValueError, TypeError, RecursionError):
        raise HTTPException(422, detail="请选择已收录馆区，输入2至160字问题，返回数量最多5条。") from None
    try:
        return await run_in_threadpool(lambda: get_index().search(request))
    except (OSError, ValueError, TypeError):
        raise HTTPException(503, detail="本地资料暂不可用，请稍后主动重试。") from None
