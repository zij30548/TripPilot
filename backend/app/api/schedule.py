from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.api.routes import SafeRouteValidation, get_walking_client, get_transit_client
from app.integrations.amap_walking import AmapWalkingClient
from app.integrations.amap_transit import AmapTransitClient
from app.schemas.schedule import ScheduleRequest, ScheduleResponse
from app.services.schedule import add_optional_schedule, build_required_schedule
from app.services.schedule_routes import ScheduleRouteCollector, optional_edges, required_edges
from app.services.schedule_drawing import ScheduleDelivery, drawing_for


class SafeScheduleValidation(SafeRouteValidation):
    validation_message = "排程预览参数无效，请检查日期、确认地点、停留时长和午餐时间。"


router = APIRouter(prefix="/trips", tags=["trips"], route_class=SafeScheduleValidation)


@router.post("/schedule-preview", response_model=ScheduleResponse | ScheduleDelivery)
async def schedule_preview(
    request: ScheduleRequest,
    amap: Annotated[AmapWalkingClient, Depends(get_walking_client)],
    transit: Annotated[AmapTransitClient, Depends(get_transit_client)],
    drawing: Annotated[Literal["walking-v1"] | None, Query()] = None,
) -> ScheduleResponse | ScheduleDelivery:
    collector = ScheduleRouteCollector(transit if request.transport_mode == "transit" else amap, request.transport_mode)
    edges = await collector.collect(required_edges(request)) if request.must_visit_places else []
    baseline = build_required_schedule(request, edges, datetime.now(timezone.utc))
    if not baseline.unscheduled and request.optional_places:
        edges = await collector.collect(optional_edges(request, baseline.days))
    schedule = add_optional_schedule(request, baseline, edges)
    if drawing is not None:
        return ScheduleDelivery(schedule=schedule, drawing=drawing_for(schedule, collector.walking_drawings))
    return schedule
