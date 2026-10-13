"""Optional live drawing delivery. Never part of ScheduleResponse or saved-trip-v1."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.route import RouteEndpoint, WalkingRoute
from app.schemas.schedule import ScheduleResponse

MAX_SEGMENTS = 256
MAX_EDGE_POINTS = 6_000
MAX_BATCH_POINTS = 24_000
MAX_DRAWING_BYTES = 1_048_576


class WalkingDrawing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    edge_id: str
    transport_mode: Literal["walking"] = "walking"
    origin: RouteEndpoint
    destination: RouteEndpoint
    queried_at: str
    status: Literal["available", "missing", "limit_exceeded"]
    route: WalkingRoute | None


class ScheduleDrawing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["walking-drawing-v1"] = "walking-drawing-v1"
    entries: list[WalkingDrawing]


class ScheduleDelivery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schedule: ScheduleResponse
    drawing: ScheduleDrawing


def within_edge_limit(route: WalkingRoute) -> bool:
    return len(route.segments) <= MAX_SEGMENTS and sum(map(len, route.segments)) <= MAX_EDGE_POINTS


def drawing_for(schedule: ScheduleResponse, routes: dict[str, WalkingRoute | None]) -> ScheduleDrawing:
    """Only actual timeline references; drop a whole attachment, never truncate steps."""
    used = {item.edge_id for day in schedule.days for item in day.items if item.kind == "walk"}
    result = ScheduleDrawing(entries=[])
    points = 0
    for edge in schedule.edges:
        if edge.id not in used or edge.transport_mode != "walking" or edge.status != "ok":
            continue
        route = routes.get(edge.id)
        status = "missing" if edge.id not in routes else "limit_exceeded" if route is None else "available"
        count = sum(map(len, route.segments)) if route else 0
        if route and (not within_edge_limit(route) or points + count > MAX_BATCH_POINTS):
            route, status = None, "limit_exceeded"
        entry = WalkingDrawing(edge_id=edge.id, origin=edge.origin, destination=edge.destination,
                               queried_at=edge.queried_at.isoformat(), status=status, route=route)
        result.entries.append(entry)
        if len(result.model_dump_json().encode()) > MAX_DRAWING_BYTES:
            entry.route, entry.status = None, "limit_exceeded"
        if entry.route:
            points += count
    # Later metadata also consumes bytes. Keep the final whole-batch bound exact.
    for entry in reversed(result.entries):
        if len(result.model_dump_json().encode()) <= MAX_DRAWING_BYTES:
            break
        if entry.route:
            entry.route, entry.status = None, "limit_exceeded"
    return result
