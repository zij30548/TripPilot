from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from api.routes import SafeRouteValidation, get_walking_client, get_transit_client
from tooling.providers.amap_walking import AmapWalkingClient
from tooling.providers.amap_transit import AmapTransitClient
from core.schemas.schedule import ScheduleRequest, ScheduleResponse
from core.schemas.destination import CityScheduleRequest, CityScheduleResponse
from tooling.travel.schedule import add_optional_schedule, build_required_schedule
from tooling.travel.schedule_routes import ScheduleRouteCollector, optional_edges, required_edges
from tooling.travel.schedule_drawing import CityScheduleDelivery, ScheduleDelivery, drawing_for


class SafeScheduleValidation(SafeRouteValidation):
    validation_message = "排程预览参数无效，请检查日期、确认地点、停留时长和午餐时间。"


router = APIRouter(prefix="/trips", tags=["trips"], route_class=SafeScheduleValidation)


@router.post("/schedule-preview", response_model=CityScheduleResponse | CityScheduleDelivery | ScheduleResponse | ScheduleDelivery)
async def schedule_preview(
    request: CityScheduleRequest | ScheduleRequest,
    amap: Annotated[AmapWalkingClient, Depends(get_walking_client)],
    transit: Annotated[AmapTransitClient, Depends(get_transit_client)],
    drawing: Annotated[Literal["walking-v1"] | None, Query()] = None,
) -> ScheduleResponse | ScheduleDelivery:
    city_request = request if isinstance(request, CityScheduleRequest) else None
    if city_request:
        request = city_request.core()
        if request.transport_mode == "transit":
            transit = transit.for_destination(city_request.destination_id)
    collector = ScheduleRouteCollector(transit if request.transport_mode == "transit" else amap, request.transport_mode)
    edges = await collector.collect(required_edges(request)) if request.must_visit_places else []
    baseline = build_required_schedule(request, edges, datetime.now(timezone.utc))
    if not baseline.unscheduled and request.optional_places:
        edges = await collector.collect(optional_edges(request, baseline.days))
    schedule = add_optional_schedule(request, baseline, edges)
    if city_request:
        schedule = CityScheduleResponse(**{**schedule.model_dump(), "request": city_request})
    if drawing is not None:
        delivery = CityScheduleDelivery if city_request else ScheduleDelivery
        return delivery(schedule=schedule, drawing=drawing_for(schedule, collector.walking_drawings))
    return schedule
