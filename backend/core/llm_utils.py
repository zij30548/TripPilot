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
        # Only validate_arguments populates this bounded, schema-derived
        # diagnostic. __str__, public code and existing trace stay compatible.
        self.details: dict[str, Any] = {}
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


def _validate_arguments(value: Any, schema: dict[str, Any], depth: int = 0) -> None:
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
                _validate_arguments(value, choice, depth + 1)
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
            _validate_arguments(item, properties[key], depth + 1)
    elif kind == "array":
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 32):
            raise RuntimeFailure("tool_arguments")
        for item in value:
            _validate_arguments(item, schema.get("items", {}), depth + 1)
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


ARGUMENT_DIAGNOSTIC_VERSION = "tool-argument-diagnostics-v1"
_DIAGNOSTIC_FIELD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")
_DIAGNOSTIC_REASONS = frozenset({"type_mismatch", "enum_mismatch", "const_mismatch", "additional_fields",
    "missing_required", "object_size", "array_length", "string_length", "pattern_mismatch",
    "date_format", "number_range", "invalid_arguments"})


def _argument_diagnostics(value: Any, schema: dict[str, Any], depth: int) -> dict[str, Any]:
    """Describe rejection without copying an input value or an unknown key.

    This is a separate bounded observer, not a replacement validator. It may
    give a less specific diagnostic when capacity is exhausted; it can never
    turn a rejection into acceptance. Paths only originate from registered
    schema properties (safe identifiers), plus bounded array positions.
    """
    remaining, truncated = [256], [False]

    def type_name(item: Any) -> str:
        return {dict: "object", list: "array", str: "string", int: "integer", float: "number",
                bool: "boolean", type(None): "null"}.get(type(item), "other")

    def diagnostic(path: tuple, reason: str, **counts: Any) -> dict:
        row: dict[str, Any] = {"path": list(path), "reason": reason}
        for key in ("count", "limit"):
            count = counts.get(key)
            if type(count) is int and count >= 0:
                row[key] = min(count, 1_000_000)
        for key in ("expected_type", "actual_type"):
            if counts.get(key) in {"object", "array", "string", "integer", "number", "boolean", "null", "other"}:
                row[key] = counts[key]
        return row

    def child(path: tuple, key: Any) -> tuple:
        if len(path) >= 12 or type(key) is str and _DIAGNOSTIC_FIELD.fullmatch(key) is None:
            truncated[0] = True
            return path
        if type(key) is int and 0 <= key <= 1_000_000 or type(key) is str:
            return path + (key,)
        truncated[0] = True
        return path

    def affinity(item: Any, spec: dict) -> int:
        """Prefer the server's matching discriminator, never print its value."""
        score = int(spec.get("type") == type_name(item))
        if type(item) is dict and type(spec.get("properties")) is dict:
            for key, rule in spec["properties"].items():
                if key not in item or type(rule) is not dict:
                    continue
                if "const" in rule:
                    score += 2 if type(item[key]) is type(rule["const"]) and item[key] == rule["const"] else -2
                elif type(rule.get("enum")) is list:
                    score += 2 if any(type(item[key]) is type(option) and item[key] == option for option in rule["enum"]) else -2
        return score

    def walk(item: Any, spec: Any, level: int, path: tuple) -> list[dict]:
        remaining[0] -= 1
        if remaining[0] < 0 or level > 12 or type(spec) is not dict:
            truncated[0] = True
            return [diagnostic(path, "invalid_arguments")]
        if "anyOf" in spec:
            choices = spec["anyOf"]
            if type(choices) is not list or not 1 <= len(choices) <= 8:
                return [diagnostic(path, "invalid_arguments")]
            failures = []
            for index, choice in enumerate(choices):
                errors = walk(item, choice, level + 1, path)
                if not errors:
                    return []
                failures.append((affinity(item, choice) if type(choice) is dict else -100,
                    max(len(row["path"]) for row in errors), -index, errors))
            return max(failures, key=lambda row: row[:3])[3]
        kind, actual = spec.get("type"), type_name(item)
        matches = (actual == kind and (kind != "number" or math.isfinite(item))
                   or kind == "number" and actual == "integer" and math.isfinite(item))
        if not matches:
            return [diagnostic(path, "type_mismatch", expected_type=kind, actual_type=actual)]
        if "enum" in spec and not any(type(item) is type(option) and item == option for option in spec["enum"]):
            return [diagnostic(path, "enum_mismatch")]
        if "const" in spec and (type(item) is not type(spec["const"]) or item != spec["const"]):
            return [diagnostic(path, "const_mismatch")]
        if kind == "object":
            properties, required = spec.get("properties", {}), spec.get("required", [])
            errors = []
            extra_count = len(set(item) - set(properties))
            if extra_count:
                errors.append(diagnostic(path, "additional_fields", count=extra_count))
            missing = [key for key in required if key not in item]
            for key in missing[:4 - len(errors)]:
                errors.append(diagnostic(child(path, key), "missing_required"))
            if len(missing) > 4 - int(bool(extra_count)):
                truncated[0] = True
            if len(item) > 32 and len(errors) < 4:
                errors.append(diagnostic(path, "object_size", count=len(item), limit=32))
            if errors:
                return errors[:4]
            for key, nested in item.items():
                errors.extend(walk(nested, properties[key], level + 1, child(path, key)))
                if len(errors) >= 4:
                    truncated[0] = True
                    return errors[:4]
            return errors
        if kind == "array":
            if not spec.get("minItems", 0) <= len(item) <= spec.get("maxItems", 32):
                return [diagnostic(path, "array_length", count=len(item))]
            errors = []
            for index, nested in enumerate(item):
                errors.extend(walk(nested, spec.get("items", {}), level + 1, child(path, index)))
                if len(errors) >= 4:
                    truncated[0] = True
                    return errors[:4]
            return errors
        if kind == "string":
            if not spec.get("minLength", 0) <= len(item) <= spec.get("maxLength", 4000):
                return [diagnostic(path, "string_length", count=len(item))]
            if "pattern" in spec and re.search(spec["pattern"], item) is None:
                return [diagnostic(path, "pattern_mismatch")]
            if "format" in spec:
                try:
                    if spec["format"] != "date" or date.fromisoformat(item).isoformat() != item:
                        raise ValueError()
                except ValueError:
                    return [diagnostic(path, "date_format")]
        if kind in {"integer", "number"} and (
                item < spec.get("minimum", -9_007_199_254_740_991) or item > spec.get("maximum", 9_007_199_254_740_991)):
            return [diagnostic(path, "number_range")]
        return []

    try:
        errors = walk(value, schema, depth, ())[:4]
        if not errors or any(row.get("reason") not in _DIAGNOSTIC_REASONS for row in errors):
            errors = [{"path": [], "reason": "invalid_arguments"}]
        return {"schema_version": ARGUMENT_DIAGNOSTIC_VERSION, "errors": errors, "truncated": truncated[0]}
    except Exception:
        # Diagnostics may never replace the original validation failure or leak
        # schema/input exception representations. No supplied data is copied.
        return {"schema_version": ARGUMENT_DIAGNOSTIC_VERSION,
                "errors": [{"path": [], "reason": "invalid_arguments"}], "truncated": True}


