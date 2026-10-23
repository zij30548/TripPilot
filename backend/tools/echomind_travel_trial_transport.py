"""Controlled-only admission using the exact production metered client.

This tool has no live mode, credentials/config discovery, URL parameter or real
HTTP transport. Request hooks run *after* the production durable reservation,
but before the controlled transport. A hook refusal may therefore occupy a
product attempt while making zero external calls. A received HTTP body is not
a semantic pass. No old live authorization or transport subclass is reused.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from copy import deepcopy
import hashlib
import json
import re
import time
from typing import Any
import zlib

import httpx
from jsonschema import Draft202012Validator, SchemaError, ValidationError

from core.llm_utils import RuntimeFailure, strict_json
from core.schemas.dialogue import GROUPS
from core.usage_runtime import MeteredAsyncClient
from tools.echomind_travel_trial_ledger import Ledger, LedgerError

VERSION = "echomind-travel-controlled-transport-v1"
URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
MODEL = "qwen3.7-flash-2026-07-15"
PARAMETERS = {"model": MODEL, "max_tokens": 900, "temperature": .1, "stream": False,
              "enable_thinking": False, "parallel_tool_calls": False, "tool_choice": "auto"}
MAX_INPUT, MAX_OUTPUT = 48_000, 32_768
KNOWN_TOOLS = frozenset(GROUPS) | {"inspect_trip", "read_preferences", "search_materials",
    "travel_clarify", "travel_information", "update_trip_requirements"}
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


class TransportStopped(RuntimeError):
    """Fixed diagnostic codes only; never include bodies, headers or names."""


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _no_references(schema: Any) -> None:
    """The current builder inlines schemas; validation must never retrieve URI.

    Refuse local references as well instead of introducing another resolver or
    network-capable validation path in this controlled-only preparation tool.
    """
    pending, nodes = [(schema, 0)], 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > 512 or depth > 16:
            raise ValueError()
        if type(item) is dict:
            if {"$ref", "$dynamicRef", "$recursiveRef"} & set(item):
                raise ValueError()
            pending.extend((value, depth + 1) for value in item.values())
        elif type(item) is list:
            pending.extend((value, depth + 1) for value in item)


async def _persist(function: Callable[[], Any]) -> tuple[Any, bool]:
    """Drain the actual fsync worker before propagating repeated cancellation."""
    task = asyncio.create_task(asyncio.to_thread(function))
    interrupted = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            interrupted = True
    return task.result(), interrupted


def _payload(value: Any) -> dict[str, Any]:
    try:
        if type(value) is not dict or set(value) != set(PARAMETERS) | {"messages", "tools"}:
            raise ValueError()
        for key, expected in PARAMETERS.items():
            if type(value[key]) is not type(expected) or value[key] != expected:
                raise ValueError()
        if len(canonical(value)) > MAX_INPUT:
            raise ValueError()
        messages, tools = value["messages"], value["tools"]
        if (type(messages) is not list or not 2 <= len(messages) <= 65
                or type(tools) is not list or not 1 <= len(tools) <= 16
                or messages[0].get("role") != "system" or not messages[0].get("content")):
            raise ValueError()
        names = set()
        for item in tools:
            if type(item) is not dict or set(item) != {"type", "function"} or item["type"] != "function":
                raise ValueError()
            function = item["function"]
            if (type(function) is not dict or set(function) != {"name", "description", "parameters"}
                    or function["name"] not in KNOWN_TOOLS or function["name"] in names
                    or type(function["description"]) is not str or not 1 <= len(function["description"]) <= 2000):
                raise ValueError()
            _no_references(function["parameters"])
            Draft202012Validator.check_schema(function["parameters"])
            names.add(function["name"])
        return value
    except (KeyError, TypeError, ValueError, AttributeError, SchemaError, RecursionError):
        raise TransportStopped("controlled_payload_invalid") from None


def _decode(raw: bytes, encoding: str) -> bytes:
    if encoding in {"", "identity"}:
        return raw
    windows = (16 + zlib.MAX_WBITS,) if encoding == "gzip" else (zlib.MAX_WBITS, -zlib.MAX_WBITS)
    if encoding not in {"gzip", "deflate"}:
        raise TransportStopped("controlled_response_encoding")
    for window in windows:
        try:
            decoder = zlib.decompressobj(window)
            result = decoder.decompress(raw, MAX_OUTPUT + 1)
            if len(result) > MAX_OUTPUT or decoder.unconsumed_tail or decoder.unused_data or not decoder.eof:
                raise TransportStopped("controlled_response_limit")
            return result
        except zlib.error:
            continue
    raise TransportStopped("controlled_response_encoding")


class _Lease:
    def __init__(self, ledger: Ledger, operation_id: str, binding_sha256: str, question: str,
                 initial_payload: dict[str, Any], current_binding: Callable[[], str] | None):
        if (type(ledger) is not Ledger or type(binding_sha256) is not str or not _HASH.fullmatch(binding_sha256)
                or type(question) is not str or not 1 <= len(question) <= 2000):
            raise TransportStopped("controlled_binding_required")
        self.initial = deepcopy(_payload(initial_payload))
        if self.initial["messages"][-1] != {"role": "user", "content": question}:
            raise TransportStopped("controlled_question_binding")
        self.ledger, self.operation_id, self.binding = ledger, operation_id, binding_sha256
        self.current_binding = current_binding or (lambda: binding_sha256)
        self.previous: dict[str, Any] | None = None
        self.calls: list[dict[str, Any]] | None = None
        self.pending: str | None = None
        self.last_status: int | None = None
        self.started = 0.0
        self.lock = asyncio.Lock()
        self.closed = False

    def _feedback(self, payload: dict[str, Any]) -> None:
        assert self.previous is not None
        previous, messages = self.previous["messages"], payload["messages"]
        calls = self.calls
        if (not calls or len(messages) != len(previous) + len(calls) + 1 or messages[:len(previous)] != previous):
            raise TransportStopped("controlled_feedback_chain")
        assistant = messages[len(previous)]
        if (type(assistant) is not dict or set(assistant) != {"role", "content", "tool_calls"}
                or assistant["role"] != "assistant" or assistant["content"] is not None
                or type(assistant["tool_calls"]) is not list or len(assistant["tool_calls"]) != len(calls)):
            raise TransportStopped("controlled_feedback_chain")
        for actual, expected in zip(assistant["tool_calls"], calls, strict=True):
            try:
                if (set(actual) != {"id", "type", "function"} or actual["id"] != expected["id"]
                        or actual["type"] != "function" or set(actual["function"]) != {"name", "arguments"}
                        or actual["function"]["name"] != expected["function"]["name"]
                        or canonical(strict_json(actual["function"]["arguments"])) != canonical(
                            strict_json(expected["function"]["arguments"]))):
                    raise ValueError()
            except (KeyError, ValueError, TypeError, RecursionError):
                raise TransportStopped("controlled_feedback_chain") from None
        for result, selected in zip(messages[len(previous) + 1:], calls, strict=True):
            if (type(result) is not dict or set(result) != {"role", "tool_call_id", "content"}
                    or result["role"] != "tool" or result["tool_call_id"] != selected["id"]
                    or type(result["content"]) is not str or len(result["content"].encode()) > MAX_OUTPUT):
                raise TransportStopped("controlled_feedback_chain")
            try:
                strict_json(result["content"])
            except (ValueError, TypeError, RecursionError):
                raise TransportStopped("controlled_feedback_chain") from None

    async def request(self, request: httpx.Request) -> None:
        try:
            await self._request(request)
        except BaseException:
            # A candidate may catch provider exceptions inside the yield. Stop
            # durably here rather than relying only on the outer context. In
            # particular, a refusal before claim must not seal a zero-send op
            # and permit the next planned operation after an execution error.
            await self.pause_preserving_error()
            raise

    async def pause_preserving_error(self) -> None:
        try:
            await _persist(self.ledger.pause)
        except BaseException:
            # The persistence error has already poisoned/occupied the ledger.
            # Never replace the original exception or attempt a forced seal.
            pass

    async def _request(self, request: httpx.Request) -> None:
        async with self.lock:
            if self.closed or self.pending is not None or self.current_binding() != self.binding:
                raise TransportStopped("controlled_context_changed_or_pending")
            if (str(request.url) != URL or request.method != "POST"
                    or request.headers.get("authorization") != "Bearer controlled-not-real"):
                raise TransportStopped("controlled_endpoint_or_credentials")
            try:
                raw = request.content
                if len(raw) > MAX_INPUT:
                    raise ValueError()
                payload = _payload(strict_json(raw))
            except (ValueError, TypeError, RecursionError, httpx.RequestNotRead):
                raise TransportStopped("controlled_payload_invalid") from None
            if {key: value for key, value in payload.items() if key != "messages"} != {
                    key: value for key, value in self.initial.items() if key != "messages"}:
                raise TransportStopped("controlled_frozen_configuration_changed")
            if self.previous is None:
                if canonical(payload) != canonical(self.initial):
                    raise TransportStopped("controlled_initial_payload_changed")
            else:
                self._feedback(payload)
            attempt, interrupted = await _persist(lambda: self.ledger.claim(
                self.operation_id, digest(raw), binding_sha256=self.binding))
            self.pending, self.previous, self.calls = attempt, deepcopy(payload), None
            self.started = time.monotonic()
            if interrupted:
                raise asyncio.CancelledError
            if self.current_binding() != self.binding:
                raise TransportStopped("controlled_context_changed_or_pending")

    def _selected(self, raw: bytes) -> list[dict[str, Any]] | None:
        """Inspect a bounded copy for next-round binding, never repair outputs."""
        try:
            data = strict_json(raw)
            choice = data["choices"][0]
            calls = choice["message"]["tool_calls"]
            if (len(data["choices"]) != 1 or choice["finish_reason"] != "tool_calls"
                    or choice["message"].get("role") != "assistant"
                    or choice["message"].get("content") not in (None, "")
                    or type(calls) is not list or not 1 <= len(calls) <= 4):
                return None
            schemas = {tool["function"]["name"]: tool["function"]["parameters"] for tool in self.initial["tools"]}
            result, seen = [], set()
            for index, call in enumerate(calls):
                if (type(call) is not dict or set(call) - {"id", "index", "type", "function"}
                        or call["type"] != "function" or not _ID.fullmatch(call["id"]) or call["id"] in seen
                        or "index" in call and (type(call["index"]) is not int or call["index"] != index)
                        or set(call["function"]) != {"name", "arguments"}
                        or call["function"]["name"] not in schemas):
                    return None
                arguments = strict_json(call["function"]["arguments"])
                Draft202012Validator(schemas[call["function"]["name"]]).validate(arguments)
                seen.add(call["id"])
                result.append({key: deepcopy(call[key]) for key in ("id", "type", "function")})
            return result
        except (ValueError, TypeError, KeyError, IndexError, AttributeError, ValidationError, RecursionError):
            return None

    async def finish(self, state: str, raw: bytes | None = None, encoding: str = "") -> None:
        async with self.lock:
            if self.pending is None:
                return
            metadata: dict[str, Any] = {"request_model": MODEL}
            elapsed = int((time.monotonic() - self.started) * 1000)
            if 0 <= elapsed <= 120_000:
                metadata["duration_ms"] = elapsed
            if self.last_status is not None:
                metadata["status_code"] = self.last_status
                if self.last_status != 200 and state == "received":
                    state = "failed"
            if raw is not None:
                metadata["response_sha256"] = digest(raw)
                decoded = _decode(raw, encoding)
                self.calls = self._selected(decoded) if self.last_status == 200 else None
                metadata["native_tool_calls"] = len(self.calls or [])
                metadata["upstream_model"] = "unknown"
                try:
                    parsed = strict_json(decoded)
                    if type(parsed) is dict:
                        if parsed.get("model") == MODEL:
                            metadata["upstream_model"] = MODEL
                        usage = parsed.get("usage")
                        if type(usage) is dict:
                            for original, safe in (("prompt_tokens", "input_tokens"), ("completion_tokens", "output_tokens")):
                                value = usage.get(original)
                                metadata[safe] = value if type(value) is int and 0 <= value <= 10_000_000 else None
                except (ValueError, TypeError, RecursionError):
                    pass  # Invalid business JSON is not repaired or called a pass.
            attempt = self.pending
            _, interrupted = await _persist(lambda: self.ledger.finish(attempt, state, metadata))
            self.pending = None
            if interrupted:
                raise asyncio.CancelledError

    async def response(self, response: httpx.Response) -> None:
        self.last_status = response.status_code
        if response.is_closed:
            if len(response.content) > MAX_OUTPUT:
                await self.finish("failed")
                raise TransportStopped("controlled_response_limit")
            await self.finish("received", response.content)
        else:
            response.stream = _Capture(response.stream, self, response.headers.get("content-encoding", "").strip().lower())


class _Capture(httpx.AsyncByteStream):
    def __init__(self, inner: httpx.AsyncByteStream, lease: _Lease, encoding: str):
        self.inner, self.lease, self.encoding = inner, lease, encoding
        self.body = bytearray()
        self.done = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            async for chunk in self.inner:
                if len(self.body) + len(chunk) > MAX_OUTPUT:
                    raise TransportStopped("controlled_response_limit")
                self.body.extend(chunk)
                yield chunk
            await self.lease.finish("received", bytes(self.body), self.encoding)
            self.done = True
        except asyncio.CancelledError:
            await self.lease.finish("cancelled")
            self.done = True
            raise
        except BaseException:
            await self.lease.finish("failed")
            self.done = True
            raise

    async def aclose(self) -> None:
        try:
            await self.inner.aclose()
        finally:
            if not self.done:
                await self.lease.finish("unknown")
                self.done = True


@asynccontextmanager
async def controlled_client(ledger: Ledger, operation_id: str, binding_sha256: str,
                            question: str, initial_payload: dict[str, Any],
                            handler: Callable[[httpx.Request], Any], *,
                            current_binding: Callable[[], str] | None = None) -> AsyncIterator[MeteredAsyncClient]:
    """One fixed operation; no real URL, model, credentials or transport option.

    The handler is an explicitly scripted MockTransport peer, not a live sender.
    This preserves native provider payloads and normal product-store metering.
    Transport failures without response hooks stay occupied and pause admission.
    """
    try:
        lease = _Lease(ledger, operation_id, binding_sha256, question, initial_payload, current_binding)
    except BaseException:
        if type(ledger) is Ledger:
            try:
                await _persist(ledger.pause)
            except BaseException:
                pass
        raise
    failed = False
    caught_error = False
    try:
        _, interrupted = await _persist(lambda: ledger.start_operation(operation_id, binding_sha256))
        if interrupted:
            raise asyncio.CancelledError
        async with MeteredAsyncClient(transport=httpx.MockTransport(handler), trust_env=False,
                follow_redirects=False, timeout=httpx.Timeout(20, connect=5),
                headers={"Authorization": "Bearer controlled-not-real"},
                event_hooks={"request": [lease.request], "response": [lease.response]}) as client:
            yield client
    except asyncio.CancelledError:
        caught_error = True
        failed = True
        try:
            await lease.finish("cancelled")
        except BaseException:
            pass
        await lease.pause_preserving_error()
        raise
    except BaseException as error:
        caught_error = True
        # An already received ordinary business/structure failure belongs to
        # the separate score, not execution admission. All actual transport,
        # binding, counter or programming failures conservatively stop here.
        execution_error = (lease.pending is not None or not isinstance(error, RuntimeFailure)
                           or isinstance(error, (TransportStopped, LedgerError, httpx.HTTPError, TimeoutError)))
        if execution_error:
            failed = True
            try:
                await lease.finish("unknown")
            except BaseException:
                pass
            await lease.pause_preserving_error()
        raise
    finally:
        lease.closed = True
        if lease.pending is not None and not failed:
            failed = True
            try:
                await lease.finish("unknown")
            except BaseException:
                await lease.pause_preserving_error()
                if not caught_error:
                    raise
        # A paused ledger refuses this transition. Preserve the original error;
        # no sealing failure is permission to repeat an operation or attempt.
        if not failed:
            try:
                if not ledger.summary().get("paused"):
                    _, interrupted = await _persist(lambda: ledger.seal_operation(operation_id))
                    if interrupted:
                        raise asyncio.CancelledError
            except BaseException:
                await lease.pause_preserving_error()
                if not caught_error:
                    raise
