import asyncio
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from api.explanation import get_qwen_http_client, while_connected
from core.config import Settings, get_settings
from tooling.providers.qwen import QwenClient, QwenError, strict_json
from core.schemas.explanation import ExplanationUsage
from core.schemas.knowledge import KnowledgeRequest
from core.schemas.knowledge_answer import AnswerRequest, AnswerResponse
from tooling.travel.knowledge import get_index
from tooling.travel.knowledge_answer import answer_payload, render_answer

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.post("/answer", response_model=AnswerResponse)
async def answer(raw: Request, settings: Annotated[Settings, Depends(get_settings)],
                 client: Annotated[httpx.AsyncClient, Depends(get_qwen_http_client)]) -> AnswerResponse:
    body = bytearray()
    try:
        async with asyncio.timeout(5):
            async for chunk in raw.stream():
                if len(body) + len(chunk) > 4096:
                    raise HTTPException(413, detail="问题请求超过大小限制。")
                body.extend(chunk)
        strict_json(bytes(body))
        request = AnswerRequest.model_validate_json(bytes(body))
    except TimeoutError:
        raise HTTPException(408, detail="接收问题超时。") from None
    except (ValueError, TypeError, RecursionError):
        raise HTTPException(422, detail="问题、馆区或资料版本无效，请重新检索。") from None
    try:
        index = await run_in_threadpool(get_index)
        if request.corpus_version != index.corpus.corpus_version or request.corpus_hash != index.hash:
            raise HTTPException(409, detail="资料版本已变化，请重新检索后再主动生成回答。")
        retrieval = await run_in_threadpool(lambda: index.search(KnowledgeRequest(
            venue_id=request.venue_id, question=request.question, limit=5)))
    except (OSError, ValueError, TypeError):
        raise HTTPException(503, detail="知识库读取失败；不是资料不足，请检查服务后重试。") from None
    if not retrieval.matches:
        return render_answer({"status": "insufficient", "answers": [], "missing": ["not_in_materials"]},
                             request, retrieval, None, ExplanationUsage())
    try:
        payload = answer_payload(retrieval, settings.qwen_model)
        content, usage = await while_connected(raw, QwenClient(settings, client).generate(payload))
        return render_answer(content, request, retrieval, settings.qwen_model, usage)
    except QwenError as error:
        raise HTTPException(error.status, detail={"code": error.code,
            "message": "依据资料回答未完成，检索资料及原行程不变。请主动重试；未自动再次调用。"}) from None
