from collections.abc import AsyncIterator
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.config import Settings, get_settings
from app.integrations.amap_proxy import (
    AmapProxyClient, DISTRICT_PATH, PROXY_TIMEOUT, ProxyError,
)


router = APIRouter(prefix="/_AMapService", tags=["map-proxy"])


async def get_proxy_http_client() -> AsyncIterator[httpx.AsyncClient]:
    # No browser headers/cookies are forwarded. TLS validation stays enabled.
    async with httpx.AsyncClient(timeout=PROXY_TIMEOUT, follow_redirects=False) as client:
        yield client


def get_proxy_client(
    settings: Annotated[Settings, Depends(get_settings)],
    client: Annotated[httpx.AsyncClient, Depends(get_proxy_http_client)],
) -> AmapProxyClient:
    key = settings.amap_js_key.get_secret_value().strip()
    security_code = settings.amap_js_security_code.get_secret_value().strip()
    if not key or not security_code:
        raise HTTPException(status_code=503, detail="地图服务未配置 AMAP_JS_KEY 或 AMAP_JS_SECURITY_CODE。")
    return AmapProxyClient(client, key, security_code, settings.amap_web_key.get_secret_value().strip())


async def _proxy_response(request: Request, path: str, proxy: AmapProxyClient) -> Response:
    if len(request.scope.get("query_string", b"")) > 4096:
        raise HTTPException(status_code=400, detail="地图查询参数过长。")
    # Avoid accepting percent-encoded slashes / alternate path spellings.
    expected_path = f"/_AMapService/{path}".encode()
    if request.scope.get("raw_path", expected_path) != expected_path:
        raise HTTPException(status_code=404, detail="不支持的地图服务路径。")
    try:
        payload = await proxy.request(path, request.query_params.multi_items())
    except ProxyError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from None
    return Response(payload.body, media_type=payload.media_type, headers={
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
    })


@router.get(f"/{DISTRICT_PATH}")
async def district_proxy(request: Request, proxy: Annotated[AmapProxyClient, Depends(get_proxy_client)]) -> Response:
    return await _proxy_response(request, DISTRICT_PATH, proxy)
