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


# Independent v2 entry points. The frozen v1 schema, corpus and /search above
# remain unchanged; a new venue cannot silently change an old request's scope.
from core.schemas.knowledge_national import (
    NationalCatalogResponse, NationalKnowledgeRequest, NationalKnowledgeResponse,
)
from tooling.travel.knowledge_national import get_index as get_national_index


@router.get("/catalog-v2", response_model=NationalCatalogResponse)
async def national_catalog(raw: Request) -> NationalCatalogResponse:
    if raw.query_params:
        raise HTTPException(422, detail="资料目录不接受额外参数。")
    try:
        return await run_in_threadpool(lambda: get_national_index().catalog())
    except (OSError, ValueError, TypeError):
        raise HTTPException(503, detail="已审核资料目录暂不可用；未查询外部网站或生成答案。") from None


@router.post("/search-v2", response_model=NationalKnowledgeResponse)
async def national_search(raw: Request) -> NationalKnowledgeResponse:
    if raw.query_params:
        raise HTTPException(422, detail="资料查询不接受额外参数。")
    body = bytearray()
    try:
        async with asyncio.timeout(5):
            async for chunk in raw.stream():
                if len(body) + len(chunk) > 4096:
                    raise HTTPException(413, detail="查询内容过长。")
                body.extend(chunk)
        strict_json(bytes(body))
        request = NationalKnowledgeRequest.model_validate_json(bytes(body))
    except TimeoutError:
        raise HTTPException(408, detail="接收查询超时。") from None
    except (ValueError, TypeError, RecursionError):
        raise HTTPException(422, detail="请选择已收录的城市及对应场馆，输入2至160字问题，最多返回5条资料。") from None
    try:
        index = await run_in_threadpool(get_national_index)
    except (OSError, ValueError, TypeError):
        raise HTTPException(503, detail="已审核本地资料暂不可用；未查询外部网站或生成答案。") from None
    try:
        index.validate_request(request)
    except ValueError:
        raise HTTPException(422, detail="该城市或场馆尚未收录，或城市与场馆不对应；未改查其他城市。") from None
    try:
        return await run_in_threadpool(lambda: index.search(request))
    except (ValueError, TypeError):
        raise HTTPException(503, detail="本地资料检索未完成；未自动切换模型或外部搜索。") from None
