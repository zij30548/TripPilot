"""Opt-in, request-scoped AMap POI MCP adapter, not an autonomous Agent.

Real MCP initialize/list/call through the preserved StandardMCPClient + SDK.
No original files are patched. Its background reconnect/refresh is replaced
with one finite request lifecycle; its bounded result is accepted only when
complete. The model sees the existing local search contract, not remote prose.
"""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from datetime import timedelta
import json
import threading
from typing import Any, Awaitable, Callable

import httpx
from jsonschema import validators
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from core.business_usage import UsageError
from core.chat_cities import city_for
from core.schemas.destination import LocatedPlace
from echomind_core.diagnostics import quarantine_upstream_logging
from echomind_core.upstream.tooling.mcp_client import (
    MCPServerConfig, StandardMCPClient, tool_alias,
)
from tooling.providers.amap import AmapTimeoutError
from tooling.providers.amap_mcp_transport import (
    DEADLINE_SECONDS, MCPTransportError, create_http_client, endpoint_for, quiet_sdk_logs,
)
from tooling.providers.chat_places import ChatPlacesError, normalize_places
from tooling.providers.qwen import strict_json

VERSION = "amap-mcp-poi-v1"
TOOL = "maps_text_search"
SERVER = "amap"
MAX_CATALOG_BYTES = 64 * 1024
MAX_SCHEMA_BYTES = 8192
MAX_RESULT_BYTES = 96 * 1024
MAX_CONCURRENT_SEARCHES = 3
_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_SEARCHES)


def _json_bytes(value: Any, maximum: int) -> bytes:
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        if len(raw) > maximum:
            raise ValueError()
        return raw
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise ChatPlacesError("chat_place_mcp_result") from None


def _schema(value: Any) -> None:
    _json_bytes(value, MAX_SCHEMA_BYTES)
    def visit(item: Any, depth: int = 0) -> None:
        if depth > 12:
            raise ValueError()
        if isinstance(item, dict):
            if (len(item) > 64 or any(key in item for key in
                    ("$ref", "$dynamicRef", "$recursiveRef", "pattern", "patternProperties"))
                    or item.get("uniqueItems") is True):
                raise ValueError()
            for child in item.values():
                visit(child, depth + 1)
        elif isinstance(item, list):
            if len(item) > 64:
                raise ValueError()
            for child in item:
                visit(child, depth + 1)
    try:
        if not isinstance(value, dict):
            raise ValueError()
        visit(value)
        validators.validator_for(value).check_schema(value)
    except Exception:
        raise ChatPlacesError("chat_place_mcp_protocol") from None


def _result(result: Any) -> dict[str, Any]:
    """No resource fetching, free prose, duplicate keys or competing payloads."""
    if result.isError or len(result.content) > 1:
        raise ChatPlacesError("chat_place_mcp_result")
    structured = result.structuredContent
    text = None
    if result.content:
        block = result.content[0]
        if block.type != "text" or len(block.text.encode()) > MAX_RESULT_BYTES:
            raise ChatPlacesError("chat_place_mcp_result")
        try:
            text = strict_json(block.text.encode())
        except (ValueError, TypeError, RecursionError, UnicodeError):
            raise ChatPlacesError("chat_place_mcp_result") from None
    if structured is not None and text is not None and _json_bytes(structured, MAX_RESULT_BYTES) != _json_bytes(text, MAX_RESULT_BYTES):
        # Canonical JSON preserves types (True is not 1), while allowing only
        # object-key order differences. Never silently choose one payload.
        raise ChatPlacesError("chat_place_mcp_result")
    payload = structured if structured is not None else text
    _json_bytes(payload, MAX_RESULT_BYTES)
    if not isinstance(payload, dict) or not isinstance(payload.get("pois"), list):
        raise ChatPlacesError("chat_place_mcp_result")
    if "status" in payload and payload["status"] != "1":
        raise ChatPlacesError("chat_place_upstream_error")
    return payload


class _CheckedSession:
    def __init__(self, session: ClientSession):
        self.session = session

    async def call_tool(self, name: str, arguments: dict) -> Any:
        result = await self.session.call_tool(name, arguments)
        _result(result)
        return result  # Original client still performs its normal projection.


