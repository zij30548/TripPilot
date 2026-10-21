"""Strict native Qwen response values; no provider text is executed as code.

EchoMind's text/tool separation is retained, using Qwen's native protocol rather
than copying its Anthropic client or silently translating tool instructions.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import date
from typing import Any


class RuntimeFailure(Exception):
    """A safe reason code. Provider text, arguments and secrets are excluded."""

    def __init__(self, code: str) -> None:
        self.code = code
        self.trace: dict[str, Any] = {}
        super().__init__(code)


def strict_json(raw: str | bytes) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_key")
            result[key] = value
        return result

    def finite(_: str) -> None:
        raise ValueError("nonfinite")

    return json.loads(raw, object_pairs_hook=unique, parse_constant=finite)


def bounded_json(value: Any, limit: int) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode()) > limit:
            raise ValueError("too_large")
        return encoded
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise RuntimeFailure("context_limit") from None


def validate_arguments(value: Any, schema: dict[str, Any], depth: int = 0) -> None:
    """Validate the finite JSON Schema subset used by registered travel tools.

    Unsupported schema keywords fail closed. This does not claim to implement
    arbitrary JSON Schema or accept user-supplied schemas.
    """
    if depth > 12 or not isinstance(schema, dict):
        raise RuntimeFailure("tool_schema")
    allowed = {"type", "properties", "required", "additionalProperties", "items", "enum", "const",
               "minLength", "maxLength", "minimum", "maximum", "minItems", "maxItems", "pattern",
               "description", "title", "format", "anyOf"}
    if set(schema) - allowed:
        raise RuntimeFailure("tool_schema")
    if "anyOf" in schema:
        choices = schema["anyOf"]
        if not isinstance(choices, list) or not 1 <= len(choices) <= 8:
            raise RuntimeFailure("tool_schema")
        for choice in choices:
            try:
                validate_arguments(value, choice, depth + 1)
                return
            except RuntimeFailure as error:
                if error.code == "tool_schema":
                    raise
        raise RuntimeFailure("tool_arguments")
    kind = schema.get("type")
    valid_type = {
        "object": lambda: type(value) is dict,
        "array": lambda: type(value) is list,
        "string": lambda: type(value) is str,
        "integer": lambda: type(value) is int,
        "number": lambda: type(value) in (int, float) and math.isfinite(value),
        "boolean": lambda: type(value) is bool,
        "null": lambda: value is None,
    }
    if kind not in valid_type:
        raise RuntimeFailure("tool_schema")
    if not valid_type[kind]():
        raise RuntimeFailure("tool_arguments")
    if "enum" in schema and not any(type(value) is type(item) and value == item for item in schema["enum"]):
        raise RuntimeFailure("tool_arguments")
    if "const" in schema and (type(value) is not type(schema["const"]) or value != schema["const"]):
        raise RuntimeFailure("tool_arguments")
    if kind == "object":
        properties, required = schema.get("properties", {}), schema.get("required", [])
        if (schema.get("additionalProperties") is not False or not isinstance(properties, dict)
                or not isinstance(required, list) or any(key not in properties for key in required)):
            raise RuntimeFailure("tool_schema")
        if set(value) - set(properties) or any(key not in value for key in required) or len(value) > 32:
            raise RuntimeFailure("tool_arguments")
        for key, item in value.items():
            validate_arguments(item, properties[key], depth + 1)
    elif kind == "array":
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 32):
            raise RuntimeFailure("tool_arguments")
        for item in value:
            validate_arguments(item, schema.get("items", {}), depth + 1)
    elif kind == "string":
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 4000):
            raise RuntimeFailure("tool_arguments")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise RuntimeFailure("tool_arguments")
        if "format" in schema:
            if schema["format"] != "date":
                raise RuntimeFailure("tool_schema")
            try:
                if date.fromisoformat(value).isoformat() != value:
                    raise ValueError()
            except ValueError:
                raise RuntimeFailure("tool_arguments") from None
    elif kind in {"integer", "number"}:
        if value < schema.get("minimum", -9_007_199_254_740_991) or value > schema.get("maximum", 9_007_199_254_740_991):
            raise RuntimeFailure("tool_arguments")


@dataclass(frozen=True)
class NativeToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ProviderResponse:
    content: str | None = None
    tool_call: NativeToolCall | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    request_id: str | None = None
    model: str | None = None


def parse_native_response(data: Any, max_tokens: int = 2048) -> ProviderResponse:
    """Reject mixed content, multiple calls, truncation and malformed JSON."""
    try:
        if not isinstance(data, dict) or data.get("error"):
            raise ValueError()
        choices = data.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise ValueError()
        choice, message = choices[0], choices[0].get("message")
        if choice.get("finish_reason") == "length":
            raise RuntimeFailure("truncated")
        if choice.get("finish_reason") == "content_filter" or isinstance(message, dict) and message.get("refusal"):
            raise RuntimeFailure("refused")
        if (not isinstance(message, dict) or message.get("role") != "assistant"
                or message.get("reasoning_content") or message.get("function_call")):
            raise ValueError()
        usage = data.get("usage", {})
        def token(key: str) -> int | None:
            value = usage.get(key) if isinstance(usage, dict) else None
            if value is not None and (type(value) is not int or not 0 <= value <= 1_000_000):
                raise ValueError()
            return value
        output = token("completion_tokens")
        if output is not None and output > max_tokens:
            raise RuntimeFailure("truncated")
        metadata = {"input_tokens": token("prompt_tokens"), "output_tokens": output,
                    "request_id": data.get("id") if isinstance(data.get("id"), str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", data["id"]) else None,
                    "model": data.get("model") if isinstance(data.get("model"), str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", data["model"]) else None}
        calls = message.get("tool_calls")
        if choice.get("finish_reason") == "tool_calls":
            if message.get("content") not in (None, "") or not isinstance(calls, list) or len(calls) != 1:
                raise ValueError()
            call = calls[0]
            if (not isinstance(call, dict) or set(call) not in ({"id", "type", "function"}, {"id", "type", "function", "index"})
                    or call["type"] != "function" or "index" in call and (type(call["index"]) is not int or call["index"] != 0)):
                raise ValueError()
            function = call["function"]
            if (not isinstance(call["id"], str) or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", call["id"]) is None
                    or not isinstance(function, dict) or set(function) != {"name", "arguments"}
                    or not isinstance(function["name"], str) or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", function["name"]) is None
                    or not isinstance(function["arguments"], str) or len(function["arguments"].encode()) > 8192):
                raise ValueError()
            arguments = strict_json(function["arguments"])
            if not isinstance(arguments, dict):
                raise ValueError()
            return ProviderResponse(tool_call=NativeToolCall(call["id"], function["name"], arguments), **metadata)
        content = message.get("content")
        if (choice.get("finish_reason") != "stop" or calls or not isinstance(content, str)
                or not content.strip() or len(content.encode()) > 16_384):
            raise ValueError()
        return ProviderResponse(content=content.strip(), **metadata)
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise RuntimeFailure("model_structure") from None
