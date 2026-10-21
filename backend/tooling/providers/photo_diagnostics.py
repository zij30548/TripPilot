"""Bounded photo rejection metadata. Never log values, exception text or inputs."""
import json
import logging

from pydantic import ValidationError

from tooling.providers.qwen import strict_json

logger = logging.getLogger(__name__)
MAX_ISSUES = 8
MAX_COUNT = 1000
_REASONS = {"json_parse", "field_type", "extra_field", "length", "missing_field",
            "enum", "state_consistency", "envelope", "validation"}
_TYPES = {
    "string_type": "field_type", "list_type": "field_type", "model_type": "field_type",
    "model_attributes_type": "field_type", "dict_type": "field_type",
    "extra_forbidden": "extra_field", "missing": "missing_field", "literal_error": "enum",
    "string_too_long": "length", "string_too_short": "length", "too_long": "length", "too_short": "length",
    # The root postcondition binds status to the exact permitted clue count.
    "value_error": "state_consistency",
}


def safe_path(location: tuple[object, ...]) -> str:
    """Only grammar-owned names/indices; even an unknown KEY may contain secrets."""
    if not location:
        return "$"
    if location == ("status",):
        return "$.status"
    if location[0] != "clues":
        return "$.<unknown>"
    path = "$.clues"
    if len(location) == 1:
        return path
    index = location[1]
    if type(index) is not int:
        return path + ".<unknown>"
    path += "[0]" if index == 0 else "[*]"
    if len(location) == 2:
        return path
    field = location[2]
    if len(location) == 3 and field in ("name", "city"):
        return path + "." + field
    return path + ".<unknown>"


def record_structure(stage: str, reason: str, location: tuple[object, ...] = ()) -> None:
    # Defense in depth: callers cannot turn this into an arbitrary-text logger.
    _emit(stage, [(location, reason)], 1)


def _emit(stage: str, issues: list[tuple[tuple[object, ...], str]], count: int) -> None:
    details = {
        "stage": stage if stage in {"envelope", "content", "selection"} else "unknown",
        "error_count_capped": min(max(count, 0), MAX_COUNT),
        "issues": [{"path": safe_path(path), "type": reason if reason in _REASONS else "validation"}
                   for path, reason in issues[:MAX_ISSUES]],
    }
    logger.warning("photo_structure_rejected %s", json.dumps(details, separators=(",", ":")))


def record_validation(error: ValidationError) -> None:
    # No raw msg, ctx, input, URL or exception repr is passed to the logger.
    errors = error.errors(include_input=False, include_context=False, include_url=False)
    _emit("selection", [(tuple(item["loc"]), _TYPES.get(item["type"], "validation"))
                        for item in errors[:MAX_ISSUES]], error.error_count())


def record_content_rejection(data: object) -> None:
    """Classify the existing parser's rejection, without changing its decision.

    The shared parser intentionally collapses malformed content into structure.
    Reparse only on failure for a safe category, never repair or accept it.
    """
    if isinstance(data, dict):
        choices = data.get("choices")
        if isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict):
            message = choices[0].get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if not isinstance(content, str):
                    record_structure("content", "field_type")
                    return
                try:
                    if len(content.encode()) > 8192:
                        record_structure("content", "length")
                        return
                    strict_json(content)
                except (ValueError, UnicodeError, RecursionError):
                    record_structure("content", "json_parse")
                    return
    record_structure("envelope", "envelope")
