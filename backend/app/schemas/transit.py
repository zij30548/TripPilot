"""Transit's own units and partial-geometry contract, separate from walking."""

from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.schemas.route import Coordinate, RouteEndpoint


NonNegative = Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]
Geometry = list[Annotated[list[Coordinate], Field(min_length=2)]]


class TransitRouteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: RouteEndpoint
    destination: RouteEndpoint


class TransitLeg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["walking", "bus", "subway"]
    distance_meters: NonNegative | None
    duration_seconds: NonNegative | None
    instruction: str | None
    line_name: str | None
    departure_stop: str | None
    arrival_stop: str | None
    # Upstream step boundaries survive; never connect gaps with synthetic lines.
    geometry: Geometry
    geometry_complete: bool

    @model_validator(mode="after")
    def require_consistent_leg(self) -> Self:
        if self.mode != "walking" and not all(
            value and value.strip() for value in (self.line_name, self.departure_stop, self.arrival_stop)
        ):
            raise ValueError("A ride requires its line and both stops")
        if self.mode == "walking" and any(
            value is not None for value in (self.line_name, self.departure_stop, self.arrival_stop)
        ):
            raise ValueError("Walking cannot contain a ride line")
        if self.geometry_complete and not self.geometry:
            raise ValueError("Complete geometry cannot be empty")
        for segment in self.geometry:
            if all(point == segment[0] for point in segment[1:]):
                raise ValueError("Geometry must contain a drawable segment")
        return self


class TransitRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Already includes access/transfer walking: do not sum leg times into it.
    duration_seconds: Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
    walking_distance_meters: NonNegative
    fare_cny: NonNegative | None
    geometry_complete: bool
    legs: Annotated[list[TransitLeg], Field(min_length=1)]

    @model_validator(mode="after")
    def require_transit(self) -> Self:
        if not any(leg.mode in ("bus", "subway") for leg in self.legs):
            raise ValueError("A transit plan must include a supported ride")
        if self.geometry_complete != all(leg.geometry_complete for leg in self.legs):
            raise ValueError("Geometry completeness must match its legs")
        return self


class TransitRouteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "no_route", "unsupported", "same_place"]
    source: Literal["amap"] = "amap"
    queried_at: AwareDatetime
    selection_rule: Literal["first_supported_complete"] = "first_supported_complete"
    route: TransitRoute | None = None

    @model_validator(mode="after")
    def require_matching_status(self) -> Self:
        if (self.status == "ok") != (self.route is not None):
            raise ValueError("Only successful responses contain a route")
        return self
