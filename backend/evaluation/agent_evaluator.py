"""Deterministic outcome grading, adapted from EchoMind's evaluation split.

No LLM judge, hidden calls or benchmark claims. Semantics require a separately
frozen human review; these checks only inspect technical outcomes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ExpectedOutcome:
    required_tools: tuple[str, ...] = ()
    forbidden_tools: tuple[str, ...] = ()
    maximum_model_calls: int = 3


def evaluate_outcome(outcome: Any, expected: ExpectedOutcome) -> dict[str, Any]:
    names = [item["name"] for item in outcome.tool_results]
    checks = {"required_tools": set(expected.required_tools) <= set(names),
              "forbidden_tools": not bool(set(expected.forbidden_tools) & set(names)),
              "model_budget": outcome.trace["model_calls"] <= expected.maximum_model_calls,
              "nonempty_reply": bool(outcome.reply.strip())}
    return {"passed": all(checks.values()), "checks": checks,
            "semantic_correctness": "not_assessed", "judge_calls": 0}
