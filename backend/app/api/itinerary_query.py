from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError

from app.api.explanation import BoundedExplanationRoute, get_qwen_http_client, while_connected
from app.config import Settings, get_settings
from app.integrations.qwen import QwenError, strict_json
from app.integrations.qwen_tools import QwenToolClient
from app.schemas.itinerary_query import ItineraryQueryRequest, ItineraryQueryResponse

router = APIRouter(prefix="/trips", tags=["trips"], route_class=BoundedExplanationRoute)


@router.post("/itinerary-query", response_model=ItineraryQueryResponse)
async def itinerary_query(raw: Request, settings: Annotated[Settings, Depends(get_settings)],
                          client: Annotated[httpx.AsyncClient, Depends(get_qwen_http_client)]) -> ItineraryQueryResponse:
    try:
        body = await raw.body()
        if len(body) > 4096:
            raise ValueError()
        request = ItineraryQueryRequest.model_validate(strict_json(body))
    except (ValidationError, ValueError, RecursionError):
        raise HTTPException(422, detail="查询或草案日期无效，未执行任何工具。") from None
    try:
        identifier, call, usage = await while_connected(raw, QwenToolClient(settings, client).choose(request))
        return ItineraryQueryResponse(request_id=request.request_id, draft_version=request.draft_version,
                                      cost_version=request.cost_version, dates=request.dates, tool_call_id=identifier,
                                      tool_call=call, model=settings.qwen_model, usage=usage)
    except QwenError as error:
        raise HTTPException(error.status, detail={"code": error.code, "message": "只读查询未完成，未执行工具；原行程与费用未改变。"}) from None
