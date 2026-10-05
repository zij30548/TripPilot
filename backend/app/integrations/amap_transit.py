"""One explainable city-transit proposal from the fixed AMap v3 upstream."""

import logging
import math
from datetime import datetime, timezone
from typing import Literal

import httpx

from app.integrations.amap import AmapError, AmapTimeoutError, REQUEST_TIMEOUT
from app.schemas.route import Coordinate, RouteEndpoint
from app.schemas.transit import TransitLeg, TransitRoute, TransitRouteRequest, TransitRouteResponse


TRANSIT_URL = "https://restapi.amap.com/v3/direction/transit/integrated"
INVALID_DATA = "公交／地铁路线服务返回的数据格式异常，请稍后重试。"
logger = logging.getLogger(__name__)
SUPPORTED_BUS_TYPES = {"普通公交", "普通公交线路", "公交线路"}
SUPPORTED_SUBWAY_TYPES = {"地铁线路", "地铁"}


class UnsupportedPlan(ValueError):
    """A required mode is outside this milestone, not an empty route."""


def _absent(value: object) -> bool:
    # v3 uses [] as well as empty strings for missing optional scalar fields.
    return value is None or value == "" or value == [] or value == {}


def _empty_railway_placeholder(value: object) -> bool:
    # v3 extensions=all emits this empty shell even for ordinary city transit.
    # Recognize only those documented list placeholders, not arbitrary nested
    # objects that could hide a necessary railway segment or a new mode.
    return (
        isinstance(value, dict)
        and set(value).issubset({"via_stops", "alters", "spaces"})
        and all(isinstance(item, list) and not item for item in value.values())
    )


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError("Invalid numeric value")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError("Invalid non-negative number")
    return result


def _optional_number(value: object) -> float | None:
    if value is None or value == "" or value == []:
        return None
    return _number(value)


def _text(value: object, *, required: bool = False) -> str | None:
    if not required and (value is None or value == "" or value == []):
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Missing or invalid text")
    return value.strip()


def _polyline(value: object) -> list[Coordinate] | None:
    if value is None or value == "" or value == []:
        return None
    if not isinstance(value, str):
        raise ValueError("Invalid geometry")
    result: list[Coordinate] = []
    for pair in value.split(";"):
        parts = pair.split(",")
        if len(parts) != 2:
            raise ValueError("Invalid coordinate pair")
        longitude, latitude = (float(part.strip()) for part in parts)
        if not math.isfinite(longitude) or not math.isfinite(latitude):
            raise ValueError("Non-finite coordinate")
        if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
            raise ValueError("Out-of-bounds coordinate")
        result.append((longitude, latitude))
    if len(result) < 2 or all(point == result[0] for point in result[1:]):
        raise ValueError("Undrawable geometry")
    return result


def _walking(value: object) -> TransitLeg | None:
    if _absent(value):
        return None
    if not isinstance(value, dict):
        raise ValueError("Invalid walking leg")
    steps = value.get("steps")
    if steps is None:
        steps = []
    if not isinstance(steps, list):
        raise ValueError("Invalid walking steps")
    geometry: list[list[Coordinate]] = []
    instructions: list[str] = []
    complete = bool(steps)
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError("Invalid walking step")
        segment = _polyline(step.get("polyline"))
        if segment is None:
            complete = False
        else:
            geometry.append(segment)
        instruction = _text(step.get("instruction"))
        if instruction:
            instructions.append(instruction)
    return TransitLeg(
        mode="walking", distance_meters=_optional_number(value.get("distance")),
        duration_seconds=_optional_number(value.get("duration")),
        instruction="；".join(instructions) or None,
        line_name=None, departure_stop=None, arrival_stop=None,
        geometry=geometry, geometry_complete=complete,
    )


def _busline(value: object) -> TransitLeg:
    if not isinstance(value, dict):
        raise ValueError("Invalid busline")
    kind = _text(value.get("type"), required=True)
    mode: Literal["bus", "subway"]
    if kind in SUPPORTED_SUBWAY_TYPES:
        mode = "subway"
    elif kind in SUPPORTED_BUS_TYPES:
        mode = "bus"
    else:
        raise UnsupportedPlan("Unsupported vehicle type")
    departure, arrival = value.get("departure_stop"), value.get("arrival_stop")
    if not isinstance(departure, dict) or not isinstance(arrival, dict):
        raise ValueError("Missing ride stops")
    geometry = _polyline(value.get("polyline"))
    return TransitLeg(
        mode=mode, distance_meters=_optional_number(value.get("distance")),
        duration_seconds=_optional_number(value.get("duration")), instruction=None,
        line_name=_text(value.get("name"), required=True),
        departure_stop=_text(departure.get("name"), required=True),
        arrival_stop=_text(arrival.get("name"), required=True),
        geometry=[geometry] if geometry is not None else [],
        geometry_complete=geometry is not None,
    )


