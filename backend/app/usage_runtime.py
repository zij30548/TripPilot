"""Per-attempt production metering; identity is supplied only by server middleware.

Reservations and dispatch intent are durable before transport/decoder work. A
dispatching row is deliberately conservative: a crash cannot prove whether the
peer received the request. No network error, cancellation or parser failure is a
refund, and cancellation never abandons an in-flight SQLite worker.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
import re
from typing import TypeVar

import httpx

from app.business_usage import Service, UsageError, UsageStore, get_usage_store

T = TypeVar("T")


@dataclass(frozen=True)
class UsageContext:
    owner_id: str
    request_path: str
    store: UsageStore | None = None


_CONTEXT: ContextVar[UsageContext | None] = ContextVar("business_usage_context", default=None)


def bind_usage_context(owner_id: str, request_path: str, store: UsageStore | None = None) -> Token:
    """Trusted server binding; tests may inject a real independent UsageStore.

    There is no request parameter, bypass switch, or unmetered store mode.
    """
    return _CONTEXT.set(UsageContext(owner_id, request_path, store))


def reset_usage_context(token: Token) -> None:
    _CONTEXT.reset(token)


def _context() -> UsageContext:
    context = _CONTEXT.get()
    if context is None:
        raise UsageError()
    return context


async def _persist(function: Callable[..., T], *arguments: object) -> T:
    """Drain a DB thread even if cancellation is repeated; never dispatch after it."""
    task = asyncio.create_task(asyncio.to_thread(function, *arguments))
    interrupted = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            interrupted = True
    if interrupted:
        # Retrieve failures without leaking details; reservation remains counted.
        if not task.cancelled():
            task.exception()
        raise asyncio.CancelledError
    return task.result()


class _Attempt:
    def __init__(self, store: UsageStore, identifier: str):
        self.store, self.identifier = store, identifier
        self.finished = False
        self.lock = asyncio.Lock()

    async def finish(self, state: str) -> None:
        async with self.lock:
            if not self.finished:
                try:
                    await _persist(self.store.mark, self.identifier, state)
                except asyncio.CancelledError:
                    # The thread was drained. Do not attempt a different terminal
                    # transition when httpx subsequently closes this response.
                    self.finished = True
                    raise
                self.finished = True


async def _start(service: Service) -> _Attempt:
    context = _context()
    store = context.store if context.store is not None else get_usage_store()
    identifier = await _persist(store.reserve, context.owner_id, service)
    attempt = _Attempt(store, identifier)
    try:
        await _persist(store.mark, identifier, "dispatching")
        # Cancellation pending before this point must not reach the transport.
        await asyncio.sleep(0)
    except asyncio.CancelledError:
        await attempt.finish("cancelled")
        raise
    return attempt


_AMAP_PATHS: dict[str, Service] = {
    "/v5/place/text": "amap_poi",
    "/v3/direction/walking": "amap_route",
    "/v3/direction/transit/integrated": "amap_route",
    "/v3/weather/weatherInfo": "amap_weather",
    "/v3/config/district": "amap_proxy",
}
_TEXT_PATHS = frozenset({"/trips/schedule-explanation", "/trips/adjustment-intent", "/trips/itinerary-query"})


def _service(request: httpx.Request) -> Service:
    context = _context()
    url = request.url
    if url.scheme != "https" or url.port not in {None, 443} or url.username or url.password or url.fragment:
        raise UsageError()
    if request.method == "GET" and url.host == "restapi.amap.com" and url.path in _AMAP_PATHS:
        return _AMAP_PATHS[url.path]
    beijing = url.host == "dashscope.aliyuncs.com" or re.fullmatch(
        r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.cn-beijing\.maas\.aliyuncs\.com", url.host)
    if request.method == "POST" and beijing and url.path == "/compatible-mode/v1/chat/completions" and not url.query:
        if context.request_path == "/media/landmark":
            return "qwen_vision"
        if context.request_path in _TEXT_PATHS or re.fullmatch(
                r"/chat/conversations/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}/(?:turns|places)",
                context.request_path):
            return "qwen_text"
    raise UsageError()


class _MeteredStream(httpx.AsyncByteStream):
    def __init__(self, inner: httpx.AsyncByteStream, attempt: _Attempt):
        self.inner, self.attempt = inner, attempt

    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            async for chunk in self.inner:
                yield chunk
        except asyncio.CancelledError:
            await self.attempt.finish("cancelled")
            raise
        except BaseException:
            await self.attempt.finish("failed")
            raise
        else:
            await self.attempt.finish("received")

    async def aclose(self) -> None:
        try:
            await self.inner.aclose()
        finally:
            # Closing without reading all bytes is uncertain, never a refund.
            await self.attempt.finish("cancelled")


class MeteredTransport(httpx.AsyncBaseTransport):
    """Controlled transport helper; production uses proxy-preserving client below."""
    def __init__(self, inner: httpx.AsyncBaseTransport):
        self.inner = inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await _dispatch(request, lambda: self.inner.handle_async_request(request))

    async def aclose(self) -> None:
        await self.inner.aclose()


async def _dispatch(request: httpx.Request, send: Callable[[], Awaitable[httpx.Response]]) -> httpx.Response:
    attempt = await _start(_service(request))
    try:
        response = await send()
    except asyncio.CancelledError:
        await attempt.finish("cancelled")
        raise
    except BaseException:
        await attempt.finish("failed")
        raise
    if response.is_closed:
        # MockTransport/cached transports may return an already-read body.
        await attempt.finish("received")
    else:
        response.stream = _MeteredStream(response.stream, attempt)
    return response


class MeteredAsyncClient(httpx.AsyncClient):
    """Meter public send(), retaining httpx's existing TLS/environment proxies.

    A custom default transport suppresses environment-proxy mounts in httpx.
    This client deliberately retains ordinary construction instead. Production
    adapters disable redirects and use a static Authorization header, not an
    authentication flow that could send unmetered follow-up requests.
    """
    async def send(self, request: httpx.Request, *, stream: bool = False,
                   auth=httpx.USE_CLIENT_DEFAULT, follow_redirects=httpx.USE_CLIENT_DEFAULT) -> httpx.Response:
        if (self.follow_redirects or follow_redirects not in (False, httpx.USE_CLIENT_DEFAULT)
                or self.auth is not None or auth is not None and auth is not httpx.USE_CLIENT_DEFAULT):
            raise UsageError()

        async def send_once() -> httpx.Response:
            return await super(MeteredAsyncClient, self).send(
                request, stream=True, auth=auth, follow_redirects=False)

        response = await _dispatch(request, send_once)
        if not stream:
            try:
                await response.aread()
            except BaseException:
                await response.aclose()
                raise
        return response


@asynccontextmanager
async def media_usage(service: Service) -> AsyncIterator[None]:
    if service not in {"media_photo", "media_video"}:
        raise UsageError()
    attempt = await _start(service)
    try:
        yield
    except asyncio.CancelledError:
        await attempt.finish("cancelled")
        raise
    except BaseException as error:
        await attempt.finish("cancelled" if getattr(error, "status_code", None) == 499 else "failed")
        raise
    else:
        await attempt.finish("received")
