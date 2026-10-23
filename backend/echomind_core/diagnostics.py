"""Candidate-only logging boundary: no prompts, arguments or raw exceptions.

The preserved upstream intentionally keeps its original logging statements.
Those statements are unsafe for application logs, so the opt-in runtime routes
that namespace to a non-propagating null sink. The active application loggers
are not changed. Safe events use a separate, fixed-field channel.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import math

UPSTREAM_LOGGER = "echomind_core.upstream"
EVENT_LOGGER = "echomind_core.safe_events"
_STAGES = frozenset({"intent", "tool", "task", "judge", "retrieval", "storage", "embedding"})
_CODES = frozenset({
    "started", "complete", "partial", "failed", "cancelled", "unscored", "unknown",
    "configuration", "context_changed", "task_budget", "task_deadline", "task_stopped",
    "tool_registry", "tool_arguments", "tool_failed", "tool_result", "judge_output",
    "provider_failed", "execution_failed", "provider_unbound", "provider_closed",
})
_MODELS = frozenset({"qwen3.7-flash-2026-07-15", "text-embedding-v4", "unknown"})


def quarantine_upstream_logging() -> None:
    """Suppress only preserved candidate logs, including existing child handlers.

    This is intentionally permanent for this opt-in namespace, avoiding unsafe
    overlapping context-manager restore races. Existing/future child loggers
    normally propagate to this null parent. No root/active-app filter is added.
    """
    parent = logging.getLogger(UPSTREAM_LOGGER)
    parent.handlers = [logging.NullHandler()]
    parent.propagate = False
    # Imported upstream modules do not install handlers. Still close the bypass
    # for trusted embedding/test callers that configured one before composition.
    for name, logger in tuple(logging.Logger.manager.loggerDict.items()):
        if name.startswith(UPSTREAM_LOGGER + ".") and isinstance(logger, logging.Logger):
            logger.handlers = []
            logger.propagate = True


@dataclass(frozen=True)
class SafeEvent:
    stage: str
    code: str
    count: int = 0
    elapsed_ms: float = 0.0
    model: str = "unknown"

    def fields(self) -> dict[str, str | int | float]:
        # Invalid values cannot become arbitrary safe-looking log messages.
        return {
            "stage": self.stage if type(self.stage) is str and self.stage in _STAGES else "task",
            "code": self.code if type(self.code) is str and self.code in _CODES else "unknown",
            "count": self.count if type(self.count) is int and 0 <= self.count <= 1_000_000 else 0,
            "elapsed_ms": round(self.elapsed_ms, 1)
            if type(self.elapsed_ms) in (int, float) and math.isfinite(self.elapsed_ms)
            and 0 <= self.elapsed_ms <= 3_600_000 else 0.0,
            "model": self.model if type(self.model) is str and self.model in _MODELS else "unknown",
        }


def emit(event: SafeEvent) -> None:
    """Only the dataclass whitelist is accepted, never a model-derived dict."""
    if type(event) is not SafeEvent:
        raise TypeError("safe_event_required")
    logging.getLogger(EVENT_LOGGER).info(json.dumps(event.fields(), separators=(",", ":")))