def _bus(value: object) -> TransitLeg | None:
    if _absent(value):
        return None
    if not isinstance(value, dict) or not isinstance(value.get("buslines"), list):
        raise ValueError("Invalid bus leg")
    lines = value["buslines"]
    if not lines:
        return None
    malformed = False
    # buslines are alternatives for ONE ride, never consecutive transfers.
    for line in lines:
        try:
            return _busline(line)
        except UnsupportedPlan:
            continue
        except (ValueError, OverflowError):
            malformed = True
    if malformed:
        raise ValueError("No complete valid alternative")
    raise UnsupportedPlan("No supported ride alternative")


def _proposal(value: object) -> TransitRoute:
    if not isinstance(value, dict) or not isinstance(value.get("segments"), list) or not value["segments"]:
        raise ValueError("Invalid transit proposal")
    duration = _number(value.get("duration"))
    walking_distance = _number(value.get("walking_distance"))
    fare = _optional_number(value.get("cost"))
    legs: list[TransitLeg] = []
    for segment in value["segments"]:
        if not isinstance(segment, dict):
            raise ValueError("Invalid transit segment")
        # Required unsupported modes invalidate the whole proposal. Unknown
        # nonempty segment fields are conservative failures, never dropped rides.
        for field, content in segment.items():
            if field == "railway" and _empty_railway_placeholder(content):
                continue
            if field not in {"walking", "bus", "entrance", "exit"} and not _absent(content):
                raise UnsupportedPlan("Unsupported necessary segment")
        walking, bus = _walking(segment.get("walking")), _bus(segment.get("bus"))
        if walking is None and bus is None:
            raise ValueError("Empty transit segment")
        if walking is not None:
            legs.append(walking)
        if bus is not None:
            legs.append(bus)
    if not any(leg.mode != "walking" for leg in legs):
        raise UnsupportedPlan("Walking alone is not a transit proposal")
    if walking_distance > 0 and not any(leg.mode == "walking" for leg in legs):
        raise ValueError("Missing necessary access-walking information")
    # Use proposal totals only: route.distance is NOT transit mileage, and leg
    # durations/access walks must NOT be added on top of the total duration.
    return TransitRoute(
        duration_seconds=duration, walking_distance_meters=walking_distance,
        fare_cny=fare, legs=legs, geometry_complete=all(leg.geometry_complete for leg in legs),
    )


def _convert_route(data: object) -> tuple[Literal["ok", "no_route", "unsupported"], TransitRoute | None]:
    if not isinstance(data, dict):
        raise AmapError(INVALID_DATA)
    if data.get("status") != "1":
        raise AmapError("高德公交／地铁查询失败，请检查服务配置或稍后重试。")
    route = data.get("route")
    if not isinstance(route, dict) or not isinstance(route.get("transits"), list):
        raise AmapError(INVALID_DATA)
    proposals = route["transits"]
    if not proposals:
        return "no_route", None
    malformed = False
    for proposal in proposals:
        try:
            return "ok", _proposal(proposal)
        except UnsupportedPlan:
            continue
        except (ValueError, OverflowError):
            malformed = True
    if malformed:
        raise AmapError(INVALID_DATA)
    return "unsupported", None


def _location(endpoint: RouteEndpoint) -> str:
    return f"{endpoint.longitude:.6f},{endpoint.latitude:.6f}"


class AmapTransitClient:
    def __init__(self, client: httpx.AsyncClient, key: str) -> None:
        self._client = client
        self._key = key

    async def transit(self, request: TransitRouteRequest) -> TransitRouteResponse:
        origin, destination = request.origin, request.destination
        origin_location, destination_location = _location(origin), _location(destination)
        if origin.place_id == destination.place_id or origin_location == destination_location:
            return TransitRouteResponse(status="same_place", queried_at=datetime.now(timezone.utc))
        for attempt in (1, 2):
            try:
                response = await self._client.get(
                    TRANSIT_URL,
                    params={
                        "key": self._key, "origin": origin_location, "destination": destination_location,
                        "city": "上海", "cityd": "上海", "extensions": "all", "output": "JSON",
                    },
                    timeout=REQUEST_TIMEOUT, follow_redirects=False,
                )
                response.raise_for_status()
                break
            except httpx.HTTPError as error:
                logger.warning("AMap transit request failed (%s, attempt %d/2)", type(error).__name__, attempt)
                if attempt == 1 and isinstance(error, (httpx.ConnectTimeout, httpx.ConnectError)):
                    continue
                if isinstance(error, httpx.TimeoutException):
                    raise AmapTimeoutError("公交／地铁路线查询超时，请稍后重试。") from None
                raise AmapError("暂时无法连接公交／地铁路线服务，请稍后重试。") from None
        try:
            data = response.json()
        except ValueError:
            raise AmapError(INVALID_DATA) from None
        status, route = _convert_route(data)
        # These are the only upstream text fields we expose. Fail safely if an
        # upstream service ever reflects this request's credential into them.
        if route is not None and self._key in route.model_dump_json():
            raise AmapError(INVALID_DATA)
        return TransitRouteResponse(status=status, queried_at=datetime.now(timezone.utc), route=route)
