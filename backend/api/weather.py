from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException

from api.places import get_http_client
from api.routes import SafeRouteValidation
from core.config import Settings, get_settings
from tooling.providers.amap import AmapError, AmapTimeoutError
from tooling.providers.amap_weather import AmapWeatherClient
from core.schemas.weather import CityWeatherRequest, CityWeatherForecastResponse, WeatherForecastResponse, WeatherRequest


class SafeWeatherValidation(SafeRouteValidation):
    validation_message = "天气查询参数无效：当前城市请求支持连续 1～7 天，旧版无城市请求仅支持 1～3 天。"


router = APIRouter(prefix="/weather", tags=["weather"], route_class=SafeWeatherValidation)


def get_weather_client(
    settings: Annotated[Settings, Depends(get_settings)],
    client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
) -> AmapWeatherClient:
    key = settings.amap_web_key.get_secret_value().strip()
    if not key:
        raise HTTPException(status_code=503, detail="天气服务未配置 AMAP_WEB_KEY。")
    return AmapWeatherClient(client, key)


@router.post("/forecast", response_model=CityWeatherForecastResponse | WeatherForecastResponse)
async def forecast(
    request: CityWeatherRequest | WeatherRequest,
    amap: Annotated[AmapWeatherClient, Depends(get_weather_client)],
) -> WeatherForecastResponse:
    try:
        return await amap.forecast(request)
    except AmapTimeoutError as error:
        raise HTTPException(status_code=504, detail=str(error)) from None
    except AmapError as error:
        raise HTTPException(status_code=502, detail=str(error)) from None
