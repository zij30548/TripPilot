"""Version 1 archives are full normalized results, not arbitrary React state."""
import json
import math
from types import UnionType
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from pydantic import AwareDatetime, BaseModel, Field, StringConstraints, TypeAdapter, model_validator

from app.schemas.explanation import ScheduleSnapshot, StrictModel
from app.schemas.schedule import ScheduleResponse
from app.schemas.trip import TripRequest
from app.services.explanation import compact_schedule, validate_snapshot
from app.services.schedule import build_schedule
from app.schemas.destination import CityTripRequest, CityScheduleRequest, CityScheduleResponse, core_place

MAX_BODY_BYTES = 2 * 1024 * 1024
VERSION = "saved-trip-v1"
RecordId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
Name = Annotated[str, StringConstraints(min_length=1, max_length=80)]
CostText = Annotated[str, StringConstraints(max_length=256)]


def bounded_tree(value: Any, depth: int = 0, count: list[int] | None = None) -> None:
    count = [0] if count is None else count
    count[0] += 1
    if depth > 24 or count[0] > 200_000:
        raise ValueError("Archive is too complex")
    if isinstance(value, str) and len(value) > 1024:
        raise ValueError("Archive string too long")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Nonfinite archive number")
    if isinstance(value, (dict, list)):
        if len(value) > (64 if isinstance(value, dict) else 10_000):
            raise ValueError("Archive collection too large")
        for entry in value.values() if isinstance(value, dict) else value:
            bounded_tree(entry, depth + 1, count)


def exact_model_fields(value: Any, annotation: Any) -> None:
    """Require all v1 wire fields, including defaults; reject nested extras.

    Reuses model field definitions instead of another handwritten schedule schema.
    Pydantic strict JSON validation subsequently checks all scalar/enum types.
    """
    origin, args = get_origin(annotation), get_args(annotation)
    if origin is Annotated:
        exact_model_fields(value, args[0])
    elif origin in (Union, UnionType):
        for variant in args:
            if variant is not type(None) and value is not None:
                exact_model_fields(value, variant)
    elif isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if not isinstance(value, dict) or set(value) != set(annotation.model_fields):
            raise ValueError("Unknown or missing archive fields")
        for key, field in annotation.model_fields.items():
            exact_model_fields(value[key], field.annotation)
    elif origin is list and isinstance(value, list):
        for item in value:
            exact_model_fields(item, args[0])


class SavedCosts(StrictModel):
    tickets: dict[str, CostText] = Field(max_length=9)
    meals: dict[str, CostText] = Field(max_length=3)
    accommodation: CostText
    other: CostText


class SavedSnapshot(StrictModel):
    schema_version: Literal["saved-trip-v1"]
    requirements: TripRequest
    schedule: ScheduleResponse
    costs: SavedCosts

    @model_validator(mode="before")
    @classmethod
    def bounded_exact(cls, value: Any) -> Any:
        bounded_tree(value)
        exact_model_fields(value, cls)
        # A before-model validator materializes JSON into Python objects. Parse
        # temporal scalars in JSON mode explicitly, retaining strict numeric types.
        return {**value,
                "requirements": TripRequest.model_validate_json(json.dumps(value["requirements"]), strict=True),
                "schedule": ScheduleResponse.model_validate_json(json.dumps(value["schedule"]), strict=True)}

    @model_validator(mode="after")
    def consistent(self) -> "SavedSnapshot":
        req, schedule = self.requirements, self.schedule
        sr = schedule.request
        if any(value.utcoffset().total_seconds() != 0 for value in [schedule.generated_at, *(e.queried_at for e in schedule.edges)]):
            raise ValueError("Normalized route timestamps must be UTC")
        if (req.start_date != sr.start_date or req.end_date != sr.end_date
                or req.accommodation_place != sr.accommodation_place
                or req.must_visit_places != sr.must_visit_places
                or req.daily_start_time.isoformat(timespec="minutes") != sr.daily_start_time
                or req.daily_end_time.isoformat(timespec="minutes") != sr.daily_end_time
                or req.daily_start_time.second or req.daily_end_time.second
                or req.daily_start_time.microsecond or req.daily_end_time.microsecond):
            raise ValueError("Requirements and official schedule disagree")
        if req.travelers > 1000 or len(req.interests) > 32 or len(req.avoid_places) > 32:
            raise ValueError("Requirements exceed archive limits")
        snapshot = ScheduleSnapshot.model_validate_json(json.dumps(compact_schedule(schedule)))
        validate_snapshot(snapshot)  # directed references + original pure scheduling rules
        expected = build_schedule(sr, schedule.edges, schedule.generated_at)
        if expected != schedule:
            raise ValueError("Full normalized schedule differs from replay")
        for edge in schedule.edges:
            if edge.status in ("ok", "same_place"):
                if edge.message is not None:
                    raise ValueError("Successful edge cannot carry an error")
                if edge.transport_mode == "walking" and not (
                    edge.distance_meters == 0 if edge.status == "same_place"
                    else 0 < edge.distance_meters <= 100_000
                ):
                    raise ValueError("Invalid walking distance")
            elif not edge.message or not edge.message.strip():
                raise ValueError("Failed route needs an explanation")
            route = edge.transit_route
            if route and (len(route.legs) > 60 or any(len(leg.geometry) > 256 for leg in route.legs)
                          or route.walking_distance_meters > 0 and not any(leg.mode == "walking" for leg in route.legs)):
                raise ValueError("Invalid or excessive transit detail")
        visited = {item.place_id for day in schedule.days for item in day.items if item.kind == "visit"}
        if set(self.costs.tickets) != visited or set(self.costs.meals) != {d.date.isoformat() for d in schedule.days}:
            raise ValueError("Costs must cover exactly current visits and trip dates")
        return self


