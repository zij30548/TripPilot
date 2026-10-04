import logging

import httpx

from app.schemas.place import Place
from app.logging_filters import install_http_log_redaction


SEARCH_URL = "https://restapi.amap.com/v5/place/text"
REQUEST_TIMEOUT = httpx.Timeout(10.0, connect=3.0)
logger = logging.getLogger(__name__)


install_http_log_redaction()


class AmapError(Exception):
    """Safe, user-facing upstream error; never contains a URL or raw payload."""


class AmapTimeoutError(AmapError):
    pass


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _convert_poi(poi: object) -> Place | None:
    if not isinstance(poi, dict):
        return None
    location = poi.get("location")
    if not isinstance(location, str):
        return None
    coordinates = location.split(",")
    if len(coordinates) != 2:
        return None
    try:
        # AMap returns longitude first, latitude second. Never fabricate coordinates.
        longitude, latitude = (float(value.strip()) for value in coordinates)
        return Place(
            id=poi.get("id"),
            name=poi.get("name"),
            address=_optional_text(poi.get("address")),
            longitude=longitude,
            latitude=latitude,
            category=_optional_text(poi.get("type")),
        )
    except ValueError:
        # Includes Pydantic validation failures for missing identity or bad bounds.
        return None


class AmapClient:
    def __init__(self, client: httpx.AsyncClient, key: str) -> None:
        self._client = client
        self._key = key

    async def search(self, keyword: str, city: str) -> list[Place]:
        for attempt in (1, 2):
            try:
                response = await self._client.get(
                    SEARCH_URL,
                    params={
                        "key": self._key,
                        "keywords": keyword,
                        "region": city,
                        "city_limit": "true",
                        "page_size": 20,
                        "page_num": 1,
                    },
                    timeout=REQUEST_TIMEOUT,
                    follow_redirects=False,
                )
                response.raise_for_status()
                break
            except httpx.HTTPError as error:
                # Log only the failure stage, never exception text or request data.
                logger.warning("AMap POI request failed (%s, attempt %d/2)", type(error).__name__, attempt)
                if attempt == 1 and isinstance(error, (httpx.ConnectTimeout, httpx.ConnectError)):
                    # This fixed, read-only GET can recover from one transient connection failure.
                    continue
                if isinstance(error, httpx.TimeoutException):
                    raise AmapTimeoutError("地点搜索服务请求超时，请稍后重试。") from None
                raise AmapError("暂时无法连接地点搜索服务，请稍后重试。") from None

        try:
            data = response.json()
        except ValueError:
            raise AmapError("地点搜索服务返回的数据格式异常。") from None
        if not isinstance(data, dict):
            raise AmapError("地点搜索服务返回的数据格式异常。")
        if data.get("status") != "1":
            raise AmapError("高德地点搜索失败，请检查服务配置或稍后重试。")
        pois = data.get("pois")
        if not isinstance(pois, list):
            raise AmapError("地点搜索服务返回的数据格式异常。")

        places = [place for poi in pois if (place := _convert_poi(poi)) is not None]
        skipped = len(pois) - len(places)
        if skipped:
            logger.warning("Skipped %d invalid AMap POI records", skipped)
        return places