def validate_arguments(value: Any, schema: dict[str, Any], depth: int = 0) -> None:
    """Original strict validation plus optional bounded rejection diagnostics.

    Acceptance, public error codes and caller signature remain unchanged.
    Diagnostics are server-local metadata, never a repaired model argument.
    """
    try:
        _validate_arguments(value, schema, depth)
    except RuntimeFailure as error:
        if error.code == "tool_arguments":
            error.details = _argument_diagnostics(value, schema, depth)
            # Provenance for the HTTP/checkpoint projection: arbitrary callers
            # assigning an untrusted .details dict do not gain a log channel.
            error._argument_diagnostic_json = json.dumps(error.details, sort_keys=True,
                separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
        raise


def tool_argument_diagnostics(error: BaseException) -> dict[str, Any] | None:
    """Project only intact validator-authored diagnostics, never arbitrary details.

    This is not authentication against trusted Python code. The private sealed
    snapshot establishes which fields came from this validator's server schema;
    the second whitelist prevents later mutations becoming HTTP/log payloads.
    """
    if not isinstance(error, RuntimeFailure) or error.code != "tool_arguments":
        return None
    raw = getattr(error, "_argument_diagnostic_json", None)
    if type(raw) is not bytes or not 1 <= len(raw) <= 4096 or type(error.details) is not dict:
        return None
    try:
        if json.dumps(error.details, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode() != raw:
            return None
        value = strict_json(raw)
        if (type(value) is not dict or set(value) != {"schema_version", "errors", "truncated"}
                or value["schema_version"] != ARGUMENT_DIAGNOSTIC_VERSION or type(value["truncated"]) is not bool
                or type(value["errors"]) is not list or not 1 <= len(value["errors"]) <= 4):
            return None
        types = {"object", "array", "string", "integer", "number", "boolean", "null", "other"}
        for row in value["errors"]:
            if (type(row) is not dict or not {"path", "reason"} <= set(row)
                    or set(row) - {"path", "reason", "count", "limit", "expected_type", "actual_type"}
                    or type(row["reason"]) is not str or row["reason"] not in _DIAGNOSTIC_REASONS
                    or type(row["path"]) is not list or len(row["path"]) > 12):
                return None
            for part in row["path"]:
                if not (type(part) is str and _DIAGNOSTIC_FIELD.fullmatch(part)
                        or type(part) is int and 0 <= part <= 1_000_000):
                    return None
            if any(type(row[key]) is not int or not 0 <= row[key] <= 1_000_000 for key in ("count", "limit") if key in row):
                return None
            if any(type(row[key]) is not str or row[key] not in types for key in ("expected_type", "actual_type") if key in row):
                return None
        return value
    except (TypeError, ValueError, UnicodeError, RecursionError):
        return None


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