class SavedSnapshotV2(StrictModel):
    """New city-aware wire version; validate the unchanged v1 core by projection."""
    schema_version: Literal["saved-trip-v2"]
    requirements: CityTripRequest
    schedule: CityScheduleResponse
    costs: SavedCosts

    @model_validator(mode="before")
    @classmethod
    def bounded_exact(cls, value: Any) -> Any:
        bounded_tree(value)
        exact_model_fields(value, cls)
        return {**value,
                "requirements": CityTripRequest.model_validate_json(json.dumps(value["requirements"]), strict=True),
                "schedule": CityScheduleResponse.model_validate_json(json.dumps(value["schedule"]), strict=True)}

    @model_validator(mode="after")
    def consistent(self) -> "SavedSnapshotV2":
        req, schedule = self.requirements, self.schedule
        if req.destination_id != schedule.request.destination_id:
            raise ValueError("Destination mismatch")
        if (req.accommodation_place != schedule.request.accommodation_place
                or req.must_visit_places != schedule.request.must_visit_places):
            raise ValueError("Confirmed regions differ")
        requirements = req.model_dump(mode="json", exclude={"destination_id"})
        requirements["accommodation_place"] = core_place(req.accommodation_place)
        requirements["must_visit_places"] = [core_place(p) for p in req.must_visit_places]
        core = schedule.model_dump(mode="json")
        core["request"] = schedule.request.core().model_dump(mode="json")
        # Pure replay does not depend on city, and never certifies upstream provenance.
        SavedSnapshot.model_validate_json(json.dumps({"schema_version": VERSION,
            "requirements": requirements, "schedule": core, "costs": self.costs.model_dump()}), strict=True)
        return self


Snapshot = Annotated[SavedSnapshot | SavedSnapshotV2, Field(discriminator="schema_version")]
snapshot_adapter = TypeAdapter(Snapshot)


class SaveTrip(StrictModel):
    operation_id: RecordId
    name: Name
    snapshot: Snapshot

    @model_validator(mode="after")
    def nonblank_name(self) -> "SaveTrip":
        if not self.name.strip() or any(ord(c) < 32 for c in self.name):
            raise ValueError("Invalid archive name")
        return self


class SavedSummary(StrictModel):
    id: RecordId
    name: Name
    saved_at: AwareDatetime
    schema_version: Literal["saved-trip-v1", "saved-trip-v2"]
    start_date: str
    end_date: str
    transport_mode: Literal["walking", "transit"]
    status: Literal["complete", "partial", "unscheduled"]


class SavedDetail(SavedSummary):
    snapshot: Snapshot


class SavedList(StrictModel):
    items: list[SavedSummary]
    offset: int
    limit: int
    has_more: bool
