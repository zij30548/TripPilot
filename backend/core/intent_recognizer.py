"""Travel routing adapted from EchoMind's intent/domain routing split.

This first route is deterministic lexical dispatch, not an embedding ensemble
or a calibrated semantic classifier. Native tool choice still decides actions.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IntentRoute:
    role: str
    reason_code: str
    source: str = "deterministic_lexical_v1"


class IntentRecognizer:
    def recognize(self, text: str) -> IntentRoute:
        lowered = text.casefold()
        if any(word in lowered for word in ("费用", "预算", "花费", "多少钱", "budget", "cost")):
            return IntentRoute("budget", "budget_terms")
        if any(word in lowered for word in ("搜索", "地点", "景点", "想去", "住宿", "酒店", "住在", "找", "poi")):
            return IntentRoute("places", "place_terms")
        if any(word in lowered for word in ("行程", "安排", "第几天", "预览", "排除", "规划", "schedule", "plan")):
            return IntentRoute("planning", "planning_terms")
        return IntentRoute("coordinator", "general_or_clarify")
