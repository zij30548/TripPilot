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


class InvalidTransitField(ValueError):
    """Only program-owned paths/codes, never upstream values or exception text."""

    def __init__(self, field: str, reason: str) -> None:
        super().__init__("Invalid transit field")
        self.field = field
        self.reason = reason


class TransitDataError(AmapError):
    def __init__(self, issues: list[InvalidTransitField]) -> None:
        super().__init__(INVALID_DATA)
        # A bounded diagnostic, separate from the unchanged public route contract.
        self.issues = tuple(issues[:4])
        self.summary = "公交参考数据校验未通过，未使用该路段估时。"
        if self.issues:
            field = self.issues[0].field
            detail = ("路线绘图数据异常（并非仅缺少绘图信息）" if field.endswith(".polyline") else
                      "方案总耗时缺失或无效" if field.endswith(".duration") and ".segments[" not in field else
                      "线路或上下车站信息缺失或无效" if field.endswith((".name", ".type", ".departure_stop", ".arrival_stop")) else
                      "分段参考数值无效" if field.endswith((".distance", ".duration")) else
                      "参考票价无效" if field.endswith(".cost") else
                      "方案字段缺失或无效")
            self.summary = f"公交参考数据校验未通过：{detail}；未使用该路段估时。"


def _data_error(issues: list[InvalidTransitField]) -> TransitDataError:
    for issue in issues[:4]:
        logger.warning("AMap transit data rejected field=%s reason=%s", issue.field, issue.reason)
    return TransitDataError(issues)


def _index(value: int) -> str:
    return str(value) if value <= 999 else "overflow"


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


