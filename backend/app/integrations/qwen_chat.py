"""Chat intent response profile and value-free diagnostics; one bounded send."""
import asyncio

import httpx
from pydantic import ValidationError

from app.config import Settings
from app.integrations.qwen import QwenClient, QwenError, parse_content, strict_json
from app.schemas.chat import Intent
from app.schemas.explanation import ExplanationUsage

OUTPUT_PROFILE_VERSION = "chat-intent-output-v1-2048"
MAX_INTENT_TOKENS = 2048
MAX_INTENT_CONTENT_BYTES = 16_384
DIAGNOSTIC_VERSION = "chat-intent-diagnostic-v1"
_FINISH = {"stop", "length", "tool_calls", "content_filter"}
_ERROR_TYPES = {
    "string_type": "field_type", "list_type": "field_type", "int_type": "field_type",
    "model_type": "field_type", "dict_type": "field_type", "extra_forbidden": "extra_field",
    "missing": "missing_field", "literal_error": "enum", "string_too_long": "length",
    "string_too_short": "length", "too_long": "length", "too_short": "length",
    "string_pattern_mismatch": "pattern", "value_error": "value",
    "greater_than_equal": "range", "less_than_equal": "range",
}


def validation_diagnostic(error: ValidationError) -> dict:
    """Never log msg/ctx/input/URL or an arbitrary field name, even a JSON key."""
    def path(location: tuple) -> str:
        if not location:
            return "$"
        root = location[0]
        if root == "requirements":
            if len(location) > 1 and location[1] in Intent.model_fields:
                return "$.requirements." + location[1] + ("[*]" if len(location) > 2 else "")
            return "$.requirements.<unknown>" if len(location) > 1 else "$.requirements"
        if root == "sources":
            suffix = "." + location[2] if len(location) == 3 and location[2] in {"field", "quote"} else ""
            return "$.sources" + ("[*]" + suffix if len(location) > 1 else "")
        return "$.issues" if root == "issues" else "$.<unknown>"
    errors = error.errors(include_input=False, include_context=False, include_url=False)
    return {"error_count_capped": min(error.error_count(), 1000), "issues": [
        {"path": path(tuple(item["loc"])), "type": _ERROR_TYPES.get(item["type"], "validation")}
        for item in errors[:8]
    ]}


def response_diagnostic(raw: object) -> dict:
    """Only bounded counters/enums for diagnosing failures; no response values."""
    result: dict = {"version": DIAGNOSTIC_VERSION, "output_profile": OUTPUT_PROFILE_VERSION,
                    "finish_reason": "unknown",
                    "content_bytes_capped": None, "json_state": "unavailable",
                    "input_tokens": None, "output_tokens": None}
    if not isinstance(raw, dict):
        return result
    usage = raw.get("usage")
    if isinstance(usage, dict):
        for target, key in (("input_tokens", "prompt_tokens"), ("output_tokens", "completion_tokens")):
            value = usage.get(key)
            if type(value) is int and 0 <= value <= 1_000_000:
                result[target] = value
    choices = raw.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        return result
    finish = choices[0].get("finish_reason")
    if isinstance(finish, str) and finish in _FINISH:
        result["finish_reason"] = finish
    message = choices[0].get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        return result
    content = message["content"]
    try:
        size = len(content.encode())
        result["content_bytes_capped"] = min(size, MAX_INTENT_CONTENT_BYTES + 1)
        if size > MAX_INTENT_CONTENT_BYTES:
            result["json_state"] = "not_checked_size_limit"
            return result
        parsed = strict_json(content)
    except (ValueError, UnicodeError, RecursionError):
        result["json_state"] = "invalid"
        return result
    result["json_state"] = "valid"
    if not isinstance(parsed, dict):
        return result
    requirements = parsed.get("requirements")
    if isinstance(requirements, dict):
        shapes = {}
        for field in Intent.model_fields:
            value = requirements.get(field)
            if isinstance(value, str):
                shapes[field] = {"characters_capped": min(len(value), 2001)}
            elif isinstance(value, list):
                shapes[field] = {"items_capped": min(len(value), 21),
                                 "max_characters_capped": min(max((len(v) for v in value[:21]
                                                                     if isinstance(v, str)), default=0), 2001)}
        result["field_sizes"] = shapes
    sources = parsed.get("sources")
    if isinstance(sources, list):
        result["sources_count_capped"] = min(len(sources), 10)
        result["max_quote_characters_capped"] = min(max((len(v["quote"]) for v in sources[:10]
            if isinstance(v, dict) and isinstance(v.get("quote"), str)), default=0), 301)
    return result


class QwenIntentClient(QwenClient):
    """Reuse the fixed Beijing transport; only intent has this response budget."""
    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        super().__init__(settings, client)
        # The caller's finally must be safe even if cancellation arrives before
        # generate enters its body, or validation rejects a request pre-send.
        self.diagnostic: dict = {"version": DIAGNOSTIC_VERSION, "output_profile": OUTPUT_PROFILE_VERSION,
                                 "outcome": "not_sent"}

    async def generate(self, payload: dict[str, object]) -> tuple[object, ExplanationUsage]:
        self.diagnostic = {"version": DIAGNOSTIC_VERSION, "output_profile": OUTPUT_PROFILE_VERSION,
                           "outcome": "not_sent"}
        if type(payload.get("max_tokens")) is not int or payload["max_tokens"] != MAX_INTENT_TOKENS:
            raise QwenError("configuration")
        self.diagnostic["outcome"] = "transport_pending"
        try:
            raw = await self._request(payload)
            self.diagnostic = response_diagnostic(raw)
            content, usage = parse_content(raw, max_output_tokens=MAX_INTENT_TOKENS,
                                          max_content_bytes=MAX_INTENT_CONTENT_BYTES)
            self.diagnostic["outcome"] = "content_parsed"
            return content, usage
        except QwenError as error:
            self.diagnostic["outcome"] = error.code
            raise
        except (ValueError, UnicodeError, RecursionError):
            self.diagnostic["outcome"] = "structure"
            raise QwenError("structure") from None
        except asyncio.CancelledError:
            self.diagnostic["outcome"] = "cancelled"
            raise
