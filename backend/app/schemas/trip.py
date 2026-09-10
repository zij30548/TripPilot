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
    id: str
    name: str
    category: Literal["sightseeing", "food", "museum", "shopping"]
    estimated_cost: float = Field(ge=0, allow_inf_nan=False)
    description: str
    start_time: time
    end_time: time


class TransportSegment(BaseModel):
    from_activity_id: str
    to_activity_id: str
    mode: Literal["walking", "metro", "taxi"]
    duration_minutes: int = Field(gt=0)
    estimated_cost: float = Field(ge=0, allow_inf_nan=False)
    description: str


class WeatherSummary(BaseModel):
    date: date
    condition: str
    min_temperature: float = Field(allow_inf_nan=False)
    max_temperature: float = Field(allow_inf_nan=False)
    rain_risk: int = Field(ge=0, le=100)

    @model_validator(mode="after")
    def validate_temperatures(self) -> Self:
        if self.min_temperature > self.max_temperature:
            raise ValueError("最低温度不能高于最高温度。")
        return self


class BudgetBreakdown(BaseModel):
    transport: float = Field(ge=0, allow_inf_nan=False)
    food: float = Field(ge=0, allow_inf_nan=False)
    tickets: float = Field(ge=0, allow_inf_nan=False)
    other: float = Field(ge=0, allow_inf_nan=False)


class DayPlan(BaseModel):
    day: int = Field(ge=1)
    date: date
    title: str
    activities: list[Activity] = Field(min_length=1)
    transports: list[TransportSegment]
    weather: WeatherSummary


class TripPlan(BaseModel):
    destination: str
    request: TripRequest
    budget_breakdown: BudgetBreakdown
    estimated_cost: float = Field(ge=0, allow_inf_nan=False)
    currency: Literal["CNY"] = "CNY"
    is_mock: Literal[True] = True
    notice: str
    days: list[DayPlan] = Field(min_length=1)
