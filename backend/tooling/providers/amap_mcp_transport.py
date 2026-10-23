"""Fixed, single-search HTTP boundary for the opt-in AMap MCP adapter.

This is a transport for the real MCP SDK, not an alternative RPC protocol.  The
SDK still initializes, discovers and calls the tool.  This boundary only admits
that finite sequence, limits bytes, disables hidden reconnections and meters
the actual ``tools/call`` before dispatch.  Control requests do not consume POI
query quota; they are bounded to three per search and cannot invoke business
tools.  No REST fallback, environment proxy, retry or network cleanup request.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from core.business_usage import UsageError
from core.logging_filters import install_http_log_redaction
from core.usage_runtime import _context, _start
from tooling.providers.qwen import strict_json

ENDPOINT = "https://mcp.amap.com/mcp"
MAX_INPUT_BYTES = 8192
MAX_RESPONSE_BYTES = 256 * 1024
MAX_POSTS = 4
DEADLINE_SECONDS = 20
_PATH = re.compile(
    r"/chat/conversations/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}/(?:places|assistant-turns)"
)
_STEPS = ("initialize", "notifications/initialized", "tools/list", "tools/call")
_SDK_LOGGERS = (
    "mcp.client.streamable_http", "mcp.client.session", "mcp.shared.session",
    # SDK 1.26.0 uses this non-namespaced logger in client/session.py.
    "client",
)


class MCPTransportError(Exception):
    """Safe error only: never contains a URL, key, body or raw exception."""

    def __init__(self, code: str = "mcp_transport") -> None:
        self.code = code
        super().__init__(code)


def endpoint_for(key: str) -> str:
    if type(key) is not str or re.fullmatch(r"[0-9a-fA-F]{32}", key) is None:
        raise MCPTransportError("mcp_configuration")
    return f"{ENDPOINT}?key={key}"


def quiet_sdk_logs() -> None:
    """Quarantine only exact SDK loggers that can emit private RPC payloads.

    This permanent opt-in boundary avoids concurrent restore races.  It does
    not modify the root logger or other application loggers. Shared SDK payload
    logs on these exact names are deliberately suppressed for every use.
    """
    install_http_log_redaction()
    for name in _SDK_LOGGERS:
        logger = logging.getLogger(name)
        logger.handlers = [logging.NullHandler()]
        logger.propagate = False


def _current_identity() -> tuple[str, str, int]:
    context = _context()  # Missing trusted middleware context fails closed.
    if (re.fullmatch(r"[0-9a-f]{32}", context.owner_id) is None
            or _PATH.fullmatch(context.request_path) is None):
        raise MCPTransportError("mcp_context")
    return context.owner_id, context.request_path, id(context.store)


def _safe_text(value: object, maximum: int) -> bool:
    return (type(value) is str and 1 <= len(value) <= maximum and bool(value.strip())
            and not any(ord(character) < 32 or ord(character) == 127 for character in value))


def _request_id(value: object) -> tuple[type, object]:
    if (type(value) is int and 0 <= value <= 2_147_483_647
            or type(value) is str and re.fullmatch(r"[A-Za-z0-9._-]{1,80}", value)):
        return type(value), value
    raise MCPTransportError("mcp_request")


def _arguments(value: object) -> bool:
    return (type(value) is dict and set(value) == {"keywords", "city", "citylimit"}
            and _safe_text(value["keywords"], 80)
            and type(value["city"]) is str and re.fullmatch(r"[0-9]{6}", value["city"]) is not None
            and value["citylimit"] is True)


def _response_message(raw: bytes | str, identifier: tuple[type, object]) -> None:
    try:
        message = strict_json(raw)
        if (type(message) is not dict or message.get("jsonrpc") != "2.0"
                or set(message) not in ({"jsonrpc", "id", "result"}, {"jsonrpc", "id", "error"})
                or _request_id(message.get("id")) != identifier):
            raise ValueError
    except (ValueError, TypeError, UnicodeError, RecursionError, MCPTransportError):
        raise MCPTransportError("mcp_response") from None


def _validate_response(body: bytes, content_type: str, identifier: tuple[type, object]) -> None:
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type == "application/json":
        _response_message(body, identifier)
        return
    if media_type != "text/event-stream":
        raise MCPTransportError("mcp_response")
    # Admit a finite, complete POST SSE result.  Never let an incomplete stream
    # or an event ID/retry directive activate the SDK's automatic GET recovery.
    try:
        text = body.decode("utf-8")
    except UnicodeError:
        raise MCPTransportError("mcp_response") from None
    if "\r" in text.replace("\r\n", ""):
        raise MCPTransportError("mcp_response")
    text = text.replace("\r\n", "\n")
    if not text.endswith("\n\n"):
        raise MCPTransportError("mcp_response")
    data: list[str] = []
    event: str | None = None
    completed = 0
    for line in text.split("\n"):
        if not line:
            if data:
                if event not in (None, "message") or completed:
                    raise MCPTransportError("mcp_response")
                _response_message("\n".join(data), identifier)
                completed += 1
                data, event = [], None
            elif event is not None:
                raise MCPTransportError("mcp_response")
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if not separator or field not in {"event", "data"}:
            raise MCPTransportError("mcp_response")
        if field == "event":
            if event is not None:
                raise MCPTransportError("mcp_response")
            event = value
        else:
            data.append(value)
    if completed != 1:
        raise MCPTransportError("mcp_response")


async def _drain_close(close: Awaitable[None]) -> None:
    """Close sockets even if the caller repeatedly cancels during cleanup."""
    task = asyncio.create_task(close)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    if cancelled:
        if not task.cancelled():
            task.exception()
        raise asyncio.CancelledError
    task.result()


class _FixedMCPTransport(httpx.AsyncBaseTransport):
    def __init__(self, endpoint: str, inner: httpx.AsyncBaseTransport,
                 before_business: Callable[[], Awaitable[Any]] | None = None) -> None:
        self._endpoint = httpx.URL(endpoint)
        self._inner = inner
        self._identity = _current_identity()
        self._lock = asyncio.Lock()
        self._step = 0
        self._ids: set[tuple[type, object]] = set()
        self._failed = False
        self.failure: MCPTransportError | UsageError | None = None
        self.failed = asyncio.Event()
        self._before_business = before_business

    async def _check_business(self) -> None:
        if self._before_business is None:
            return
        try:
            await self._before_business()
        except (UsageError, asyncio.CancelledError):
            raise
        except Exception:
            # A trusted hook can perform current-version checks; its private
            # storage exceptions or user content never enter SDK traces.
            raise MCPTransportError("mcp_context") from None

    async def _prepare(self, request: httpx.Request) -> tuple[str, tuple[type, object] | None]:
        if (_current_identity() != self._identity or request.url != self._endpoint
                or request.method != "POST" or self._failed or self._step >= MAX_POSTS):
            raise MCPTransportError("mcp_request")
        if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
            raise MCPTransportError("mcp_request")
        declared = request.headers.get("content-length")
        if declared is not None and (not declared.isdecimal() or int(declared) > MAX_INPUT_BYTES):
            raise MCPTransportError("mcp_input_limit")
        body = bytearray()
        async for chunk in request.stream:
            if len(body) + len(chunk) > MAX_INPUT_BYTES:
                raise MCPTransportError("mcp_input_limit")
            body.extend(chunk)
        request.stream = httpx.ByteStream(bytes(body))
        try:
            message = strict_json(body)
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise MCPTransportError("mcp_request") from None
        if (type(message) is not dict or message.get("jsonrpc") != "2.0"
                or message.get("method") != _STEPS[self._step]
                or set(message) - {"jsonrpc", "id", "method", "params"}):
            raise MCPTransportError("mcp_request")
        method = message["method"]
        identifier = None
        params = message.get("params", {})
        if method == "notifications/initialized":
            if "id" in message or params != {}:
                raise MCPTransportError("mcp_request")
        else:
            identifier = _request_id(message.get("id"))
            if identifier in self._ids:
                raise MCPTransportError("mcp_request")
            self._ids.add(identifier)
        if method == "initialize":
            if (type(params) is not dict or set(params) != {"protocolVersion", "capabilities", "clientInfo"}
                    or type(params["protocolVersion"]) is not str
                    or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", params["protocolVersion"]) is None
                    or params["capabilities"] != {} or type(params["clientInfo"]) is not dict
                    or set(params["clientInfo"]) != {"name", "version"}
                    or not all(_safe_text(item, 80) for item in params["clientInfo"].values())):
                raise MCPTransportError("mcp_request")
        elif method == "tools/list":
            if params != {}:
                raise MCPTransportError("mcp_request")
        elif method == "tools/call":
            if (type(params) is not dict or set(params) != {"name", "arguments"}
                    or params["name"] != "maps_text_search" or not _arguments(params["arguments"])):
                raise MCPTransportError("mcp_request")
        self._step += 1
        return method, identifier

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        # SDK starts an unsolicited GET event stream after initialize.  A local
        # 405 is a protocol rejection, never a business result or network send.
        if (request.method == "GET" and request.url == self._endpoint
                and _current_identity() == self._identity):
            return httpx.Response(405, content=b"", request=request)
        try:
            async with self._lock:
                return await self._send_once(request)
        except asyncio.CancelledError:
            self._failed = True
            if self.failure is None:
                self.failure = MCPTransportError("mcp_cancelled")
            self.failed.set()
            raise
        except BaseException as error:
            self._failed = True
            # SDK background tasks can otherwise turn a durable quota error
            # into a generic protocol failure or timeout. Preserve only the
            # original safe UsageError (or a fixed transport code), never raw
            # HTTP exceptions carrying credential-bearing URLs or RPC bodies.
            if self.failure is None:
                self.failure = error if isinstance(error, (UsageError, MCPTransportError)) else MCPTransportError()
            self.failed.set()
            raise

    async def _send_once(self, request: httpx.Request) -> httpx.Response:
        attempt = None
        try:
            method, identifier = await self._prepare(request)
            if method == "tools/call":
                await self._check_business()
                attempt = await _start("amap_poi")
            # A queued cancellation cannot dispatch after persistent work.
            await asyncio.sleep(0)
            if _current_identity() != self._identity:
                raise MCPTransportError("mcp_context")
            if method == "tools/call":
                # Initialization and durable reservation both await. Recheck
                # the actual operation version immediately before dispatch,
                # not only when the API first selected this provider.
                await self._check_business()
                if _current_identity() != self._identity:
                    raise MCPTransportError("mcp_context")
            response = await self._inner.handle_async_request(request)
            try:
                for name, maximum in (("mcp-session-id", 128), ("mcp-protocol-version", 32)):
                    value = response.headers.get(name)
                    if value is not None and (not 1 <= len(value) <= maximum
                                              or any(not 33 <= ord(character) <= 126 for character in value)):
                        raise MCPTransportError("mcp_response")
                declared = response.headers.get("content-length")
                if declared is not None and (not declared.isdecimal() or int(declared) > MAX_RESPONSE_BYTES):
                    raise MCPTransportError("mcp_response_limit")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise MCPTransportError("mcp_response_limit")
                    body.extend(chunk)
            finally:
                await _drain_close(response.aclose())
            if attempt is not None:
                await attempt.finish("received")
            if 300 <= response.status_code <= 399:
                raise MCPTransportError("mcp_redirect")
            if response.is_success:
                if identifier is None:
                    if response.status_code not in {200, 202, 204} or body:
                        raise MCPTransportError("mcp_response")
                elif response.status_code != 200:
                    raise MCPTransportError("mcp_response")
                else:
                    _validate_response(bytes(body), response.headers.get("content-type", ""), identifier)
            headers = {name: value for name, value in response.headers.items()
                       if name.lower() not in {"content-encoding", "content-length", "transfer-encoding"}}
            return httpx.Response(response.status_code, headers=headers, content=bytes(body), request=request)
        except asyncio.CancelledError:
            if attempt is not None:
                await attempt.finish("cancelled")
            raise
        except BaseException:
            if attempt is not None:
                await attempt.finish("failed")
            raise

    async def aclose(self) -> None:
        await _drain_close(self._inner.aclose())


class _FixedMCPClient(httpx.AsyncClient):
    @property
    def mcp_failed(self) -> asyncio.Event:
        """Wake the SDK owner if its background transport failed safely."""
        return self._transport.failed

    @property
    def mcp_failure(self) -> MCPTransportError | UsageError | None:
        """Safe failure latch for the request's SDK owner; no raw exceptions."""
        return self._transport.failure

    async def send(self, request: httpx.Request, *, stream: bool = False,
                   auth: Any = httpx.USE_CLIENT_DEFAULT,
                   follow_redirects: Any = httpx.USE_CLIENT_DEFAULT) -> httpx.Response:
        if (self.follow_redirects or follow_redirects not in (False, httpx.USE_CLIENT_DEFAULT)
                or self.auth is not None or auth not in (None, httpx.USE_CLIENT_DEFAULT)):
            raise MCPTransportError("mcp_request")
        return await super().send(request, stream=stream, auth=None, follow_redirects=False)


def create_http_client(key: str, *, transport: httpx.AsyncBaseTransport | None = None,
                       before_business: Callable[[], Awaitable[Any]] | None = None) -> httpx.AsyncClient:
    """One owner-bound MCP search client; caller owns the overall 20s deadline."""
    endpoint = endpoint_for(key)
    if before_business is not None and not callable(before_business):
        raise MCPTransportError("mcp_configuration")
    quiet_sdk_logs()
    inner = transport if transport is not None else httpx.AsyncHTTPTransport(retries=0, trust_env=False)
    return _FixedMCPClient(
        transport=_FixedMCPTransport(endpoint, inner, before_business), trust_env=False,
        follow_redirects=False, timeout=httpx.Timeout(DEADLINE_SECONDS, connect=5),
    )
