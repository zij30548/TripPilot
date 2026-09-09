from datetime import date, time
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator


NonBlankString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Pace = Literal["relaxed", "balanced", "packed"]


class TripRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_date: date
    end_date: date
    budget: float = Field(gt=0, allow_inf_nan=False, strict=True)
    travelers: int = Field(gt=0, strict=True)
    accommodation_location: NonBlankString
    pace: Pace
    interests: list[NonBlankString]
    must_visit: list[NonBlankString]
    avoid_places: list[NonBlankString]
    daily_start_time: time
    daily_end_time: time

    @field_validator("daily_start_time", "daily_end_time")
    @classmethod
    def validate_local_time(cls, value: time) -> time:
        if value.tzinfo is not None:
            raise ValueError("请使用上海当地时间，不附带时区偏移。")
        return value

    @model_validator(mode="after")
    def validate_trip_window(self) -> Self:
        if not 0 <= (self.end_date - self.start_date).days <= 2:
            raise ValueError("行程必须为 1～3 天（包含开始和结束日期）。")
        if self.daily_start_time >= self.daily_end_time:
            raise ValueError("每天结束时间必须晚于出发时间。")
        return self


class Activity(BaseModel):
    name: str
    description: str
    start_time: time
    end_time: time


class DayPlan(BaseModel):
    day: int = Field(ge=1)
    title: str
    activities: list[Activity] = Field(min_length=1)


class TripPlan(BaseModel):
    destination: str
    estimated_cost: float = Field(ge=0, allow_inf_nan=False)
    currency: Literal["CNY"] = "CNY"
    is_mock: Literal[True] = True
    notice: str
    days: list[DayPlan] = Field(min_length=1)