def _number(value: object, field: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise InvalidTransitField(field, "number_type")
    try:
        result = float(value)
    except (ValueError, OverflowError):
        raise InvalidTransitField(field, "number_format") from None
    if not math.isfinite(result) or result < 0 or (positive and result == 0):
        raise InvalidTransitField(field, "number_range")
    return result


def _optional_number(value: object, field: str) -> float | None:
    if value is None or value == "" or value == []:
        return None
    return _number(value, field)


def _text(value: object, field: str, *, required: bool = False) -> str | None:
    if not required and (value is None or value == "" or value == []):
        return None
    if not isinstance(value, str) or not value.strip():
        raise InvalidTransitField(field, "text_required_or_type")
    return value.strip()


def _polyline(value: object, field: str, *, access_walking: bool = False) -> list[Coordinate] | None:
    if value is None or value == "" or value == []:
        return None
    if not isinstance(value, str):
        raise InvalidTransitField(field, "geometry_type")
    result: list[Coordinate] = []
    for pair in value.split(";"):
        parts = pair.split(",")
        if len(parts) != 2:
            raise InvalidTransitField(field, "coordinate_format")
        try:
            longitude, latitude = (float(part.strip()) for part in parts)
        except (ValueError, OverflowError):
            raise InvalidTransitField(field, "coordinate_format") from None
        if not math.isfinite(longitude) or not math.isfinite(latitude):
            raise InvalidTransitField(field, "coordinate_non_finite")
        if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
            raise InvalidTransitField(field, "coordinate_range")
        result.append((longitude, latitude))
    if len(result) >= 2 and all(point == result[0] for point in result[1:]) and access_walking:
        # Observed AMap access steps can describe 1 metre with repeated rounded
        # coordinates. Their valid-but-undrawable geometry is not a missing
        # journey duration: keep the proposal total, retain the instruction and
        # mark this leg incomplete. Never join neighbouring steps across it.
        # Malformed/non-finite/out-of-range coordinates above still fail, as do
        # undrawable ride polylines. No replacement coordinates or time invented.
        logger.info("AMap transit geometry omitted field=%s reason=degenerate_access_walk", field)
        return None
    if len(result) < 2 or all(point == result[0] for point in result[1:]):
        raise InvalidTransitField(field, "geometry_undrawable")
    return result


def _walking(value: object, field: str) -> TransitLeg | None:
    if _absent(value):
        return None
    if not isinstance(value, dict):
        raise InvalidTransitField(field, "object_required")
    steps = value.get("steps")
    if steps is None:
        steps = []
    if not isinstance(steps, list):
        raise InvalidTransitField(f"{field}.steps", "list_required")
    geometry: list[list[Coordinate]] = []
    instructions: list[str] = []
    complete = bool(steps)
    for index, step in enumerate(steps):
        step_field = f"{field}.steps[{_index(index)}]"
        if not isinstance(step, dict):
            raise InvalidTransitField(step_field, "object_required")
        segment = _polyline(step.get("polyline"), f"{step_field}.polyline", access_walking=True)
        if segment is None:
            complete = False
        else:
            geometry.append(segment)
        instruction = _text(step.get("instruction"), f"{step_field}.instruction")
        if instruction:
            instructions.append(instruction)
    return TransitLeg(
        mode="walking", distance_meters=_optional_number(value.get("distance"), f"{field}.distance"),
        duration_seconds=_optional_number(value.get("duration"), f"{field}.duration"),
        instruction="；".join(instructions) or None,
        line_name=None, departure_stop=None, arrival_stop=None,
        geometry=geometry, geometry_complete=complete,
    )


def _busline(value: object, field: str) -> TransitLeg:
    if not isinstance(value, dict):
        raise InvalidTransitField(field, "object_required")
    kind = _text(value.get("type"), f"{field}.type", required=True)
    mode: Literal["bus", "subway"]
    if kind in SUPPORTED_SUBWAY_TYPES:
        mode = "subway"
    elif kind in SUPPORTED_BUS_TYPES:
        mode = "bus"
    else:
        raise UnsupportedPlan("Unsupported vehicle type")
    departure, arrival = value.get("departure_stop"), value.get("arrival_stop")
    if not isinstance(departure, dict):
        raise InvalidTransitField(f"{field}.departure_stop", "object_required")
    if not isinstance(arrival, dict):
        raise InvalidTransitField(f"{field}.arrival_stop", "object_required")
    geometry = _polyline(value.get("polyline"), f"{field}.polyline")
    return TransitLeg(
        mode=mode, distance_meters=_optional_number(value.get("distance"), f"{field}.distance"),
        duration_seconds=_optional_number(value.get("duration"), f"{field}.duration"), instruction=None,
        line_name=_text(value.get("name"), f"{field}.name", required=True),
        departure_stop=_text(departure.get("name"), f"{field}.departure_stop.name", required=True),
        arrival_stop=_text(arrival.get("name"), f"{field}.arrival_stop.name", required=True),
        geometry=[geometry] if geometry is not None else [],
        geometry_complete=geometry is not None,
    )


def _bus(value: object, field: str) -> TransitLeg | None:
    if _absent(value):
        return None
    if not isinstance(value, dict) or not isinstance(value.get("buslines"), list):
        raise InvalidTransitField(f"{field}.buslines", "list_required")
    lines = value["buslines"]
    if not lines:
        return None
    malformed: InvalidTransitField | None = None
    # buslines are alternatives for ONE ride, never consecutive transfers.
    for index, line in enumerate(lines):
        try:
            return _busline(line, f"{field}.buslines[{_index(index)}]")
        except UnsupportedPlan:
            continue
        except InvalidTransitField as error:
            malformed = malformed or error
        except (ValueError, OverflowError):
            malformed = malformed or InvalidTransitField(field, "normalized_leg")
    if malformed is not None:
        raise malformed
    raise UnsupportedPlan("No supported ride alternative")


def _proposal(value: object, field: str = "route.transits[0]") -> TransitRoute:
    if not isinstance(value, dict) or not isinstance(value.get("segments"), list) or not value["segments"]:
        raise InvalidTransitField(f"{field}.segments", "nonempty_list_required")
    duration = _number(value.get("duration"), f"{field}.duration", positive=True)
    walking_distance = _number(value.get("walking_distance"), f"{field}.walking_distance")
    fare = _optional_number(value.get("cost"), f"{field}.cost")
    legs: list[TransitLeg] = []
    for index, segment in enumerate(value["segments"]):
        segment_field = f"{field}.segments[{_index(index)}]"
        if not isinstance(segment, dict):
            raise InvalidTransitField(segment_field, "object_required")
        # Required unsupported modes invalidate the whole proposal. Unknown
        # nonempty segment fields are conservative failures, never dropped rides.
        for key, content in segment.items():
            if key == "railway" and _empty_railway_placeholder(content):
                continue
            if key not in {"walking", "bus", "entrance", "exit"} and not _absent(content):
                raise UnsupportedPlan("Unsupported necessary segment")
        walking, bus = _walking(segment.get("walking"), f"{segment_field}.walking"), _bus(segment.get("bus"), f"{segment_field}.bus")
        if walking is None and bus is None:
            raise InvalidTransitField(segment_field, "empty_segment")
        if walking is not None:
            legs.append(walking)
        if bus is not None:
            legs.append(bus)
    if not any(leg.mode != "walking" for leg in legs):
        raise UnsupportedPlan("Walking alone is not a transit proposal")
    if walking_distance > 0 and not any(leg.mode == "walking" for leg in legs):
        raise InvalidTransitField(f"{field}.walking_distance", "missing_access_walking")
    # Use proposal totals only: route.distance is NOT transit mileage, and leg
    # durations/access walks must NOT be added on top of the total duration.
    return TransitRoute(
        duration_seconds=duration, walking_distance_meters=walking_distance,
        fare_cny=fare, legs=legs, geometry_complete=all(leg.geometry_complete for leg in legs),
    )


def _convert_route(data: object) -> tuple[Literal["ok", "no_route", "unsupported"], TransitRoute | None]:
    if not isinstance(data, dict):
        raise _data_error([InvalidTransitField("response", "object_required")])
    if data.get("status") != "1":
        raise AmapError("高德公交／地铁查询失败，请检查服务配置或稍后重试。")
    route = data.get("route")
    if not isinstance(route, dict) or not isinstance(route.get("transits"), list):
        raise _data_error([InvalidTransitField("route.transits", "list_required")])
    proposals = route["transits"]
    if not proposals:
        return "no_route", None
    issues: list[InvalidTransitField] = []
    for index, proposal in enumerate(proposals):
        try:
            return "ok", _proposal(proposal, f"route.transits[{_index(index)}]")
        except UnsupportedPlan:
            continue
        except InvalidTransitField as error:
            if len(issues) < 4:
                issues.append(error)
        except (ValueError, OverflowError):
            if len(issues) < 4:
                issues.append(InvalidTransitField(f"route.transits[{_index(index)}]", "normalized_route"))
    if issues:
        raise _data_error(issues)
    return "unsupported", None


def _location(endpoint: RouteEndpoint) -> str:
    return f"{endpoint.longitude:.6f},{endpoint.latitude:.6f}"


class AmapTransitClient:
    def __init__(self, client: httpx.AsyncClient, key: str, destination_id: str | None = None) -> None:
        self._client = client
        self._key = key
        from app.destinations import destination_for
        self._city = destination_for(destination_id).citycode if destination_id is not None else "上海"

    def for_destination(self, destination_id: str) -> "AmapTransitClient":
        return AmapTransitClient(self._client, self._key, destination_id)

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
                        "city": self._city, "cityd": self._city, "extensions": "all", "output": "JSON",
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
            raise _data_error([InvalidTransitField("response", "json_parse")]) from None
        status, route = _convert_route(data)
        # These are the only upstream text fields we expose. Fail safely if an
        # upstream service ever reflects this request's credential into them.
        if route is not None and self._key in route.model_dump_json():
            raise _data_error([InvalidTransitField("normalized_route", "credential_reflection")])
        return TransitRouteResponse(status=status, queried_at=datetime.now(timezone.utc), route=route)
