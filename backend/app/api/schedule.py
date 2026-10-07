from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.routes import SafeRouteValidation, get_walking_client
from app.integrations.amap_walking import AmapWalkingClient
from app.schemas.schedule import ScheduleRequest, ScheduleResponse
from app.services.schedule import add_optional_schedule, build_required_schedule
from app.services.schedule_routes import ScheduleRouteCollector, optional_edges, required_edges


class SafeScheduleValidation(SafeRouteValidation):
    validation_message = "排程预览参数无效，请检查日期、确认地点、停留时长和午餐时间。"


router = APIRouter(prefix="/trips", tags=["trips"], route_class=SafeScheduleValidation)


@router.post("/schedule-preview", response_model=ScheduleResponse)
async def schedule_preview(
    request: ScheduleRequest,
    amap: Annotated[AmapWalkingClient, Depends(get_walking_client)],
) -> ScheduleResponse:
    collector = ScheduleRouteCollector(amap)
    edges = await collector.collect(required_edges(request)) if request.must_visit_places else []
    baseline = build_required_schedule(request, edges, datetime.now(timezone.utc))
    if not baseline.unscheduled and request.optional_places:
        edges = await collector.collect(optional_edges(request, baseline.days))
    return add_optional_schedule(request, baseline, edges)
