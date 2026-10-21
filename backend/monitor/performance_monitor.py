"""Aggregate observations inspired by EchoMind's AgentStats/Monitor.

Counters describe technical execution, not semantic truth or user satisfaction.
Request text, tool arguments, outputs and account IDs are never stored here.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import time


@dataclass(frozen=True)
class Observation:
    successful: bool
    elapsed_ms: float
    model_calls: int
    tool_calls: int
    recorded_at: float


class PerformanceMonitor:
    def __init__(self, max_samples: int = 200) -> None:
        if not 1 <= max_samples <= 1000:
            raise ValueError("invalid_monitor_limit")
        self._samples: deque[Observation] = deque(maxlen=max_samples)

    def record(self, successful: bool, elapsed_ms: float, model_calls: int, tool_calls: int) -> None:
        self._samples.append(Observation(successful, elapsed_ms, model_calls, tool_calls, time.monotonic()))

    def summary(self) -> dict[str, int | float | str]:
        values = list(self._samples)
        return {"scope": "process_local_technical_execution", "samples": len(values),
                "successful": sum(item.successful for item in values),
                "mean_elapsed_ms": round(sum(item.elapsed_ms for item in values) / len(values), 1) if values else 0,
                "model_calls": sum(item.model_calls for item in values),
                "tool_calls": sum(item.tool_calls for item in values)}
