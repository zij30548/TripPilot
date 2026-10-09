"""One bounded Shanghai forecast batch; strict parsing and safe failures."""

import asyncio
import logging
import math
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from app.integrations.amap import AmapError, AmapTimeoutError
from app.schemas.weather import (
    WeatherDay, WeatherForecastResponse, WeatherPeriod, WeatherRequest,
    precipitation_for, strict_date,
)


WEATHER_URL = "https://restapi.amap.com/v3/weather/weatherInfo"
BATCH_TIMEOUT_SECONDS = 10.0
SHANGHAI = ZoneInfo("Asia/Shanghai")
INVALID_DATA = "天气服务返回的数据格式异常，请稍后重试。"
TIMEOUT_MESSAGE = "天气查询超时，请稍后重试。"
logger = logging.getLogger(__name__)


def _missing(value: object) -> bool:
    # AMap documents strings but also uses empty arrays for absent scalar data.
    return value is None or value == [] or isinstance(value, str) and not value.strip()


def _text(value: object) -> str | None:
    if _missing(value):
        return None
    if not isinstance(value, str):
        raise ValueError("Non-string weather text")
    result = value.strip()
    if len(result) > 100 or re.search(r"[\x00-\x1f\x7f-\x9f\ufeff]", result):
        raise ValueError("Invalid weather text")
    return result


def _temperature(value: object) -> float | None:
    if _missing(value):
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError("Invalid temperature")
    if isinstance(value, str) and not re.fullmatch(r"[+-]?[0-9]+(?:\.[0-9]+)?", value.strip()):
        raise ValueError("Invalid temperature string")
    number = float(value)
    if not math.isfinite(number) or not -100 <= number <= 100:
        raise ValueError("Invalid temperature bounds")
    return number


def _period(cast: dict[str, object], prefix: str) -> WeatherPeriod:
    weather = _text(cast.get(f"{prefix}weather"))
    return WeatherPeriod(
        weather=weather,
        temperature_celsius=_temperature(cast.get(f"{prefix}temp")),
        wind_direction=_text(cast.get(f"{prefix}wind")),
        # Preserve source wind-power text (including ranges); do not invent speed.
        wind_power=_text(cast.get(f"{prefix}power")),
        precipitation=precipitation_for(weather),
        precipitation_basis=weather,
    )


def _report_time(value: object, queried_at: datetime) -> tuple[datetime | None, str, str]:
    if _missing(value):
        return None, "missing", "unknown"
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}", value):
        return None, "invalid", "unknown"
    try:
        reported_at = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=SHANGHAI)
    except ValueError:
        return None, "invalid", "unknown"
    if reported_at.year < 2000 or reported_at > queried_at + timedelta(minutes=5):
        return None, "invalid", "unknown"
    freshness = "stale" if queried_at - reported_at > timedelta(hours=24) else "fresh"
    return reported_at, "valid", freshness


def convert_forecast(data: object, request: WeatherRequest, queried_at: datetime) -> WeatherForecastResponse:
    if not isinstance(data, dict):
        raise AmapError(INVALID_DATA)
    if data.get("status") != "1":
        raise AmapError("高德天气查询失败，请检查服务配置或稍后重试。")
    forecasts = data.get("forecasts")
    if not isinstance(forecasts, list) or len(forecasts) > 1:
        raise AmapError(INVALID_DATA)
    matched: dict[date, tuple[WeatherPeriod, WeatherPeriod]] = {}
    reported_at, report_status, freshness = None, "missing", "unknown"
    try:
        if forecasts:
            forecast = forecasts[0]
            if (not isinstance(forecast, dict) or forecast.get("adcode") != "310000"
                    or forecast.get("city") not in ("上海", "上海市")):
                raise ValueError("Unexpected forecast city")
            casts = forecast.get("casts")
            if not isinstance(casts, list):
                raise ValueError("Invalid forecast dates")
            reported_at, report_status, freshness = _report_time(forecast.get("reporttime"), queried_at)
            for cast in casts:
                if not isinstance(cast, dict):
                    raise ValueError("Invalid forecast entry")
                forecast_date = strict_date(cast.get("date"))
                if forecast_date in matched:
                    raise ValueError("Duplicate forecast date")
                # Validate every returned entry, not only requested dates. Invalid
                # records must never masquerade as normal lack of coverage.
                matched[forecast_date] = (_period(cast, "day"), _period(cast, "night"))
        days = [
            WeatherDay(date=day, status="available", day=matched[day][0], night=matched[day][1])
            if day in matched else WeatherDay(date=day, status="unavailable", day=None, night=None)
            for day in request.dates()
        ]
        available = sum(day.status == "available" for day in days)
        return WeatherForecastResponse(
            request=request, queried_at=queried_at, reported_at=reported_at,
            report_time_status=report_status, freshness_at_query=freshness,
            coverage="complete" if available == len(days) else "partial" if available else "none",
            days=days,
        )
    except (ValueError, TypeError, OverflowError):
        raise AmapError(INVALID_DATA) from None


class AmapWeatherClient:
    def __init__(self, client: httpx.AsyncClient, key: str) -> None:
        self._client = client
        self._key = key

    async def forecast(self, request: WeatherRequest) -> WeatherForecastResponse:
        # The absolute deadline is shared by both attempts, never reset on retry.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + BATCH_TIMEOUT_SECONDS
        try:
            async with asyncio.timeout_at(deadline):
                for attempt in (1, 2):
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        raise TimeoutError
                    try:
                        response = await self._client.get(
                            WEATHER_URL,
                            params={"key": self._key, "city": "310000", "extensions": "all", "output": "JSON"},
                            timeout=httpx.Timeout(remaining, connect=min(3.0, remaining)),
                            follow_redirects=False,
                        )
                        response.raise_for_status()
                        break
                    except httpx.HTTPError as error:
                        logger.warning("AMap weather request failed (%s, attempt %d/2)", type(error).__name__, attempt)
                        if attempt == 1 and isinstance(error, (httpx.ConnectError, httpx.ConnectTimeout)):
                            continue
                        if isinstance(error, httpx.TimeoutException):
                            raise AmapTimeoutError(TIMEOUT_MESSAGE) from None
                        raise AmapError("暂时无法连接天气服务，请稍后重试。") from None
                try:
                    data = response.json()
                except ValueError:
                    raise AmapError(INVALID_DATA) from None
                result = convert_forecast(data, request, datetime.now(timezone.utc))
                # Synchronous decoding/validation also consumes this budget.
                if loop.time() >= deadline:
                    raise TimeoutError
                return result
        except TimeoutError:
            raise AmapTimeoutError(TIMEOUT_MESSAGE) from None
