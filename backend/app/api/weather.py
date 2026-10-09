from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException

from app.api.places import get_http_client
from app.api.routes import SafeRouteValidation
from app.config import Settings, get_settings
from app.integrations.amap import AmapError, AmapTimeoutError
from app.integrations.amap_weather import AmapWeatherClient
from app.schemas.weather import WeatherForecastResponse, WeatherRequest


class SafeWeatherValidation(SafeRouteValidation):
    validation_message = "天气查询参数无效，请提供连续 1 至 3 天的起止日期。"


router = APIRouter(prefix="/weather", tags=["weather"], route_class=SafeWeatherValidation)


def get_weather_client(
    settings: Annotated[Settings, Depends(get_settings)],
    client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
) -> AmapWeatherClient:
    key = settings.amap_web_key.get_secret_value().strip()
    if not key:
        raise HTTPException(status_code=503, detail="天气服务未配置 AMAP_WEB_KEY。")
    return AmapWeatherClient(client, key)


@router.post("/forecast", response_model=WeatherForecastResponse)
async def forecast(
    request: WeatherRequest,
    amap: Annotated[AmapWeatherClient, Depends(get_weather_client)],
) -> WeatherForecastResponse:
    try:
        return await amap.forecast(request)
    except AmapTimeoutError as error:
        raise HTTPException(status_code=504, detail=str(error)) from None
    except AmapError as error:
        raise HTTPException(status_code=502, detail=str(error)) from None
