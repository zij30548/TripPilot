"""Bound calendar only: model never receives itinerary facts or user costs."""
from datetime import date
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.schemas.explanation import DateText, ExplanationUsage, StrictModel

VERSION = "itinerary-query-v1"
Reason = Literal["date_ambiguous", "date_out_of_range", "multiple_requests", "unsupported"]


class ItineraryQueryRequest(StrictModel):
    contract_version: Literal["itinerary-query-v1"]
    request_id: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")]
    draft_version: Annotated[int, Field(ge=0, le=9_007_199_254_740_991)]
    cost_version: Annotated[int, Field(ge=0, le=9_007_199_254_740_991)]
    dates: Annotated[list[DateText], Field(min_length=1, max_length=3)]
    question: Annotated[str, Field(min_length=1, max_length=400)]

    @model_validator(mode="after")
    def calendar(self) -> "ItineraryQueryRequest":
        dates = [date.fromisoformat(value) for value in self.dates]
        if any((right-left).days != 1 for left, right in zip(dates, dates[1:])):
            raise ValueError("Nonconsecutive calendar")
        if not self.question.strip() or any(ord(c) < 32 for c in self.question):
            raise ValueError("Invalid question")
        return self


class DayArguments(StrictModel):
    date: DateText


class EmptyArguments(StrictModel):
    pass


class ClarificationArguments(StrictModel):
    reason: Reason


class DayCall(StrictModel):
    name: Literal["get_day_schedule"]
    arguments: DayArguments


class BudgetCall(StrictModel):
    name: Literal["get_budget_summary"]
    arguments: EmptyArguments


class ClarificationCall(StrictModel):
    name: Literal["request_clarification"]
    arguments: ClarificationArguments


ToolCall = Annotated[DayCall | BudgetCall | ClarificationCall, Field(discriminator="name")]


class ItineraryQueryResponse(StrictModel):
    contract_version: Literal["itinerary-query-v1"] = VERSION
    request_id: str
    draft_version: int
    cost_version: int
    dates: list[DateText]
    tool_call_id: str
    tool_call: ToolCall
    model: str
    usage: ExplanationUsage
