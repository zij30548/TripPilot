"""chat-2's local working copy; not a schedulable TripRequest or archive."""
from typing import Annotated, Literal, Self
import re

from pydantic import AfterValidator, Field, StringConstraints, model_validator

from app.chat_cities import city_for
from app.schemas.chat import Identifier, Strict, Timestamp, TurnState, plain_text
from app.schemas.destination import LocatedPlace


def valid_city(value: str) -> str:
    city_for(value)
    return value


CityId = Annotated[str, StringConstraints(pattern=r"^[0-9]{6}$"), AfterValidator(valid_city)]
PlaceRole = Literal["accommodation", "required"]
Clarification = Literal["city_ambiguous", "city_confirmation_required", "multi_city", "missing_place", "unsupported"]
Keyword = Annotated[str, StringConstraints(min_length=1, max_length=80, pattern=r"\S"), AfterValidator(plain_text)]
PlaceId = Annotated[str, StringConstraints(min_length=1, max_length=120, pattern=r"\S"), AfterValidator(plain_text)]


def checked_place(place: LocatedPlace) -> None:
    for value, maximum in ((place.id, 120), (place.name, 200), (place.address, 1000), (place.category, 200)):
        if value is not None and (not value or len(value) > maximum or plain_text(value) != value):
            raise ValueError("place_text_invalid")


class Batch(Strict):
    id: Identifier
    city_id: CityId
    keyword: Keyword
    role: PlaceRole
    queried_at: Timestamp
    places: list[LocatedPlace] = Field(max_length=20)

    @model_validator(mode="after")
    def unique_places(self) -> Self:
        if len({place.id for place in self.places}) != len(self.places):
            raise ValueError("duplicate_poi_identity")
        for place in self.places:
            checked_place(place)
        return self


class ChatPlacesWorkspace(Strict):
    schema_version: Literal["chat-places-v1"] = "chat-places-v1"
    conversation_id: Identifier
    intent_revision: int = Field(ge=0, le=1000)
    revision: int = Field(ge=0, le=10000)
    city_id: CityId | None = None
    accommodation: LocatedPlace | None = None
    required_places: list[LocatedPlace] = Field(default_factory=list, max_length=6)
    batch: Batch | None = None
    last_operation: TurnState | None = None
    clarification: Clarification | None = None
    proposed_city_id: CityId | None = None
    request_text: str | None = Field(default=None, min_length=1, max_length=2000)
    stale: bool = False

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if len({place.id for place in self.required_places}) != len(self.required_places):
            raise ValueError("duplicate_required_place")
        if self.batch is not None and self.batch.city_id != self.city_id:
            raise ValueError("batch_city_mismatch")
        if self.stale and self.batch is not None:
            raise ValueError("stale_batch_not_allowed")
        if self.last_operation is not None and self.last_operation.error_code is not None and re.fullmatch(
                r"[a-z][a-z0-9_]{0,79}", self.last_operation.error_code) is None:
            raise ValueError("invalid_operation_error")
        if self.city_id is None and (self.accommodation is not None or self.required_places or self.batch is not None):
            raise ValueError("city_confirmation_required")
        if self.city_id is not None:
            from app.chat_cities import region_belongs_to
            places = [*self.required_places, *([self.accommodation] if self.accommodation else []),
                      *(self.batch.places if self.batch else [])]
            seen: dict[str, LocatedPlace] = {}
            for place in places:
                checked_place(place)
                if place.id in seen and seen[place.id] != place:
                    raise ValueError("inconsistent_place_snapshot")
                seen[place.id] = place
            if any(not region_belongs_to(place.region, self.city_id) for place in places):
                raise ValueError("place_city_mismatch")
        return self


class Command(Strict):
    operation_id: Identifier
    expected_intent_revision: int = Field(ge=0, le=1000)
    expected_revision: int = Field(ge=0, le=10000)


class SetCity(Command):
    action: Literal["set_city"]
    city_id: CityId


class Search(Command):
    action: Literal["search"]
    keyword: Keyword
    role: PlaceRole


class Ask(Command):
    action: Literal["ask"]
    text: Annotated[str, AfterValidator(plain_text)] = Field(min_length=1, max_length=2000, pattern=r"\S")


class Confirm(Command):
    action: Literal["confirm"]
    batch_id: Identifier
    place_id: PlaceId
    role: PlaceRole


class Remove(Command):
    action: Literal["remove"]
    place_id: PlaceId
    role: PlaceRole


class Sync(Command):
    action: Literal["sync"]


class Cancel(Command):
    action: Literal["cancel"]


ChatPlacesRequest = Annotated[SetCity | Search | Ask | Confirm | Remove | Sync | Cancel, Field(discriminator="action")]


class SearchArguments(Strict):
    city_id: CityId
    keyword: Keyword
    role: PlaceRole


class ClarificationArguments(Strict):
    reason: Literal["city_ambiguous", "multi_city", "missing_place", "unsupported"]


class SearchTool(Strict):
    name: Literal["search_places"]
    arguments: SearchArguments


class ClarificationTool(Strict):
    name: Literal["request_clarification"]
    arguments: ClarificationArguments


ChatPlaceTool = Annotated[SearchTool | ClarificationTool, Field(discriminator="name")]
