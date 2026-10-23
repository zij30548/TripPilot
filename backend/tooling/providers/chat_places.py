"""Bounded chat-only AMap search using an explicit offline city identity.

This does not expand the old six-city planning/snapshot contract. One logical
search retains at most the original one connection retry; MeteredAsyncClient
accounts for each physical send at its existing boundary.
"""
from __future__ import annotations

import asyncio
import logging

import httpx
from pydantic import ValidationError

from core.chat_cities import city_for, region_belongs_to
from core.destinations import PlaceRegion
from tooling.providers.amap import AmapError, AmapTimeoutError, REQUEST_TIMEOUT, SEARCH_URL
from tooling.providers.qwen import strict_json
from core.schemas.destination import LocatedPlace

MAX_RESPONSE_BYTES = 256 * 1024
MAX_PLACES = 20
logger = logging.getLogger(__name__)


class ChatPlacesError(AmapError):
    def __init__(self, code: str = "chat_place_data_error") -> None:
        self.code = code
        message = {
            "chat_place_region_error": "地点城市归属缺失、矛盾或跨城，本次搜索未采用。",
            "chat_place_response_limit": "地点结果超过安全处理范围，本次搜索未采用。",
            "chat_place_upstream_error": "地点搜索服务暂不可用，本次未取得可确认结果。",
            "chat_place_invalid_query": "请选择明确的目录城市并输入1～80个字的地点关键词。",
            "places_not_configured": "地点搜索尚未配置，本次没有发送查询。",
            "chat_place_mcp_unavailable": "MCP 地点搜索依赖尚未准备；没有自动改用其他查询方式。",
            "chat_place_mcp_protocol": "MCP 地点搜索协议或工具版本不兼容，本次未取得可确认结果。",
            "chat_place_mcp_result": "MCP 地点结果缺少必要字段或格式无效，本次未采用；没有自动补查。",
            "chat_places_busy": "地点搜索处理容量已满；本次没有排队或发送，请稍后主动操作。",
        }.get(code, "地点数据格式或必要字段无效，本次搜索未采用。")
        super().__init__(message)


def _bounded_text(value: object, maximum: int, *, nullable: bool = False) -> str | None:
    if nullable and (value is None or value == [] or isinstance(value, str) and not value.strip()):
        return None
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or any(ord(character) < 32 or ord(character) == 127 for character in value)):
        raise ChatPlacesError()
    return value.strip()


def _place(poi: object, city_id: str) -> LocatedPlace:
    if not isinstance(poi, dict):
        raise ChatPlacesError()
    try:
        region = PlaceRegion.model_validate({key: poi.get(key) for key in ("citycode", "cityname", "adcode")})
        if not region_belongs_to(region, city_id):
            raise ChatPlacesError("chat_place_region_error")
    except ValidationError:
        raise ChatPlacesError("chat_place_region_error") from None
    location = poi.get("location")
    if not isinstance(location, str) or len(location) > 80 or len(location.split(",")) != 2:
        raise ChatPlacesError()
    try:
        longitude, latitude = (float(value.strip()) for value in location.split(","))
        return LocatedPlace(id=_bounded_text(poi.get("id"), 120), name=_bounded_text(poi.get("name"), 200),
                            address=_bounded_text(poi.get("address"), 1000, nullable=True),
                            longitude=longitude, latitude=latitude,
                            category=_bounded_text(poi.get("type"), 200, nullable=True),
                            source="amap", region=region)
    except (ValueError, TypeError):
        raise ChatPlacesError() from None


def normalize_places(rows: object, city_id: str) -> list[LocatedPlace]:
    """Shared REST/MCP projection; returned region is evidence, query is not.

    Keep all-or-nothing rejection and identity checks at the same boundary.
    Only an actual empty POI list is an empty search, not missing data.
    """
    if not isinstance(rows, list):
        raise ChatPlacesError()
    if len(rows) > MAX_PLACES:
        raise ChatPlacesError("chat_place_response_limit")
    places: dict[str, LocatedPlace] = {}
    for row in rows:
        place = _place(row, city_id)
        if place.id in places and places[place.id] != place:
            raise ChatPlacesError()
        places.setdefault(place.id, place)
    return list(places.values())


class ChatPlacesClient:
    def __init__(self, client: httpx.AsyncClient, key: str):
        self._client, self._key = client, key

    async def search(self, keyword: str, city_id: str) -> list[LocatedPlace]:
        try:
            city = city_for(city_id)
        except ValueError:
            raise ChatPlacesError("chat_place_invalid_query") from None
        if (not isinstance(keyword, str) or not 1 <= len(keyword.strip()) <= 80
                or any(ord(character) < 32 or ord(character) == 127 for character in keyword)):
            raise ChatPlacesError("chat_place_invalid_query")
        for attempt in (1, 2):
            try:
                async with self._client.stream("GET", SEARCH_URL, params={"key": self._key, "keywords": keyword.strip(),
                                              "region": city.id, "city_limit": "true", "page_size": MAX_PLACES, "page_num": 1},
                                              timeout=REQUEST_TIMEOUT, follow_redirects=False) as response:
                    response.raise_for_status()
                    declared = response.headers.get("content-length")
                    if declared is not None and (not declared.isdecimal() or int(declared) > MAX_RESPONSE_BYTES):
                        raise ChatPlacesError("chat_place_response_limit")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise ChatPlacesError("chat_place_response_limit")
                        body.extend(chunk)
                break
            except httpx.HTTPError as error:
                logger.warning("Chat POI transport failed stage=%s attempt=%d", type(error).__name__, attempt)
                if attempt == 1 and isinstance(error, (httpx.ConnectTimeout, httpx.ConnectError)):
                    continue
                if isinstance(error, httpx.TimeoutException):
                    raise AmapTimeoutError("地点搜索服务请求超时；没有自动重新搜索。") from None
                raise ChatPlacesError("chat_place_upstream_error") from None
        await asyncio.sleep(0)  # A pending cancellation cannot publish candidates.
        try:
            data = strict_json(body)
        except (ValueError, TypeError, RecursionError):
            raise ChatPlacesError() from None
        if not isinstance(data, dict) or data.get("status") != "1":
            raise ChatPlacesError("chat_place_upstream_error")
        return normalize_places(data.get("pois"), city_id)
