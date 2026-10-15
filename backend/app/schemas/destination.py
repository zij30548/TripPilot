"""Explicit modern contracts; original v1 models remain unchanged for replay."""
from typing import Self

from pydantic import model_validator

from app.destinations import DestinationId, PlaceRegion
from app.schemas.place import ConfirmedPlace
from app.schemas.trip import TripRequest
from app.schemas.schedule import ScheduleRequest, ScheduleResponse
from app.schemas.candidates import CandidateRequest, CandidateResponse, PlaceCandidate


class LocatedPlace(ConfirmedPlace):
    region: PlaceRegion


def check_places(destination_id: DestinationId, places: list[LocatedPlace]) -> None:
    seen: dict[str, LocatedPlace] = {}
    for place in places:
        if not place.region.belongs_to(destination_id):
            raise ValueError("地点城市归属缺失、矛盾或不属于所选城市，请重新搜索确认。")
        if place.id in seen and seen[place.id] != place:
            raise ValueError("同一地点的快照不一致。")
        seen[place.id] = place


def core_place(place: LocatedPlace) -> dict:
    return place.model_dump(exclude={"region"})


class CityTripRequest(TripRequest):
    destination_id: DestinationId
    accommodation_place: LocatedPlace
    must_visit_places: list[LocatedPlace]

    @model_validator(mode="after")
    def city_consistent(self) -> Self:
        check_places(self.destination_id, [self.accommodation_place, *self.must_visit_places])
        return self


class CityScheduleRequest(ScheduleRequest):
    destination_id: DestinationId
    accommodation_place: LocatedPlace
    must_visit_places: list[LocatedPlace]
    optional_places: list[LocatedPlace]

    @model_validator(mode="after")
    def city_consistent(self) -> Self:
        if len(self.must_visit_places) > 6 or len(self.optional_places) > 3:
            raise ValueError("地点数量超过本阶段范围。")
        check_places(self.destination_id, [self.accommodation_place, *self.must_visit_places, *self.optional_places])
        return self

    def core(self) -> ScheduleRequest:
        data = self.model_dump(mode="json", exclude={"destination_id"})
        data["accommodation_place"] = core_place(self.accommodation_place)
        for name in ("must_visit_places", "optional_places"):
            data[name] = [core_place(p) for p in getattr(self, name)]
        return ScheduleRequest.model_validate(data)


class CityScheduleResponse(ScheduleResponse):
    request: CityScheduleRequest


class CityCandidateRequest(CandidateRequest):
    destination_id: DestinationId
    accommodation_place: LocatedPlace
    must_visit_places: list[LocatedPlace]

    @model_validator(mode="after")
    def city_consistent(self) -> Self:
        check_places(self.destination_id, [self.accommodation_place, *self.must_visit_places])
        return self


class LocatedCandidate(PlaceCandidate):
    place: LocatedPlace


class CityCandidateResponse(CandidateResponse):
    destination_id: DestinationId
    candidates: list[LocatedCandidate]