class _Registry:
    """Only the preserved MCP client's fixed discovered search registration."""
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def register(self, tool: Any) -> None:
        if tool.name != tool_alias(SERVER, TOOL) or self.tools or tool.cache_ttl != 0:
            raise ChatPlacesError("chat_place_mcp_protocol")
        self.tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        self.tools.pop(name, None)


class _OneSearch(StandardMCPClient):
    """Reuse discovery registration/native call; no automatic reconnect loop."""
    def __init__(self, key: str, transport: httpx.AsyncBaseTransport | None,
                 before_send: Callable[[], Awaitable[Any]] | None = None):
        quiet_sdk_logs()
        quarantine_upstream_logging()
        self._key, self._transport = key, transport
        self._before_send = before_send
        self.http: httpx.AsyncClient | None = None
        self.usage_error: UsageError | None = None
        config = MCPServerConfig(name=SERVER, transport="streamable_http", allowed_tools=[TOOL],
            agents=["general"], url=endpoint_for(key), read_only=False, timeout_s=max(1, DEADLINE_SECONDS),
            cache_ttl=0, max_result_chars=50_000)
        # read_only=False here only skips optional REMOTE hint requirements;
        # trusted local policy permits this one read-only tool, never arbitrary
        # remote capabilities. Explicit contradictory hints are rejected below.
        super().__init__(_Registry(), [config])

    def check_usage(self) -> None:
        # SDK transport runs in child tasks and may wrap their errors. Preserve
        # the existing public quota/counter failure contract, never a generic
        # "protocol failed" or a misleading empty result instead of denied quota.
        failure = getattr(self.http, "mcp_failure", None)
        if isinstance(failure, UsageError):
            raise failure
        if self.usage_error is not None:
            raise self.usage_error

    async def rpc(self, call: Awaitable[Any]) -> Any:
        # The SDK may wrap a failed child transport into a closed stream while
        # leaving an RPC waiter alive until its timeout. Race its request-local
        # failure latch so quota/network rejection is reported promptly, not
        # hidden until the 20-second deadline. Neither path sends again.
        operation = asyncio.ensure_future(call)
        failed = asyncio.create_task(self.http.mcp_failed.wait())
        try:
            done, _ = await asyncio.wait((operation, failed), return_when=asyncio.FIRST_COMPLETED)
            if failed in done:
                self.check_usage()
                raise ChatPlacesError("chat_place_upstream_error")
            return operation.result()
        finally:
            for task in (operation, failed):
                if not task.done():
                    task.cancel()
            await asyncio.gather(operation, failed, return_exceptions=True)

    @staticmethod
    async def _discover(session: ClientSession, timeout: float) -> list[Any]:
        page = await asyncio.wait_for(session.list_tools(), timeout)
        if page.nextCursor or not 1 <= len(page.tools) <= 32:
            raise ChatPlacesError("chat_place_mcp_protocol")
        _json_bytes(page.model_dump(mode="json"), MAX_CATALOG_BYTES)
        if len({tool.name for tool in page.tools}) != len(page.tools):
            raise ChatPlacesError("chat_place_mcp_protocol")
        selected = [tool for tool in page.tools if tool.name == TOOL]
        if len(selected) != 1:
            raise ChatPlacesError("chat_place_mcp_protocol")
        tool = selected[0]
        _schema(tool.inputSchema)
        if tool.outputSchema is not None:
            _schema(tool.outputSchema)
        properties = tool.inputSchema.get("properties", {})
        if (tool.inputSchema.get("type") != "object" or not isinstance(properties, dict)
                or any(not isinstance(properties.get(key), dict) or properties[key].get("type") != kind
                       for key, kind in (("keywords", "string"), ("city", "string"), ("citylimit", "boolean")))
                or set(tool.inputSchema.get("required", [])) - {"keywords", "city", "citylimit"}
                or tool.annotations is not None and (tool.annotations.destructiveHint is True
                                                     or tool.annotations.readOnlyHint is False)):
            raise ChatPlacesError("chat_place_mcp_protocol")
        return selected

    async def _serve(self, connection: Any) -> None:
        try:
            async with AsyncExitStack() as stack:
                http = await stack.enter_async_context(create_http_client(self._key, transport=self._transport,
                                                                         before_business=self._before_send))
                self.http = http
                read, write, _ = await stack.enter_async_context(streamable_http_client(
                    connection.config.url, http_client=http, terminate_on_close=False))
                session = await stack.enter_async_context(ClientSession(read, write,
                    read_timeout_seconds=timedelta(seconds=DEADLINE_SECONDS)))
                initialized = await self.rpc(session.initialize())
                catalog = await self.rpc(self._discover(session, DEADLINE_SECONDS))
                connection.session = _CheckedSession(session)
                connection.protocol_version = initialized.protocolVersion
                self._register_catalog(connection, catalog)
                connection.state = "connected"
                connection.ready.set()
                await connection.stop.wait()
        except asyncio.CancelledError:
            raise
        except UsageError as error:
            self.usage_error = error
            connection.state, connection.error = "disconnected", "usage_failed"
        except Exception:
            connection.state, connection.error = "disconnected", "protocol_failed"
        finally:
            connection.session = None
            self._remove_server(connection.config.name)
            connection.ready.set()


