"""Explicit, metered Qwen bridge for the preserved EchoMind algorithms.

The facade accepts the small Anthropic ``messages.create`` shape used by the
reference sources; it is not an Anthropic client or an alternative transport.
No constructor reads credentials/environment, opens a client, or enables a live
route. Trusted code must bind a metered transport, immutable configuration
snapshot and one shared task budget. This module performs no tool execution.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
import math
import re
import time
from typing import Any, Iterator

from jsonschema import Draft202012Validator, SchemaError, ValidationError

from core.config import Settings
from core.llm_utils import RuntimeFailure, bounded_json, strict_json
from core.usage_runtime import MeteredAsyncClient
from tooling.providers.qwen import MODEL, QwenClient, QwenError, configured_endpoint

MAX_INPUT_BYTES = 48_000
MAX_CALL_TOKENS = 2048
MAX_TOOLS = 16
MAX_RESPONSE_CALLS = 4
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,63}\Z")
_CALL_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


class ProviderError(RuntimeFailure):
    """Stable safe codes only; never include upstream bodies or input values."""


@dataclass
class TaskBudget:
    """Shared by intent, child Agents, composer and optional judge.

    Reservations are conservative and never refunded after failure/cancellation.
    A token reservation is the requested *maximum*, not a claimed actual bill.
    Tool runners must reserve_tool immediately before executing each tool; a
    returned tool selection alone is not an executed attempt.
    """

    model_limit: int = 3
    tool_limit: int = 4
    token_limit: int = 6144
    seconds: float = 45.0
    _started: float = field(default_factory=time.monotonic, init=False, repr=False)
    _models: int = field(default=0, init=False)
    _tools: int = field(default=0, init=False)
    _tokens: int = field(default=0, init=False)
    _stopped: bool = field(default=False, init=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        if (type(self.model_limit) is not int or not 1 <= self.model_limit <= 3
                or type(self.tool_limit) is not int or not 1 <= self.tool_limit <= 4
                or type(self.token_limit) is not int or not 1 <= self.token_limit <= 6144
                or type(self.seconds) not in (int, float) or not math.isfinite(self.seconds)
                or not 0 < self.seconds <= 45):
            raise ProviderError("configuration")

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.seconds - (time.monotonic() - self._started))

    @property
    def counts(self) -> tuple[int, int, int]:
        return self._models, self._tools, self._tokens

    async def reserve_model(self, max_tokens: int) -> None:
        async with self._lock:
            if self._stopped:
                raise ProviderError("task_stopped")
            if not self.remaining_seconds:
                raise ProviderError("task_deadline")
            if (type(max_tokens) is not int or not 1 <= max_tokens <= MAX_CALL_TOKENS
                    or self._models >= self.model_limit or self._tokens + max_tokens > self.token_limit):
                raise ProviderError("task_budget")
            self._models += 1
            self._tokens += max_tokens

    async def reserve_tool(self) -> None:
        async with self._lock:
            if self._stopped:
                raise ProviderError("task_stopped")
            if not self.remaining_seconds:
                raise ProviderError("task_deadline")
            if self._tools >= self.tool_limit:
                raise ProviderError("task_budget")
            self._tools += 1

    def stop(self) -> None:
        """Latch new work after a provider failure; in-flight sends stay counted.

        No await is needed: all reservations and this latch run on the same
        asyncio task loop, so cancellation cannot interrupt recording the stop.
        This does not claim that already-dispatched sibling calls were undone.
        """
        self._stopped = True


@dataclass(frozen=True)
class _BoundProvider:
    settings: Settings = field(repr=False)
    client: MeteredAsyncClient = field(repr=False)
    budget: TaskBudget
    closed: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    pending: set[asyncio.Task[Any]] = field(default_factory=set, repr=False)


_BOUND: ContextVar[_BoundProvider | None] = ContextVar("echomind_qwen_provider", default=None)


@contextmanager
def bind_provider(settings: Settings, client: MeteredAsyncClient, budget: TaskBudget) -> Iterator[None]:
    """Trusted request/task scope, separate from persistent usage owner binding.

    Children inherit the same budget, while unrelated contexts cannot acquire
    credentials or another task's provider. The settings are copied so mutation
    by the caller cannot retarget an in-flight task. Exiting revokes the lease
    and cancels pending creates, including any waiting for durable reservation.
    Persistent workers drain before cancellation completes; calls already sent
    remain counted, and cancellation cannot promise upstream billing stopped.
    """
    if not isinstance(client, MeteredAsyncClient) or not isinstance(budget, TaskBudget):
        raise ProviderError("configuration")
    snapshot = settings.model_copy(deep=True)
    if snapshot.qwen_model != MODEL:
        raise ProviderError("configuration")
    try:
        configured_endpoint(snapshot)
    except QwenError:
        raise ProviderError("configuration") from None
    service = _BoundProvider(snapshot, client, budget)
    token = _BOUND.set(service)
    try:
        yield
    finally:
        # ContextVar reset alone does not revoke copies inherited by children.
        service.closed.set()
        for task in tuple(service.pending):
            if not task.done():
                task.cancel()
        _BOUND.reset(token)


@dataclass(frozen=True)
class TextBlock:
    text: str
    type: str = field(default="text", init=False)


@dataclass(frozen=True)
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]
    type: str = field(default="tool_use", init=False)


@dataclass(frozen=True)
class MessageUsage:
    input_tokens: int | None
    output_tokens: int | None


@dataclass(frozen=True)
class MessageResponse:
    content: tuple[TextBlock | ToolUseBlock, ...]
    stop_reason: str
    usage: MessageUsage
    id: str | None = None
    model: str | None = None


def _schema(schema: Any) -> Draft202012Validator:
    if type(schema) is not dict:
        raise ProviderError("tool_schema")
    bounded_json(schema, 8192)
    nodes = 0

    def walk(value: Any, depth: int, refs: frozenset[str]) -> None:
        nonlocal nodes
        nodes += 1
        if depth > 16 or nodes > 512:
            raise ProviderError("tool_schema")
        if isinstance(value, dict):
            reference = value.get("$ref")
            if reference is not None:
                if (not isinstance(reference, str) or not reference.startswith("#/")
                        or reference in refs):
                    raise ProviderError("tool_schema")
                target: Any = schema
                try:
                    for segment in reference[2:].split("/"):
                        target = target[segment.replace("~1", "/").replace("~0", "~")]
                except (KeyError, TypeError):
                    raise ProviderError("tool_schema") from None
                walk(target, depth + 1, refs | {reference})
            # Dynamic references can resolve beyond the locally inspected tree.
            if "$dynamicRef" in value or "$recursiveRef" in value or "$id" in value:
                raise ProviderError("tool_schema")
            for key, child in value.items():
                if key != "$ref":
                    walk(child, depth + 1, refs)
        elif isinstance(value, list):
            for child in value:
                walk(child, depth + 1, refs)
    try:
        walk(schema, 0, frozenset())
        Draft202012Validator.check_schema(schema)
    except (SchemaError, RecursionError, TypeError, ValueError):
        raise ProviderError("tool_schema") from None
    return Draft202012Validator(schema)


def _tools(values: Any) -> tuple[list[dict[str, Any]], dict[str, Draft202012Validator]]:
    if type(values) is not list or len(values) > MAX_TOOLS:
        raise ProviderError("tool_schema")
    output, validators = [], {}
    for tool in values:
        if (type(tool) is not dict or set(tool) != {"name", "description", "input_schema"}
                or not isinstance(tool["name"], str) or _NAME.fullmatch(tool["name"]) is None
                or tool["name"] in validators or not isinstance(tool["description"], str)
                or not 1 <= len(tool["description"]) <= 2000):
            raise ProviderError("tool_schema")
        # Schema/validator and payload use the same independent snapshot, not a
        # caller-owned dict that could change across the reservation await.
        snapshot = strict_json(bounded_json(tool["input_schema"], 8192))
        validators[tool["name"]] = _schema(snapshot)
        output.append({"type": "function", "function": {
            "name": tool["name"], "description": tool["description"], "parameters": snapshot,
        }})
    return output, validators


def _arguments(value: Any, validator: Draft202012Validator) -> None:
    if type(value) is not dict:
        raise ProviderError("tool_arguments")
    bounded_json(value, 8192)
    try:
        validator.validate(value)
    except (ValidationError, RecursionError, TypeError, ValueError):
        raise ProviderError("tool_arguments") from None


def _block(value: Any) -> dict[str, Any]:
    if isinstance(value, (TextBlock, ToolUseBlock)):
        return asdict(value)
    if type(value) is dict:
        return value
    raise ProviderError("messages_structure")


def _messages(values: Any, system: Any, validators: dict[str, Draft202012Validator]) -> list[dict[str, Any]]:
    if type(values) is not list or not 1 <= len(values) <= 64:
        raise ProviderError("messages_structure")
    result: list[dict[str, Any]] = []
    if system is not None:
        if not isinstance(system, str) or not system.strip() or len(system) > 24_000:
            raise ProviderError("messages_structure")
        result.append({"role": "system", "content": system})
    seen: set[str] = set()
    pending: set[str] = set()
    for item in values:
        if (type(item) is not dict or set(item) != {"role", "content"} or not isinstance(item["role"], str)
                or item["role"] not in {"user", "assistant"}):
            raise ProviderError("messages_structure")
        role, content = item["role"], item["content"]
        if isinstance(content, str):
            if pending or not content.strip() or len(content) > 24_000:
                raise ProviderError("messages_structure")
            result.append({"role": role, "content": content})
            continue
        if not isinstance(content, (list, tuple)) or not 1 <= len(content) <= MAX_RESPONSE_CALLS:
            raise ProviderError("messages_structure")
        blocks = [_block(value) for value in content]
        if any(not isinstance(block.get("type"), str) for block in blocks):
            raise ProviderError("messages_structure")
        kinds = {block.get("type") for block in blocks}
        if kinds == {"text"}:
            if pending or any(set(block) != {"type", "text"} or not isinstance(block["text"], str) for block in blocks):
                raise ProviderError("messages_structure")
            result.append({"role": role, "content": "\n".join(block["text"] for block in blocks)})
        elif kinds == {"tool_use"} and role == "assistant" and not pending:
            calls = []
            for block in blocks:
                if (set(block) != {"type", "id", "name", "input"}
                        or not isinstance(block["id"], str) or _CALL_ID.fullmatch(block["id"]) is None
                        or block["id"] in seen or not isinstance(block["name"], str)
                        or block["name"] not in validators):
                    raise ProviderError("messages_structure")
                _arguments(block["input"], validators[block["name"]])
                seen.add(block["id"])
                pending.add(block["id"])
                calls.append({"id": block["id"], "type": "function", "function": {
                    "name": block["name"], "arguments": bounded_json(block["input"], 8192),
                }})
            result.append({"role": "assistant", "content": None, "tool_calls": calls})
        elif kinds == {"tool_result"} and role == "user" and pending:
            for block in blocks:
                if (set(block) - {"type", "tool_use_id", "content", "is_error"}
                        or not {"type", "tool_use_id", "content"} <= set(block)
                        or not isinstance(block["tool_use_id"], str) or block["tool_use_id"] not in pending
                        or not isinstance(block["content"], str)
                        or "is_error" in block and type(block["is_error"]) is not bool):
                    raise ProviderError("messages_structure")
                pending.remove(block["tool_use_id"])
                result.append({"role": "tool", "tool_call_id": block["tool_use_id"], "content": block["content"]})
            if pending:
                raise ProviderError("messages_structure")
        else:
            raise ProviderError("messages_structure")
    if pending or result[-1]["role"] == "assistant":
        raise ProviderError("messages_structure")
    return result


def _response(data: Any, validators: dict[str, Draft202012Validator], max_tokens: int) -> MessageResponse:
    try:
        if type(data) is not dict or data.get("error"):
            raise ProviderError("model_structure")
        choices = data.get("choices")
        if type(choices) is not list or len(choices) != 1 or type(choices[0]) is not dict:
            raise ProviderError("model_structure")
        choice, message = choices[0], choices[0].get("message")
        if choice.get("finish_reason") == "length":
            raise ProviderError("truncated")
        if choice.get("finish_reason") == "content_filter" or isinstance(message, dict) and message.get("refusal"):
            raise ProviderError("refused")
        if (type(message) is not dict or message.get("role") != "assistant"
                or set(message) - {"role", "content", "tool_calls", "refusal", "reasoning_content"}
                or message.get("reasoning_content")):
            raise ProviderError("model_structure")
        usage = data.get("usage")
        def token(name: str) -> int | None:
            value = usage.get(name) if type(usage) is dict else None
            if value is not None and (type(value) is not int or not 0 <= value <= 1_000_000):
                raise ProviderError("model_structure")
            return value
        output = token("completion_tokens")
        if output is not None and output > max_tokens:
            raise ProviderError("truncated")
        identifier, model = data.get("id"), data.get("model")
        if identifier is not None and (not isinstance(identifier, str) or _CALL_ID.fullmatch(identifier) is None):
            raise ProviderError("model_structure")
        if model is not None and (not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", model)):
            raise ProviderError("model_structure")
        blocks: list[TextBlock | ToolUseBlock] = []
        calls = message.get("tool_calls")
        if choice.get("finish_reason") == "tool_calls":
            if message.get("content") not in (None, "") or type(calls) is not list or not 1 <= len(calls) <= MAX_RESPONSE_CALLS:
                raise ProviderError("model_structure")
            seen: set[str] = set()
            for index, call in enumerate(calls):
                if (type(call) is not dict or set(call) - {"id", "type", "function", "index"}
                        or not {"id", "type", "function"} <= set(call) or call["type"] != "function"
                        or "index" in call and (type(call["index"]) is not int or call["index"] != index)
                        or not isinstance(call["id"], str) or _CALL_ID.fullmatch(call["id"]) is None
                        or call["id"] in seen):
                    raise ProviderError("model_structure")
                function = call["function"]
                if (type(function) is not dict or set(function) != {"name", "arguments"}
                        or not isinstance(function["name"], str) or function["name"] not in validators
                        or not isinstance(function["arguments"], str) or len(function["arguments"].encode()) > 8192):
                    raise ProviderError("tool_arguments")
                arguments = strict_json(function["arguments"])
                _arguments(arguments, validators[function["name"]])
                seen.add(call["id"])
                blocks.append(ToolUseBlock(call["id"], function["name"], arguments))
            reason = "tool_use"
        else:
            content = message.get("content")
            if (choice.get("finish_reason") != "stop" or calls or not isinstance(content, str)
                    or not content.strip() or len(content.encode()) > 16_384):
                raise ProviderError("model_structure")
            blocks.append(TextBlock(content))
            reason = "end_turn"
        return MessageResponse(tuple(blocks), reason, MessageUsage(token("prompt_tokens"), output), identifier, model)
    except (KeyError, ValueError, TypeError, UnicodeError, RecursionError):
        raise ProviderError("model_structure") from None


class _Messages:
    def __init__(self) -> None:
        self.closed = False

    async def create(self, *, model: str, max_tokens: int, messages: list[dict[str, Any]],
                     temperature: float = 0.2, system: str | None = None,
                     tools: list[dict[str, Any]] | None = None) -> MessageResponse:
        if self.closed:
            raise ProviderError("provider_closed")
        service = _BOUND.get()
        if service is None or service.closed.is_set():
            raise ProviderError("provider_unbound")
        if (model != MODEL or type(max_tokens) is not int or not 1 <= max_tokens <= MAX_CALL_TOKENS
                or type(temperature) not in (int, float) or not math.isfinite(temperature)
                or not 0 <= temperature <= 2):
            raise ProviderError("configuration")
        native_tools, validators = _tools([] if tools is None else tools)
        payload: dict[str, Any] = {
            "model": MODEL, "max_tokens": max_tokens, "temperature": temperature,
            "stream": False, "enable_thinking": False,
            "messages": _messages(messages, system, validators),
        }
        if native_tools:
            payload.update({"tools": native_tools, "tool_choice": "auto", "parallel_tool_calls": False})
        bounded_json(payload, MAX_INPUT_BYTES)
        # Register before the first await. Scope exit must also cancel a child
        # paused inside persistent usage reservation, not just suppress its
        # eventual result after an avoidable paid dispatch.
        task = asyncio.current_task()
        if task is None or service.closed.is_set():
            raise ProviderError("provider_unbound")
        service.pending.add(task)
        try:
            await service.budget.reserve_model(max_tokens)
            async with asyncio.timeout(service.budget.remaining_seconds):
                data = await QwenClient(service.settings, service.client)._request(payload)
            if service.closed.is_set():
                raise ProviderError("provider_unbound")
            return _response(data, validators, max_tokens)
        except TimeoutError:
            service.budget.stop()
            raise ProviderError("task_deadline") from None
        except QwenError as error:
            service.budget.stop()
            raise ProviderError(error.code) from None
        except ProviderError as error:
            # A denied reservation has not attempted provider work. Other
            # pending children may use their already granted reservations; do
            # not turn all subsequent budget denials into a provider failure.
            if error.code not in {"task_budget", "task_stopped"}:
                service.budget.stop()
            raise
        except BaseException:
            # Preserved upstream algorithms may try a fallback or Judge repair.
            # They must not obtain another send after a failed/uncertain call.
            service.budget.stop()
            raise
        finally:
            service.pending.discard(task)


class QwenMessagesClient:
    """Construction-compatible facade, intentionally incapable until bound."""

    def __init__(self, *, api_key: str = "", base_url: str | None = None, max_retries: int = 0) -> None:
        if api_key != "" or base_url is not None or type(max_retries) is not int or max_retries != 0:
            raise ProviderError("configuration")
        self.messages = _Messages()

    async def close(self) -> None:
        """Close this facade only; the trusted caller owns the shared transport."""
        self.messages.closed = True
