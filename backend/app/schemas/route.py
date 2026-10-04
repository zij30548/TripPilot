from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator


Longitude = Annotated[float, Field(strict=True, ge=-180, le=180, allow_inf_nan=False)]
Latitude = Annotated[float, Field(strict=True, ge=-90, le=90, allow_inf_nan=False)]
Coordinate = tuple[Longitude, Latitude]
PlaceId = Annotated[str, StringConstraints(
    strict=True, strip_whitespace=True, min_length=1, max_length=128,
    pattern=r"^[A-Za-z0-9_-]+$",
)]


class RouteEndpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    place_id: PlaceId
    longitude: Longitude
    latitude: Latitude


class WalkingRouteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: RouteEndpoint
    destination: RouteEndpoint


class WalkingRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # AMap v3 walking documents a maximum supported route length of 100 km.
    distance_meters: float = Field(gt=0, le=100_000, allow_inf_nan=False)
    duration_seconds: float = Field(gt=0, allow_inf_nan=False)
    # Each upstream step remains independent: never bridge a gap with a fake line.
    segments: Annotated[list[Annotated[list[Coordinate], Field(min_length=2)]], Field(min_length=1)]

    @model_validator(mode="after")
    def require_drawable_geometry(self) -> Self:
        if not any(any(point != segment[0] for point in segment[1:]) for segment in self.segments):
            raise ValueError("Route geometry has no drawable segment")
        return self


class WalkingRouteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "no_route", "same_place"]
    source: Literal["amap"] = "amap"
    queried_at: AwareDatetime
    route: WalkingRoute | None = None

    @model_validator(mode="after")
    def require_matching_status(self) -> Self:
        if (self.status == "ok") != (self.route is not None):
            raise ValueError("Only successful responses contain a route")
        return self
