from collections.abc import Callable, Coroutine
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute

from app.api.places import get_http_client
from app.config import Settings, get_settings
from app.integrations.amap import AmapError, AmapTimeoutError
from app.integrations.amap_walking import AmapWalkingClient
from app.integrations.amap_transit import AmapTransitClient
from app.schemas.route import WalkingRouteRequest, WalkingRouteResponse
from app.schemas.transit import TransitRouteRequest, TransitRouteResponse


class SafeRouteValidation(APIRoute):
    validation_message = "步行路线请求参数无效，请检查地点和坐标。"

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()

        async def safe_handler(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError:
                # Avoid echoing arbitrary input (including NaN/Infinity, which
                # cannot be serialized by JSONResponse) in validation errors.
                raise HTTPException(status_code=422, detail=self.validation_message) from None

        return safe_handler


router = APIRouter(prefix="/routes", tags=["routes"], route_class=SafeRouteValidation)


def get_walking_client(
    settings: Annotated[Settings, Depends(get_settings)],
    client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
) -> AmapWalkingClient:
    key = settings.amap_web_key.get_secret_value().strip()
    if not key:
        raise HTTPException(status_code=503, detail="步行路线服务未配置 AMAP_WEB_KEY。")
    return AmapWalkingClient(client, key)


@router.post("/walking", response_model=WalkingRouteResponse)
async def walking_route(
    request: WalkingRouteRequest,
    amap: Annotated[AmapWalkingClient, Depends(get_walking_client)],
) -> WalkingRouteResponse:
    try:
        return await amap.walking(request)
    except AmapTimeoutError as error:
        raise HTTPException(status_code=504, detail=str(error)) from None
    except AmapError as error:
        raise HTTPException(status_code=502, detail=str(error)) from None


class SafeTransitValidation(SafeRouteValidation):
    validation_message = "公交／地铁路线请求参数无效，请检查地点和坐标。"


transit_router = APIRouter(route_class=SafeTransitValidation)


def get_transit_client(
    settings: Annotated[Settings, Depends(get_settings)],
    client: Annotated[httpx.AsyncClient, Depends(get_http_client)],
) -> AmapTransitClient:
    key = settings.amap_web_key.get_secret_value().strip()
    if not key:
        raise HTTPException(status_code=503, detail="公交／地铁路线服务未配置 AMAP_WEB_KEY。")
    return AmapTransitClient(client, key)


@transit_router.post("/transit", response_model=TransitRouteResponse)
async def transit_route(
    request: TransitRouteRequest,
    amap: Annotated[AmapTransitClient, Depends(get_transit_client)],
) -> TransitRouteResponse:
    try:
        return await amap.transit(request)
    except AmapTimeoutError as error:
        raise HTTPException(status_code=504, detail=str(error)) from None
    except AmapError as error:
        raise HTTPException(status_code=502, detail=str(error)) from None


router.include_router(transit_router)
