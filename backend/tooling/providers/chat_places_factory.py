"""Server-selected POI transport; no request URL, fallback or eager MCP import.

Both transports feed the existing owner/version-bound preview and explicit
confirmation path. Selecting MCP does not expand planning or POI trust rules.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Protocol

import httpx

from core.config import Settings
from core.schemas.destination import LocatedPlace
from tooling.providers.chat_places import ChatPlacesClient, ChatPlacesError


class ChatPlacesSearch(Protocol):
    async def search(self, keyword: str, city_id: str) -> list[LocatedPlace]: ...


def chat_places_configured(settings: Settings) -> bool:
    key = settings.amap_mcp_key if settings.chat_places_transport == "amap_mcp" else settings.amap_web_key
    return bool(key.get_secret_value().strip())


def create_chat_places_client(settings: Settings, client: httpx.AsyncClient, *,
                              before_send: Callable[[], Awaitable[Any]] | None = None) -> ChatPlacesSearch:
    """Select once from trusted settings and fail closed; never try both paths."""
    if not chat_places_configured(settings):
        raise ChatPlacesError("places_not_configured")
    if settings.chat_places_transport == "rest":
        return ChatPlacesClient(client, settings.amap_web_key.get_secret_value().strip())
    try:
        # Optional dependency failure is scoped to explicitly selected MCP. It
        # cannot make the default app fail to import or silently select REST.
        from tooling.providers.amap_mcp import ChatPlacesMCPClient
        if before_send is not None:
            return ChatPlacesMCPClient(settings.amap_mcp_key.get_secret_value().strip(), before_send=before_send)
        return ChatPlacesMCPClient(settings.amap_mcp_key.get_secret_value().strip())
    except ImportError:
        raise ChatPlacesError("chat_place_mcp_unavailable") from None
