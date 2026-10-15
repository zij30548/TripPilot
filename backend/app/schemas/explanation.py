"""Bounded, geometry/cost/free-text-free schedule projection, not proof of provenance."""
import math
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from app.schemas.route import Latitude, Longitude, PlaceId, RouteEndpoint
from app.destinations import DestinationId
from app.schemas.schedule import (
    LunchWindow, ScheduleRequest, StayDuration, TransportMode,
)

CONTRACT_VERSION = "schedule-explanation-v1"
Identifier = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")]
Clock = Annotated[str, StringConstraints(strict=True, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")]
DateText = Annotated[str, StringConstraints(strict=True, pattern=r"^\d{4}-\d{2}-\d{2}$")]
EdgeStatus = Literal["ok", "same_place", "no_route", "unsupported", "timeout", "data_error", "failed", "budget_exhausted"]
Reason = Literal["time_window", "route_timeout", "no_route", "route_unsupported", "route_budget_exhausted", "route_data_error", "route_failed", "current_order_not_continued"]
Outcome = Literal["scheduled", "time_window", "no_route", "unsupported", "timeout", "data_error", "failed", "budget_exhausted", "day_slot_used"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SnapshotPlace(StrictModel):
    id: PlaceId
    name: Annotated[str, StringConstraints(min_length=1, max_length=160)]
    longitude: Longitude
    latitude: Latitude
    source: Literal["amap"]

    @field_validator("name")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError("Invalid place label")
        return value


class SnapshotStay(StayDuration):
    place_id: PlaceId


class SnapshotRequest(StrictModel):
    destination_id: DestinationId | None = None
    start_date: DateText
    end_date: DateText
    daily_start_time: Clock
    daily_end_time: Clock
    accommodation_place: SnapshotPlace
    must_visit_places: list[SnapshotPlace] = Field(max_length=6)
    optional_places: list[SnapshotPlace] = Field(max_length=3)
    duration_settings: list[SnapshotStay] = Field(min_length=1, max_length=9)
    lunch: LunchWindow
    transport_mode: TransportMode

    def scheduling_input(self) -> ScheduleRequest:
        data = self.model_dump(mode="json", exclude={"destination_id"})
        for place in [data["accommodation_place"], *data["must_visit_places"], *data["optional_places"]]:
            place.update(address=None, category=None)
        return ScheduleRequest.model_validate(data)

    @model_validator(mode="after")
    def existing_rules(self) -> Self:
        self.scheduling_input()
        home = self.accommodation_place
        for place in self.must_visit_places:
            if place.id == home.id and place != home:
                raise ValueError("One POI identity cannot describe two places")
        return self


class SnapshotEdge(StrictModel):
    id: Annotated[str, StringConstraints(pattern=r"^e(?:[0-9]|[12][0-9])$")]
    origin: RouteEndpoint
    destination: RouteEndpoint
    transport_mode: TransportMode
    status: EdgeStatus
    duration_seconds: Annotated[float, Field(ge=0, le=604800, allow_inf_nan=False)] | None
    duration_minutes: Annotated[int, Field(ge=0, le=10080)] | None
    source: Literal["amap", "same_place"]
    queried_at: AwareDatetime
    used: bool

    @model_validator(mode="after")
    def estimates(self) -> Self:
        same = self.origin.place_id == self.destination.place_id or (
            self.origin.longitude == self.destination.longitude and self.origin.latitude == self.destination.latitude)
        if (self.status == "same_place") != same:
            raise ValueError("Invalid local same-place result")
        if self.transport_mode == "walking" and self.status == "unsupported":
            raise ValueError("Unsupported is transit-only")
        if self.status in ("ok", "same_place"):
            if self.duration_seconds is None or self.duration_minutes != math.ceil(self.duration_seconds / 60):
                raise ValueError("Movement rounding mismatch")
            if same:
                if self.source != "same_place" or self.duration_seconds != 0:
                    raise ValueError("Same-place movement must be local zero")
            elif self.source != "amap" or self.duration_seconds <= 0:
                raise ValueError("Invalid reference duration")
        elif self.duration_seconds is not None or self.duration_minutes is not None or self.source != "amap" or self.used:
            raise ValueError("Failed edges cannot provide movement estimates")
        return self


class SnapshotItem(StrictModel):
    kind: Literal["walk", "transit", "visit", "wait", "lunch"]
    start_time: Clock
    end_time: Clock
    duration_minutes: int = Field(ge=0, le=1440)
    place_id: PlaceId | None
    from_place_id: PlaceId | None
    to_place_id: PlaceId | None
    edge_id: Identifier | None
    duration_source: Literal["default", "user"] | None


class SnapshotDay(StrictModel):
    date: DateText
    items: list[SnapshotItem] = Field(max_length=40)
    return_time: Clock | None


class SnapshotUnscheduled(StrictModel):
    place_id: PlaceId
    reason: Reason


class SnapshotAttempt(StrictModel):
    date: DateText
    outcome: Outcome


class SnapshotOptional(StrictModel):
    place_id: PlaceId
    scheduled_date: DateText | None
    not_attempted_reason: Literal["must_incomplete"] | None
    attempts: list[SnapshotAttempt] = Field(max_length=3)


class ScheduleSnapshot(StrictModel):
    generated_at: AwareDatetime
    status: Literal["complete", "partial", "unscheduled"]
    request: SnapshotRequest
    days: list[SnapshotDay] = Field(min_length=1, max_length=3)
    edges: list[SnapshotEdge] = Field(max_length=29)
    unscheduled: list[SnapshotUnscheduled] = Field(max_length=6)
    optional_results: list[SnapshotOptional] = Field(max_length=3)


class ExplanationRequest(StrictModel):
    contract_version: Literal["schedule-explanation-v1"]
    request_id: Identifier
    snapshot: ScheduleSnapshot


MessageCode = Literal["arrangement", "visits", "movement", "reserved_time", "return_to_stay", "not_arranged", "optional_attempts", "empty_day"]


class Highlight(StrictModel):
    message_code: MessageCode
    fact_ids: list[Identifier] = Field(min_length=1, max_length=3)


class ModelSelection(StrictModel):
    highlights: list[Highlight] = Field(min_length=1, max_length=6)


class RenderedHighlight(Highlight):
    text: str


class ExplanationUsage(StrictModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class ExplanationResponse(StrictModel):
    contract_version: Literal["schedule-explanation-v1"] = CONTRACT_VERSION
    request_id: Identifier
    schedule_generated_at: AwareDatetime
    explained_at: AwareDatetime
    model: str
    highlights: list[RenderedHighlight]
    limitations: list[str]
    usage: ExplanationUsage