async def _close(client: _OneSearch) -> None:
    """Drain the owning task even when cancellation repeats during cleanup."""
    task = asyncio.create_task(client.close())
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    task.result()
    if cancelled:
        raise asyncio.CancelledError


class ChatPlacesMCPClient:
    def __init__(self, key: str, *, transport: httpx.AsyncBaseTransport | None = None,
                 before_send: Callable[[], Awaitable[Any]] | None = None):
        try:
            endpoint_for(key)
        except MCPTransportError:
            raise ChatPlacesError("places_not_configured") from None
        self._key, self._transport = key, transport
        self._before_send = before_send

    async def search(self, keyword: str, city_id: str) -> list[LocatedPlace]:
        try:
            city = city_for(city_id)
            if (not isinstance(keyword, str) or not 1 <= len(keyword.strip()) <= 80
                    or any(ord(c) < 32 or ord(c) == 127 for c in keyword)):
                raise ValueError()
        except ValueError:
            raise ChatPlacesError("chat_place_invalid_query") from None
        if not _SLOTS.acquire(blocking=False):
            raise ChatPlacesError("chat_places_busy")
        client = None
        try:
            client = _OneSearch(self._key, self._transport, self._before_send)
            async with asyncio.timeout(DEADLINE_SECONDS):
                await client.start()
                client.check_usage()
                connection = client.connections[SERVER]
                if connection.state != "connected":
                    raise ChatPlacesError("chat_place_mcp_protocol")
                params = {"keywords": keyword.strip(), "city": city.id, "citylimit": True}
                spec = client.manager.tools[tool_alias(SERVER, TOOL)]
                validator = validators.validator_for(spec.schema)(spec.schema)
                if not validator.is_valid(params):
                    raise ChatPlacesError("chat_place_mcp_protocol")
                result = await client.rpc(client._call_remote(SERVER, TOOL, params))
                client.check_usage()
                if result.get("truncated") is not False or result.get("content"):
                    raise ChatPlacesError("chat_place_mcp_result")
                payload = result.get("structured")
                if not isinstance(payload, dict):
                    raise ChatPlacesError("chat_place_mcp_result")
                await asyncio.sleep(0)
                return normalize_places(payload.get("pois"), city_id)
        except Exception as error:
            if client is not None:
                client.check_usage()
            if isinstance(error, (UsageError, ChatPlacesError)):
                raise
            if isinstance(error, TimeoutError):
                raise AmapTimeoutError("MCP 地点搜索超时；没有自动重试或改用 REST。") from None
            if isinstance(error, MCPTransportError):
                raise ChatPlacesError("chat_place_upstream_error") from None
            raise ChatPlacesError("chat_place_mcp_protocol") from None
        finally:
            try:
                if client is not None:
                    await _close(client)
            finally:
                # Only after actual connection/SDK task cleanup, including
                # repeated cancellation; never an unbounded waiting queue.
                _SLOTS.release()
