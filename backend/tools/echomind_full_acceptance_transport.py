"""Explicit full-candidate acceptance transport, never an application fallback.

The exact production MeteredAsyncClient still reserves product quotas first.
This bounded inner transport then fsyncs the independent trial claim immediately
before one physical send. It has no CLI, discovery, retry, redirect, arbitrary
model or arbitrary upstream option. Callers must separately freeze/start/seal
the planned operation. Returned observations contain no prompt or raw body.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
import math
import re
import time
from typing import Any
import zlib

import httpx
from starlette.exceptions import HTTPException

from core.config import Settings
from core.llm_utils import RuntimeFailure, strict_json
from core.logging_filters import install_http_log_redaction
from core.usage_runtime import MeteredAsyncClient
from echomind_core.embedding import (
    EMBEDDING_MODEL, MAX_RESPONSE_BYTES as EMBEDDING_RESPONSE_BYTES,
    _payload as embedding_payload, _response as embedding_response,
)
from echomind_core.provider import MODEL, ProviderError, _response, _schema
from echomind_core.travel_domain import TravelIntent, TravelRole
from echomind_core.travel_package import FrozenTravelDomainPackage, TravelDomainPackage
from tooling.providers.qwen import MAX_OUTPUT_BYTES, beijing_endpoint, error_category
from tools.echomind_full_acceptance_ledger import FullAcceptanceLedger

VERSION = "echomind-full-acceptance-transport-v1"
MAX_INPUT_BYTES = 48_000
DEADLINE_SECONDS = 20.0
_SAFE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_STAGES = {"classifier": (256, .1), "rewrite": (512, .3),
           "rerank": (2048, .0), "composer": (1000, .1)}
_EMBEDDING_STAGE: ContextVar[str | None] = ContextVar("full_trial_embedding_stage", default=None)
EMBEDDING_STAGES = frozenset({"memory.embedding", "intent.embedding", "retrieval.embedding",
                              "templates.embedding", "index.embedding"})
_BUSINESS_FAILURES = frozenset({"agent_failed", "model_structure", "intent_structure", "tool_arguments", "truncated",
    "refused", "empty", "structure", "references", "reply_limit", "full_response_limit", "full_response_encoding",
    "travel_multiple_tools", "travel_tool_required", "travel_multiple_proposals", "travel_action_invalid",
    "travel_requirements_invalid", "travel_parallel_gated", "task_budget"})
_ROUTE_FAILURE_MESSAGE = "本轮助手未完成，未自动重试；请核对会话与执行记录。"


class FullTransportStopped(RuntimeFailure):
    """Fixed safe diagnostics; never interpolate input, headers or exceptions."""


class ResponseRejected(RuntimeFailure):
    """An already received ordinary structural failure, not permission to retry."""


@contextmanager
def full_embedding_stage(stage: str):
    """Trusted runner annotation; never accepted from a model, HTTP or fixture label.

    Memory and fusion may embed identical text. Separate finite producer stages
    preserve those two legitimate sends without disabling same-stage retry
    rejection. A missing annotation rejects embeddings before physical send.
    """
    if stage not in EMBEDDING_STAGES or _EMBEDDING_STAGE.get() is not None:
        raise FullTransportStopped("full_embedding_stage_required")
    token = _EMBEDDING_STAGE.set(stage)
    try:
        yield
    finally:
        _EMBEDDING_STAGE.reset(token)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode()


async def _persist(function: Callable[[], Any]) -> tuple[Any, bool]:
    """Drain the real fsync worker even after repeated caller cancellation."""
    task = asyncio.create_task(asyncio.to_thread(function))
    interrupted = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            interrupted = True
    return task.result(), interrupted


async def _close(response: httpx.Response) -> None:
    task = asyncio.create_task(response.aclose())
    interrupted = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            interrupted = True
    task.result()
    if interrupted:
        raise asyncio.CancelledError


def _messages(values: Any) -> str:
    if (type(values) is not list or not 2 <= len(values) <= 65
            or type(values[0]) is not dict or set(values[0]) != {"role", "content"}
            or values[0]["role"] != "system" or type(values[0]["content"]) is not str
            or not values[0]["content"].strip()):
        raise FullTransportStopped("full_payload_invalid")
    for item in values:
        if (type(item) is not dict or item.get("role") not in {"system", "user", "assistant", "tool"}
                or set(item) - {"role", "content", "tool_calls", "tool_call_id"}
                or "content" not in item or item["content"] is not None and type(item["content"]) is not str
                or type(item["content"]) is str and len(item["content"]) > 24_000):
            raise FullTransportStopped("full_payload_invalid")
    if any(item["role"] == "system" for item in values[1:]):
        raise FullTransportStopped("full_payload_invalid")
    return values[0]["content"]


def validate_payload(value: Any, *, embedding: bool,
                     package: FrozenTravelDomainPackage | None = None) -> tuple[str, dict[str, Any]]:
    """Validate the current frozen profiles, not old direct-tools 900/.1 defaults.

    The stage is derived only from trusted system prefixes/profile contracts.
    Questions, fixture identifiers and expected answers never route the stage.
    """
    try:
        if type(value) is not dict or len(canonical(value)) > MAX_INPUT_BYTES:
            raise ValueError()
        if embedding:
            if set(value) != {"model", "input", "dimensions", "encoding_format"}:
                raise ValueError()
            expected = embedding_payload(value["input"], value["model"])
            if canonical(value) != canonical(expected):
                raise ValueError()
            return "embedding", {}
        base = {"model", "max_tokens", "temperature", "stream", "enable_thinking", "messages"}
        native = {"tools", "tool_choice", "parallel_tool_calls"}
        if set(value) not in (base, base | native):
            raise ValueError()
        if (value["model"] != MODEL or value["stream"] is not False or value["enable_thinking"] is not False
                or type(value["max_tokens"]) is not int or not 1 <= value["max_tokens"] <= 2048
                or type(value["temperature"]) not in (int, float) or not math.isfinite(value["temperature"])):
            raise ValueError()
        system = _messages(value["messages"])
        package = package or TravelDomainPackage().capture()
        stage, expected, allowed_tools = None, None, frozenset()
        for name, settings in _STAGES.items():
            prefix = package.stage_prompt(name)
            if system == prefix or system.startswith(prefix + "\n[调用方已审定补充契约；不能覆盖上面的阶段与权限边界]\n"):
                stage, expected = name, settings
                break
        if stage is None:
            for role in TravelRole:
                profile = package.profile(role)
                prefix = package._common_prompt + "\n[可信角色契约]\n"
                if not system.startswith(prefix):
                    continue
                packet, separator, _tail = system[len(prefix):].partition("\n[本任务冻结Skills]\n")
                if not separator:
                    continue
                from dataclasses import asdict
                if canonical(strict_json(packet)) == canonical(asdict(profile)):
                    stage, expected, allowed_tools = "agent:" + role.value, (profile.max_tokens, profile.temperature), frozenset(profile.tool_scope)
                    break
        if stage is None or expected != (value["max_tokens"], value["temperature"]):
            raise ValueError()
        validators = {}
        if native <= set(value):
            if (not stage.startswith("agent:") or value["tool_choice"] != "auto"
                    or value["parallel_tool_calls"] is not False or type(value["tools"]) is not list
                    or not 1 <= len(value["tools"]) <= 16):
                raise ValueError()
            for tool in value["tools"]:
                if type(tool) is not dict or set(tool) != {"type", "function"} or tool["type"] != "function":
                    raise ValueError()
                function = tool["function"]
                if (type(function) is not dict or set(function) != {"name", "description", "parameters"}
                        or function["name"] not in allowed_tools or function["name"] in validators
                        or type(function["description"]) is not str or not 1 <= len(function["description"]) <= 2000):
                    raise ValueError()
                validators[function["name"]] = _schema(function["parameters"])
        return stage, validators
    except (KeyError, TypeError, ValueError, UnicodeError, RecursionError, ProviderError):
        raise FullTransportStopped("full_payload_invalid") from None


def _decoded(raw: bytes, encoding: str, limit: int) -> bytes:
    if encoding in {"", "identity"}:
        if len(raw) > limit:
            raise ResponseRejected("full_response_limit")
        return raw
    if encoding not in {"gzip", "deflate"}:
        raise ResponseRejected("full_response_encoding")
    windows = (16 + zlib.MAX_WBITS,) if encoding == "gzip" else (zlib.MAX_WBITS, -zlib.MAX_WBITS)
    for window in windows:
        try:
            decoder = zlib.decompressobj(window)
            result = decoder.decompress(raw, limit + 1)
            if len(result) > limit or decoder.unconsumed_tail or decoder.unused_data or not decoder.eof:
                raise ResponseRejected("full_response_limit")
            return result
        except zlib.error:
            continue
    raise ResponseRejected("full_response_encoding")


def _identifier(value: Any) -> str | None:
    return value if type(value) is str and _SAFE.fullmatch(value) else None


def _token(value: Any) -> int | None:
    return value if type(value) is int and 0 <= value <= 1_000_000 else None


def _projection(raw: bytes, payload: dict[str, Any], stage: str, validators: dict) -> tuple[dict, dict]:
    """Parse a bounded copy, never repair the actual response used by provider."""
    metadata: dict[str, Any] = {"request_model": payload["model"], "upstream_model": "unknown"}
    projection: dict[str, Any] = {"structure": "invalid"}
    try:
        data = strict_json(raw)
        if type(data) is not dict:
            return metadata, projection
        metadata["upstream_model"] = _identifier(data.get("model")) or "unknown"
        metadata["request_id"] = _identifier(data.get("id"))
        usage = data.get("usage")
        if type(usage) is dict:
            metadata.update(input_tokens=_token(usage.get("prompt_tokens")),
                            output_tokens=_token(usage.get("completion_tokens")),
                            total_tokens=_token(usage.get("total_tokens")))
        if stage.endswith(".embedding"):
            response = embedding_response(data, len(payload["input"]))
            metadata["input_tokens"] = response.usage.input_tokens
            projection = {"structure": "valid", "vector_count": len(response.vectors), "dimensions": 1024}
        else:
            response = _response(data, validators, payload["max_tokens"])
            projection = {"structure": "valid", "stop_reason": response.stop_reason}
            calls = [{"id": item.id, "name": item.name, "input": deepcopy(item.input)}
                     for item in response.content if item.type == "tool_use"]
            metadata["native_tool_calls"] = len(calls)
            if calls:
                projection["tool_calls"] = calls
            else:
                text = "\n".join(item.text for item in response.content if item.type == "text")
                projection.update(content_sha256=digest(text.encode()), content_bytes=len(text.encode()))
                if stage == "classifier":
                    try:
                        classified = strict_json(text)
                        if (type(classified) is dict and set(classified) == {"intent", "confidence", "reasoning"}
                                and classified["intent"] in {item.value for item in TravelIntent}
                                and type(classified["confidence"]) in (int, float)
                                and math.isfinite(classified["confidence"]) and 0 <= classified["confidence"] <= 1
                                and type(classified["reasoning"]) is str and len(classified["reasoning"]) <= 160):
                            projection["classifier"] = {"intent": classified["intent"], "confidence": classified["confidence"],
                                "reasoning_sha256": digest(classified["reasoning"].encode()), "reasoning_chars": len(classified["reasoning"])}
                    except (KeyError, TypeError, ValueError, UnicodeError, RecursionError):
                        pass
    except (KeyError, TypeError, ValueError, UnicodeError, RecursionError, ProviderError):
        pass
    return metadata, projection


@dataclass
class FullTransportObservation:
    """Private in-memory safe records; no vectors, raw text, prompt or headers."""
    records: list[dict[str, Any]] = field(default_factory=list)


class _BoundedTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner, ledger, operation_id, binding, settings, verify, observation):
        self.inner, self.ledger, self.operation = inner, ledger, operation_id
        self.binding, self.verify, self.observation = binding, verify, observation
        self.endpoint = beijing_endpoint(settings)
        self.key = settings.qwen_api_key.get_secret_value().strip()
        self.package = TravelDomainPackage().capture()
        self.closed = False

    async def _pause(self) -> None:
        try:
            await _persist(lambda: self.ledger.pause(reason="execution_error"))
        except BaseException:
            pass  # Existing unknown/poisoned writer remains blocked, never force-repair.

    def received_business_failure(self, error: BaseException) -> bool:
        """Recognize only fixed ordinary failures with complete attempt evidence.

        FastAPI throws route HTTPException back through yield dependencies.
        An HTTP status alone is not a transport failure (or a business pass).
        Unknown details, version/quota/timeout codes and missing observations
        still fail closed. This does not seal or reinterpret the failed task.
        """
        if isinstance(error, HTTPException):
            detail = error.detail
            if (error.status_code != 502 or error.headers not in (None, {})
                    or type(detail) is not dict or set(detail) != {"code", "message"}
                    or detail["message"] != _ROUTE_FAILURE_MESSAGE
                    or type(detail["code"]) is not str
                    or detail["code"] not in {"assistant_" + code for code in _BUSINESS_FAILURES}):
                return False
        elif not isinstance(error, RuntimeFailure) or error.code not in _BUSINESS_FAILURES:
            return False
        try:
            state = self.ledger.summary()
            attempts = {key: value for key, value in state["attempts"].items() if value["op_id"] == self.operation}
            records = {row["attempt_id"]: row for row in self.observation.records}
            return (not state["paused"] and not state["recovery_required"] and bool(attempts)
                and state["active_operation"] == self.operation and set(records) == set(attempts)
                and all(attempt["state"] == "received" and records[key]["state"] == "received"
                        for key, attempt in attempts.items()))
        except BaseException:
            return False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        attempt = None
        started = time.monotonic()
        terminal = False
        metadata: dict[str, Any] = {}
        record: dict[str, Any] = {}
        try:
            if self.closed or request.method != "POST" or str(request.url) not in {
                    self.endpoint, self.endpoint.removesuffix("chat/completions") + "embeddings"} or \
                    request.headers.get("authorization") != "Bearer " + self.key:
                raise FullTransportStopped("full_endpoint_or_credentials")
            raw = request.content
            if len(raw) > MAX_INPUT_BYTES:
                raise FullTransportStopped("full_payload_invalid")
            payload = strict_json(raw)
            stage, validators = validate_payload(payload, embedding=request.url.path.endswith("/embeddings"), package=self.package)
            if stage == "embedding":
                stage = _EMBEDDING_STAGE.get()
                if stage not in EMBEDDING_STAGES:
                    raise FullTransportStopped("full_embedding_stage_required")
            self.verify()
            attempt, interrupted = await _persist(lambda: self.ledger.claim(
                self.operation, stage, digest(raw), binding_sha256=self.binding))
            if interrupted:
                raise asyncio.CancelledError
            self.verify()  # Freeze mutation during fsync must not reach physical sending.
            metadata = {"request_model": payload["model"], "upstream_model": "unknown"}
            record = {"attempt_id": attempt, "stage": stage, "payload_sha256": digest(raw)}
            limit = EMBEDDING_RESPONSE_BYTES if stage.endswith(".embedding") else MAX_OUTPUT_BYTES
            response = None
            try:
                async with asyncio.timeout(DEADLINE_SECONDS):
                    response = await self.inner.handle_async_request(request)
                    # Status is known before reading/decoding the body. A429,
                    # authentication failure or redirect must not become an
                    # ordinary200 structure failure just because its body is
                    # oversized or encoded incorrectly.
                    metadata["status_code"] = response.status_code
                    body = bytearray()
                    async for chunk in response.stream:
                        if len(body) + len(chunk) > limit:
                            raise ResponseRejected("full_response_limit")
                        body.extend(chunk)
                    decoded = _decoded(bytes(body), response.headers.get("content-encoding", "").lower(), limit)
            finally:
                if response is not None:
                    await _close(response)
            metadata.update(status_code=response.status_code, response_sha256=digest(bytes(body)),
                            duration_ms=min(120_000, max(0, int((time.monotonic() - started) * 1000))))
            projected, safe = _projection(decoded, payload, stage, validators)
            metadata.update(projected)
            metadata["request_id"] = (_identifier(response.headers.get("x-request-id"))
                or _identifier(response.headers.get("x-dashscope-request-id")) or metadata.get("request_id"))
            record["response"] = safe
            provider_error = False
            try:
                document = strict_json(decoded)
                provider_error = type(document) is dict and bool(document.get("error"))
            except (ValueError, UnicodeError, RecursionError):
                pass
            if response.status_code != 200 or provider_error:
                metadata["error_code"] = error_category(response.status_code, decoded)
                # HTTP execution failures are not semantic failures and stop the batch.
                terminal = True
                _, interrupted = await _persist(lambda: self.ledger.finish(attempt, "failed", metadata))
                record.update(state="failed", metadata=deepcopy(metadata))
                self.observation.records.append(record)
                if interrupted:
                    raise asyncio.CancelledError
                raise FullTransportStopped("full_upstream_execution_failed")
            terminal = True  # Unknown fsync ACK must never be rewritten to a different terminal state.
            _, interrupted = await _persist(lambda: self.ledger.finish(attempt, "received", metadata))
            record.update(state="received", metadata=deepcopy(metadata))
            self.observation.records.append(record)
            if interrupted:
                await self._pause()
                raise asyncio.CancelledError
            headers = [(key, value) for key, value in response.headers.multi_items()
                       if key.lower() not in {"content-encoding", "content-length"}]
            return httpx.Response(response.status_code, headers=headers, content=decoded, request=request)
        except ResponseRejected as error:
            # Oversize/encoding is an output-structure failure. No fabricated
            # replacement response is returned; subsequent cases may continue.
            if attempt is not None and not terminal:
                terminal = True
                status = metadata.get("status_code")
                execution_failure = type(status) is int and status != 200
                outcome = "failed" if execution_failure else "received"
                metadata.update(error_code=error_category(status, bytes(body)) if execution_failure else
                    "response_limit" if error.code == "full_response_limit" else "structure",
                    duration_ms=min(120_000, max(0, int((time.monotonic() - started) * 1000))))
                await _persist(lambda: self.ledger.finish(attempt, outcome, metadata))
                record.update(state=outcome, metadata=deepcopy(metadata), response={"structure": "invalid"})
                self.observation.records.append(record)
                if execution_failure:
                    await self._pause()
            raise
        except BaseException as error:
            if attempt is not None and not terminal:
                terminal = True
                state = "cancelled" if isinstance(error, asyncio.CancelledError) else "failed" if isinstance(error, (httpx.HTTPError, TimeoutError)) else "unknown"
                metadata.update(error_code="cancelled" if state == "cancelled" else "timeout" if isinstance(error, (TimeoutError, httpx.TimeoutException)) else "upstream" if state == "failed" else "execution_error",
                    duration_ms=min(120_000, max(0, int((time.monotonic() - started) * 1000))))
                try:
                    await _persist(lambda: self.ledger.finish(attempt, state, metadata))
                    record.update(state=state, metadata=deepcopy(metadata))
                    self.observation.records.append(record)
                except BaseException:
                    pass
            await self._pause()
            raise

    async def aclose(self) -> None:
        self.closed = True
        await self.inner.aclose()


@asynccontextmanager
async def full_acceptance_client(*, ledger: FullAcceptanceLedger, operation_id: str,
                                 binding_sha256: str, settings: Settings, verify: Callable[[], None],
                                 transport: httpx.AsyncBaseTransport | None = None
                                 ) -> AsyncIterator[tuple[MeteredAsyncClient, FullTransportObservation]]:
    """Opt-in one operation. Bind production UsageContext before entering.

    ``transport`` is an explicit controlled-test injection; omitted means one
    fixed official HTTPS connection with retries=0. No environment proxy or
    redirect can retarget credentials. This context neither starts nor seals
    operations, so receipt/score persistence remains the runner's responsibility.
    """
    if (type(ledger) is not FullAcceptanceLedger or type(operation_id) is not str
            or not callable(verify) or type(binding_sha256) is not str or not _HASH.fullmatch(binding_sha256)):
        raise FullTransportStopped("full_binding_required")
    install_http_log_redaction()
    observation = FullTransportObservation()
    try:
        bounded = _BoundedTransport(transport or httpx.AsyncHTTPTransport(retries=0, trust_env=False), ledger, operation_id,
                                    binding_sha256, settings.model_copy(deep=True), verify, observation)
    except BaseException:
        try:
            await _persist(lambda: ledger.pause(reason="execution_error"))
        except BaseException:
            pass
        raise
    try:
        verify()
        async with MeteredAsyncClient(transport=bounded, trust_env=False, follow_redirects=False,
                timeout=httpx.Timeout(20, connect=5)) as client:
            yield client, observation
    except BaseException as error:
        # Ordinary provider structure errors are received business failures.
        # Unknown caller/programming errors still fail closed, without a retry.
        if not bounded.received_business_failure(error):
            await bounded._pause()
        raise
