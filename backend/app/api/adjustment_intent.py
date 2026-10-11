from datetime import datetime, timezone
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError

from app.api.explanation import BoundedExplanationRoute, get_qwen_http_client, while_connected
from app.config import Settings, get_settings
from app.integrations.qwen import QwenClient, QwenError, strict_json
from app.schemas.adjustment_intent import AdjustmentIntentRequest, AdjustmentIntentResponse
from app.services.adjustment_intent import intent_payload, validate_intent
from app.services.explanation import validate_snapshot

router = APIRouter(prefix="/trips", tags=["trips"], route_class=BoundedExplanationRoute)


@router.post("/adjustment-intent", response_model=AdjustmentIntentResponse)
async def adjustment_intent(raw: Request, settings: Annotated[Settings, Depends(get_settings)],
                            client: Annotated[httpx.AsyncClient, Depends(get_qwen_http_client)]) -> AdjustmentIntentResponse:
    try:
        body = await raw.body()
        strict_json(body)
        request = AdjustmentIntentRequest.model_validate_json(body)
        schedule = validate_snapshot(request.snapshot)
    except (ValidationError, ValueError, RecursionError):
        raise HTTPException(422, detail="调整要求或当前草案快照无效，原方案未改变。") from None
    try:
        payload = intent_payload(schedule, request.instruction, settings.qwen_model)
        content, usage = await while_connected(raw, QwenClient(settings, client).generate(payload))
        selection = validate_intent(content, schedule, request.instruction)
        return AdjustmentIntentResponse(request_id=request.request_id, draft_version=request.draft_version,
                                        schedule_generated_at=schedule.generated_at, interpreted_at=datetime.now(timezone.utc),
                                        model=settings.qwen_model, selection=selection, usage=usage)
    except QwenError as error:
        raise HTTPException(error.status, detail={"code": error.code, "message": "调整解析未完成，原方案未改变。"}) from None
