"""One shared execution budget, not a fresh allowance for every role.

EchoMind's bounded loop is tightened to task-wide limits and no automatic
fallback/retry. Durable billing attempts remain in TripPilot's metered transport.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from core.llm_utils import RuntimeFailure


@dataclass(frozen=True)
class ExecutionBudget:
    max_model_calls: int = 3
    max_tool_calls: int = 4
    deadline_seconds: float = 45.0
    max_tokens_per_call: int = 2048
    max_total_output_tokens: int = 6144

    def __post_init__(self) -> None:
        if (not 1 <= self.max_model_calls <= 3 or not 1 <= self.max_tool_calls <= 4
                or not 0 < self.deadline_seconds <= 45 or not 1 <= self.max_tokens_per_call <= 2048
                or not 1 <= self.max_total_output_tokens <= 6144):
            raise ValueError("invalid_execution_budget")


class ExecutionState:
    def __init__(self, budget: ExecutionBudget) -> None:
        self.budget = budget
        self.started = time.monotonic()
        self.model_calls = 0
        self.tool_calls = 0
        self.output_tokens = 0
        self.output_tokens_unknown = False

    def remaining(self) -> float:
        value = self.budget.deadline_seconds - (time.monotonic() - self.started)
        if value <= 0:
            raise RuntimeFailure("deadline")
        return value

    def begin_model(self) -> int:
        self.remaining()
        remaining_tokens = self.budget.max_total_output_tokens - self.output_tokens
        if self.model_calls >= self.budget.max_model_calls or remaining_tokens <= 0:
            raise RuntimeFailure("model_budget")
        self.model_calls += 1
        return min(self.budget.max_tokens_per_call, remaining_tokens)

    def record_output(self, tokens: int | None, reserved: int) -> None:
        # Unknown usage consumes the full reservation; it never creates credit.
        if tokens is None:
            self.output_tokens_unknown = True
            self.output_tokens += reserved
        elif type(tokens) is not int or not 0 <= tokens <= reserved:
            raise RuntimeFailure("truncated")
        else:
            self.output_tokens += tokens

    def begin_tool(self) -> None:
        self.remaining()
        if self.tool_calls >= self.budget.max_tool_calls:
            raise RuntimeFailure("tool_budget")
        self.tool_calls += 1
