"""chat-1 intent contracts, deliberately not Place or schedulable TripRequest."""
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from app.chat_cities import CITIES, LEGACY_CITIES, city_for, legacy_city_id
from app.planning_limits import MAX_TRIP_DAYS

def plain_text(value: str) -> str:
    if any((ord(c) < 32 and c not in "\n\t\r") or ord(c) == 127 for c in value):
        raise ValueError("invalid_text")
    return value


def timestamp(value: str) -> str:
    if datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is None:
        raise ValueError("timezone_required")
    return value


Identifier = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=120, pattern=r"\S"), AfterValidator(plain_text)]
Timestamp = Annotated[str, StringConstraints(min_length=10, max_length=40), AfterValidator(timestamp)]
FieldName = Literal["destination_id", "start_date", "end_date", "travelers", "budget_cny", "accommodation_hint", "required_place_hints", "preferences", "transport_mode"]
IssueCode = Literal["ambiguous", "unsupported_scope", "unsupported_action", "unimplemented_preference"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Intent(Strict):
    # chat city reference is intentionally independent of six-city scheduling
    # and saved-trip-v1/v2/v3. Previously persisted slugs remain readable.
    destination_id: str | None = Field(default=None, json_schema_extra={"anyOf": [
        {"type": "string", "enum": [*LEGACY_CITIES, *CITIES]}, {"type": "null"},
    ]})
    start_date: str | None = None
    end_date: str | None = None
    travelers: int | None = Field(default=None, ge=1, le=100)
    budget_cny: str | None = None
    accommodation_hint: ShortText | None = None
    required_place_hints: list[ShortText] = Field(default_factory=list, max_length=6)
    preferences: list[ShortText] = Field(default_factory=list, max_length=8)
    transport_mode: Literal["walking", "transit"] | None = None

    @field_validator("destination_id")
    @classmethod
    def directory_city(cls, value: str | None) -> str | None:
        if value is not None:
            city_for(legacy_city_id(value) or value)
        return value

    @field_validator("start_date", "end_date")
    @classmethod
    def calendar_date(cls, value: str | None) -> str | None:
        if value is not None:
            parsed = date.fromisoformat(value)
            if parsed.isoformat() != value or not 2000 <= parsed.year <= 2100:
                raise ValueError("invalid_calendar_date")
        return value

    @field_validator("budget_cny")
    @classmethod
    def money(cls, value: str | None) -> str | None:
        if value is not None:
            import re
            if not re.fullmatch(r"(?:0|[1-9][0-9]{0,12})(?:\.[0-9]{1,2})?", value):
                raise ValueError("invalid_budget")
            cents = Decimal(value) * 100
            if not 0 < cents <= 9_007_199_254_740_991:
                raise ValueError("invalid_budget")
        return value

    @field_validator("required_place_hints", "preferences")
    @classmethod
    def unique_nonblank(cls, values: list[str]) -> list[str]:
        if any(not v.strip() or v != v.strip() for v in values) or len(values) != len(set(values)):
            raise ValueError("invalid_hint_list")
        return values

    @model_validator(mode="after")
    def window(self) -> Self:
        if self.start_date and self.end_date:
            if not 0 <= (date.fromisoformat(self.end_date) - date.fromisoformat(self.start_date)).days < MAX_TRIP_DAYS:
                raise ValueError("invalid_trip_window")
        return self


class Source(Strict):
    field: FieldName
    quote: Annotated[str, AfterValidator(plain_text)] = Field(min_length=1, max_length=300)


class Selection(Strict):
    """Model may propose intent only; program renders all user-visible messages."""
    requirements: Intent
    sources: list[Source] = Field(max_length=9)
    issues: list[IssueCode] = Field(max_length=4)

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len({s.field for s in self.sources}) != len(self.sources) or len(set(self.issues)) != len(self.issues):
            raise ValueError("duplicate_selection")
        return self


class CreateConversation(Strict):
    operation_id: Identifier


class SubmitTurn(CreateConversation):
    expected_revision: int = Field(ge=0, le=1000)
    text: str = Field(min_length=1, max_length=2000)

    @field_validator("text")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("invalid_message")
        return plain_text(value)


class ConfirmConversation(CreateConversation):
    expected_revision: int = Field(ge=0, le=1000)


class DeleteConversation(Strict):
    expected_revision: int = Field(ge=0, le=1000)


class Message(Strict):
    id: Identifier
    role: Literal["user", "assistant"]
    text: Annotated[str, AfterValidator(plain_text)] = Field(min_length=1, max_length=3000)
    created_at: Timestamp


class TurnState(Strict):
    operation_id: Identifier
    state: Literal["running", "succeeded", "failed", "cancelled", "uncertain"]
    error_code: str | None = Field(default=None, max_length=80)


class Conversation(Strict):
    schema_version: Literal["chat-session-v1"] = "chat-session-v1"
    id: Identifier
    title: str = Field(min_length=1, max_length=80)
    revision: int = Field(ge=0, le=1000)
    requirements: Intent
    confirmed_revision: int | None = Field(default=None, ge=0, le=1000)
    sources: list[Source] = Field(max_length=9)
    issues: list[IssueCode] = Field(default_factory=list, max_length=4)
    messages: list[Message] = Field(max_length=40)
    last_turn: TurnState | None
    created_at: Timestamp
    updated_at: Timestamp

    @model_validator(mode="after")
    def consistent_view(self) -> Self:
        if (self.confirmed_revision is not None and self.confirmed_revision != self.revision
                or len({m.id for m in self.messages}) != len(self.messages)
                or len({s.field for s in self.sources}) != len(self.sources)
                or len(set(self.issues)) != len(self.issues)):
            raise ValueError("invalid_conversation")
        return self


class Summary(Strict):
    id: Identifier
    title: str = Field(min_length=1, max_length=80)
    revision: int = Field(ge=0, le=1000)
    updated_at: Timestamp


class ConversationList(Strict):
    schema_version: Literal["chat-list-v1"] = "chat-list-v1"
    items: list[Summary] = Field(max_length=20)
    next_offset: int | None = Field(default=None, ge=0, le=1000)
