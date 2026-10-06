import re
from datetime import date
from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.place import ConfirmedPlace
from app.schemas.route import RouteEndpoint


def clock_minutes(value: str) -> int:
    hour, minute = value.split(":")
    return int(hour) * 60 + int(minute)


def valid_clock(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise ValueError("时间必须是 HH:MM。")
    return value


class StayDuration(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    place_id: str = Field(min_length=1)
    minutes: int = Field(ge=15, le=480)
    source: Literal["default", "user"]

    @model_validator(mode="after")
    def match_default(self) -> Self:
        if self.source == "default" and self.minutes != 60:
            raise ValueError("默认停留时长为 60 分钟。")
        return self


class LunchWindow(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    enabled: bool = True
    start_time: str = "12:00"
    end_time: str = "13:00"

    _valid_times = field_validator("start_time", "end_time", mode="before")(valid_clock)


class ScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_date: date
    end_date: date
    daily_start_time: str
    daily_end_time: str
    accommodation_place: ConfirmedPlace
    must_visit_places: list[ConfirmedPlace] = Field(min_length=1, max_length=6, strict=True)
    duration_settings: list[StayDuration] = Field(min_length=1, max_length=6, strict=True)
    lunch: LunchWindow = Field(default_factory=LunchWindow)

    _valid_times = field_validator("daily_start_time", "daily_end_time", mode="before")(valid_clock)

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def require_iso_dates(cls, value: object) -> date:
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("日期必须是 YYYY-MM-DD。")
        return date.fromisoformat(value)

    @model_validator(mode="after")
    def consistent_inputs(self) -> Self:
        if not 0 <= (self.end_date - self.start_date).days <= 2:
            raise ValueError("仅支持 1 至 3 天。")
        start, end = clock_minutes(self.daily_start_time), clock_minutes(self.daily_end_time)
        if start >= end:
            raise ValueError("每日结束必须晚于开始。")
        if self.lunch.enabled and not (
            start <= clock_minutes(self.lunch.start_time) < clock_minutes(self.lunch.end_time) <= end
        ):
            raise ValueError("午餐时间必须完整位于每日时间窗内。")
        ids = [place.id for place in self.must_visit_places]
        settings_ids = [setting.place_id for setting in self.duration_settings]
        if len(set(ids)) != len(ids) or len(set(settings_ids)) != len(settings_ids) or set(ids) != set(settings_ids):
            raise ValueError("停留时长必须与唯一必去地点一一对应。")
        for place in [self.accommodation_place, *self.must_visit_places]:
            # Reuse walking's safe endpoint identity/coordinate validation.
            RouteEndpoint(place_id=place.id, longitude=place.longitude, latitude=place.latitude)
        return self


class ScheduleEdge(BaseModel):
    id: str
    origin: RouteEndpoint
    destination: RouteEndpoint
    status: Literal["ok", "same_place", "no_route", "timeout", "data_error", "failed"]
    duration_seconds: float | None = None
    duration_minutes: int | None = None
    distance_meters: float | None = None
    source: Literal["amap", "same_place"] = "amap"
    queried_at: AwareDatetime
    message: str | None = None
    used: bool = False


class ScheduleItem(BaseModel):
    kind: Literal["walk", "visit", "wait", "lunch"]
    start_time: str
    end_time: str
    place_id: str | None = None
    from_place_id: str | None = None
    to_place_id: str | None = None
    edge_id: str | None = None
    duration_minutes: int = Field(ge=0)
    duration_source: Literal["default", "user"] | None = None


class ScheduleDay(BaseModel):
    date: date
    items: list[ScheduleItem]
    return_time: str | None = None


class UnscheduledPlace(BaseModel):
    place_id: str
    reason: Literal[
        "time_window", "route_timeout", "no_route", "route_data_error",
        "route_failed", "current_order_not_continued",
    ]
    message: str


class ScheduleResponse(BaseModel):
    status: Literal["complete", "partial", "unscheduled"]
    generated_at: AwareDatetime
    request: ScheduleRequest
    days: list[ScheduleDay]
    unscheduled: list[UnscheduledPlace]
    edges: list[ScheduleEdge]
    rules: list[str]
    unknowns: list[str]
