"""Normalize AMap's walking API; no route synthesis or itinerary mutation."""

import logging
import math
from datetime import datetime, timezone

import httpx

from app.integrations.amap import AmapError, AmapTimeoutError, REQUEST_TIMEOUT
from app.schemas.route import Coordinate, RouteEndpoint, WalkingRoute, WalkingRouteRequest, WalkingRouteResponse


WALKING_URL = "https://restapi.amap.com/v3/direction/walking"
logger = logging.getLogger(__name__)
INVALID_DATA = "步行路线服务返回的数据格式异常，请稍后重试。"


def _number(value: object) -> float:
    # AMap documents numeric strings. Reject booleans and non-finite values.
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError("Invalid number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Non-finite number")
    return result


def _segment(value: object) -> list[Coordinate]:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Missing polyline")
    points: list[Coordinate] = []
    for pair in value.split(";"):
        coordinates = pair.split(",")
        if len(coordinates) != 2:
            raise ValueError("Invalid polyline coordinate")
        longitude, latitude = (_number(item.strip()) for item in coordinates)
        # Validation of coordinate bounds is also enforced by WalkingRoute.
        points.append((longitude, latitude))
    return points


def _location(endpoint: RouteEndpoint) -> str:
    # The Web Service requires longitude,latitude with at most six decimals.
    return f"{endpoint.longitude:.6f},{endpoint.latitude:.6f}"


def _convert_route(data: object) -> WalkingRoute | None:
    if not isinstance(data, dict):
        raise AmapError(INVALID_DATA)
    if data.get("status") != "1":
        raise AmapError("高德步行路线查询失败，请检查服务配置或稍后重试。")
    route = data.get("route")
    if not isinstance(route, dict) or not isinstance(route.get("paths"), list):
        raise AmapError(INVALID_DATA)
    paths = route["paths"]
    if not paths:
        return None

    # This milestone displays one upstream proposal, not a route-ranking engine.
    path = paths[0]
    if not isinstance(path, dict) or not isinstance(path.get("steps"), list) or not path["steps"]:
        raise AmapError(INVALID_DATA)
    try:
        segments = []
        for step in path["steps"]:
            if not isinstance(step, dict):
                raise ValueError("Invalid step")
            segments.append(_segment(step.get("polyline")))
        return WalkingRoute(
            distance_meters=_number(path.get("distance")),
            duration_seconds=_number(path.get("duration")),
            segments=segments,
        )
    except (ValueError, OverflowError):
        # Includes Pydantic errors. Never forward upstream data or raw errors.
        raise AmapError(INVALID_DATA) from None


class AmapWalkingClient:
    def __init__(self, client: httpx.AsyncClient, key: str) -> None:
        self._client = client
        self._key = key

    async def walking(self, request: WalkingRouteRequest) -> WalkingRouteResponse:
        origin, destination = request.origin, request.destination
        origin_location, destination_location = _location(origin), _location(destination)
        if origin.place_id == destination.place_id or origin_location == destination_location:
            return WalkingRouteResponse(status="same_place", queried_at=datetime.now(timezone.utc))

        for attempt in (1, 2):
            try:
                response = await self._client.get(
                    WALKING_URL,
                    params={
                        "key": self._key,
                        "origin": origin_location,
                        "destination": destination_location,
                        "origin_id": origin.place_id,
                        "destination_id": destination.place_id,
                        "output": "JSON",
                    },
                    timeout=REQUEST_TIMEOUT,
                    follow_redirects=False,
                )
                response.raise_for_status()
                break
            except httpx.HTTPError as error:
                logger.warning("AMap walking request failed (%s, attempt %d/2)", type(error).__name__, attempt)
                if attempt == 1 and isinstance(error, (httpx.ConnectTimeout, httpx.ConnectError)):
                    # Match the existing bounded POI recovery for this read-only GET.
                    continue
                if isinstance(error, httpx.TimeoutException):
                    raise AmapTimeoutError("步行路线查询超时，请稍后重试。") from None
                raise AmapError("暂时无法连接步行路线服务，请稍后重试。") from None

        try:
            data = response.json()
        except ValueError:
            raise AmapError(INVALID_DATA) from None
        route = _convert_route(data)
        return WalkingRouteResponse(
            status="ok" if route is not None else "no_route",
            queried_at=datetime.now(timezone.utc),
            route=route,
        )
