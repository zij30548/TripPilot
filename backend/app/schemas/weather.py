"""Independent city forecast contract, not the old Mock weather model."""

import re
from datetime import date, timedelta
from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


Precipitation = Literal["rain", "snow", "rain_snow", "none", "unknown"]

# Exact descriptions in AMap's weather-code table. These classify text only,
# not travel safety, probability, or whether a particular POI is outdoors.
RAIN = frozenset({
    "阵雨", "雷阵雨", "雷阵雨并伴有冰雹", "小雨", "中雨", "大雨", "暴雨", "大暴雨", "特大暴雨",
    "强阵雨", "强雷阵雨", "极端降雨", "毛毛雨/细雨", "雨", "小雨-中雨", "中雨-大雨",
    "大雨-暴雨", "暴雨-大暴雨", "大暴雨-特大暴雨", "冻雨",
})
SNOW = frozenset({"雪", "阵雪", "小雪", "中雪", "大雪", "暴雪", "小雪-中雪", "中雪-大雪", "大雪-暴雪"})
RAIN_SNOW = frozenset({"雨雪天气", "雨夹雪", "阵雨夹雪"})
NON_PRECIPITATION = frozenset({
    "晴", "少云", "晴间多云", "多云", "阴", "有风", "平静", "微风", "和风", "清风", "强风/劲风",
    "疾风", "大风", "烈风", "风暴", "狂爆风", "飓风", "热带风暴", "霾", "中度霾", "重度霾", "严重霾",
    "浮尘", "扬沙", "沙尘暴", "强沙尘暴", "龙卷风", "雾", "浓雾", "强浓雾", "轻雾", "大雾", "特强浓雾", "热", "冷",
})


def precipitation_for(weather: str | None) -> Precipitation:
    if weather in RAIN:
        return "rain"
    if weather in SNOW:
        return "snow"
    if weather in RAIN_SNOW:
        return "rain_snow"
    if weather in NON_PRECIPITATION:
        return "none"
    return "unknown"


def strict_date(value: object) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("日期必须是 YYYY-MM-DD。")
    return date.fromisoformat(value)


class WeatherRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_date: date
    end_date: date

    _iso_dates = field_validator("start_date", "end_date", mode="before")(strict_date)

    @model_validator(mode="after")
    def valid_range(self) -> Self:
        if not 0 <= (self.end_date - self.start_date).days <= 2:
            raise ValueError("天气查询仅支持含首尾的 1 至 3 天。")
        return self

    def dates(self) -> list[date]:
        return [self.start_date + timedelta(days=offset) for offset in range((self.end_date - self.start_date).days + 1)]


class WeatherPeriod(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    weather: str | None
    temperature_celsius: float | None = Field(ge=-100, le=100, allow_inf_nan=False)
    wind_direction: str | None
    wind_power: str | None
    precipitation: Precipitation
    precipitation_basis: str | None

    @field_validator("weather", "wind_direction", "wind_power", "precipitation_basis")
    @classmethod
    def valid_text(cls, value: str | None) -> str | None:
        if value is not None and (
            not 1 <= len(value) <= 100 or value != value.strip() or re.search(r"[\x00-\x1f\x7f-\x9f\ufeff]", value)
        ):
            raise ValueError("Invalid normalized weather text")
        return value

    @model_validator(mode="after")
    def explain_classification(self) -> Self:
        if self.precipitation != precipitation_for(self.weather) or self.precipitation_basis != self.weather:
            raise ValueError("Precipitation must describe the actual source text")
        return self


class WeatherDay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date
    status: Literal["available", "unavailable"]
    day: WeatherPeriod | None
    night: WeatherPeriod | None

    @model_validator(mode="after")
    def consistent_availability(self) -> Self:
        if self.status == "available" and (self.day is None or self.night is None):
            raise ValueError("Available date requires both period records")
        if self.status == "unavailable" and (self.day is not None or self.night is not None):
            raise ValueError("Unavailable date cannot contain substitute weather")
        return self


class WeatherForecastResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: WeatherRequest
    source: Literal["amap"] = "amap"
    city: Literal["上海市"] = "上海市"
    adcode: Literal["310000"] = "310000"
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"
    queried_at: AwareDatetime
    reported_at: AwareDatetime | None
    report_time_status: Literal["valid", "missing", "invalid"]
    freshness_at_query: Literal["fresh", "stale", "unknown"]
    coverage: Literal["complete", "partial", "none"]
    guidance_rule: Literal["amap_text_precipitation_v1"] = "amap_text_precipitation_v1"
    days: list[WeatherDay] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def consistent_snapshot(self) -> Self:
        if [day.date for day in self.days] != self.request.dates():
            raise ValueError("Every requested date must appear exactly once, in order")
        available = sum(day.status == "available" for day in self.days)
        coverage = "complete" if available == len(self.days) else "partial" if available else "none"
        if self.coverage != coverage:
            raise ValueError("Coverage must describe the actual matched dates")
        if self.queried_at.utcoffset() != timedelta(0):
            raise ValueError("Query time must be UTC")
        if self.report_time_status == "valid":
            if (self.reported_at is None or self.reported_at.utcoffset() != timedelta(hours=8)
                    or self.reported_at.year < 2000 or self.reported_at > self.queried_at + timedelta(minutes=5)):
                raise ValueError("Invalid source publication time")
            freshness = "stale" if self.queried_at - self.reported_at > timedelta(hours=24) else "fresh"
            if self.freshness_at_query != freshness:
                raise ValueError("Freshness must use the original publication time")
        elif self.reported_at is not None or self.freshness_at_query != "unknown":
            raise ValueError("Unknown publication cannot become fresh from a query time")
        return self
