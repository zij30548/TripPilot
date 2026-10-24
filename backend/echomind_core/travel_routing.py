"""Travel business policy around EchoMind's preserved structured router.

The original primary/support decision, best-instance score, execution/fallback
and parallel/composer dispatch remain the implementation.  These four enum
values are explicitly internal compatibility slots, not customer-service roles.
Domain data and the narrow parallel-read admission below do not authorize tools.
Every tool still needs its own trusted registry, input and current-version gate.
"""
from __future__ import annotations

from dataclasses import replace
import math
import re
from types import MappingProxyType, SimpleNamespace
from typing import Any

from echomind_core.execution import ExecutionError
from echomind_core.travel_domain import TRAVEL_DOMAIN, TravelIntent, TravelRole
from echomind_core.upstream.agents.agent_orchestrator import (
    AgentOrchestrator, AgentType, BaseAgent, Request, RoutingDecision,
)
from echomind_core.upstream.core.intent_recognizer import UrgencyLevel

VERSION = "travel-structured-routing-v1"
ROLE_FOR_SLOT = MappingProxyType({
    AgentType.GENERAL: TravelRole.COORDINATOR,
    AgentType.TECHNICAL: TravelRole.PLANNING,
    AgentType.BILLING: TravelRole.BUDGET,
    AgentType.ESCALATION: TravelRole.PLACE_REFERENCE,
})
SLOT_FOR_INTENT = MappingProxyType({
    TravelIntent.REQUIREMENTS: AgentType.GENERAL,
    TravelIntent.PLACES: AgentType.ESCALATION,
    TravelIntent.SCHEDULE: AgentType.TECHNICAL,
    TravelIntent.BUDGET: AgentType.BILLING,
    TravelIntent.WEATHER: AgentType.TECHNICAL,
    TravelIntent.ADJUSTMENT: AgentType.TECHNICAL,
    TravelIntent.ARCHIVES: AgentType.GENERAL,
    TravelIntent.PREFERENCES: AgentType.GENERAL,
    TravelIntent.REFERENCE: AgentType.ESCALATION,
    TravelIntent.CLARIFY: AgentType.GENERAL,
    TravelIntent.GREETING: AgentType.GENERAL,
    TravelIntent.OTHER: AgentType.GENERAL,
})
_KEYWORDS = MappingProxyType({
    AgentType.TECHNICAL: ("行程", "安排", "时间轴", "交通", "天气", "预览", "schedule", "itinerary", "weather"),
    AgentType.BILLING: ("费用", "预算", "花费", "小计", "门票估算", "budget", "cost"),
    AgentType.ESCALATION: ("景点", "地点", "住宿", "候选", "资料", "官方原文", "参观须知", "预约说明", "poi"),
    AgentType.GENERAL: ("需求", "日期", "人数", "偏好", "历史", "存档", "你好"),
})
_SCHEDULE_READ_WORDS = ("行程", "安排", "时间轴", "schedule", "itinerary")
_BUDGET_READ_WORDS = ("费用", "预算", "花费", "小计", "budget", "cost")
_READ_VERBS = ("看看", "查看", "核对", "查询", "列出", "读取", "看下", "看一下", "查一下", "show", "read")
_OTHER_DOMAIN_WORDS = ("天气", "预报", "下雨", "下雪", "资料", "预约", "营业", "搜索", "景点", "地点",
                       "住宿", "候选", "历史", "存档", "偏好", "weather", "search", "hotel", "booking")
_WRITE_OR_DEPENDENCY = re.compile(
    r"修改|更改|排除|取消|采用|保存|生成|添加|加入|删除|调整|记住|撤回|清空|恢复|"
    r"比较|预览|更新|然后|再看|再查|之后|以后|完成后|基于|新方案|"
    r"\b(?:change|exclude|adopt|save|generate|add|delete|update|then|after)\b",
    re.IGNORECASE,
)
_JOIN = re.compile(r"和|及|并|同时|另外|还有|[，,；;]|\band\b", re.IGNORECASE)
_NEGATIVE_PREFIX = re.compile(r"(?:不要|不用|不必|无需|别|不是|不想|不看|不查|不需要|暂不)[^，,。.!！？?；;\n]{0,7}$")
_NEGATIVE_SUFFIX = re.compile(r"^\s*(?:不用|不看|不查|不要|无需|不需要)")
_ENGLISH_NEGATIVE_PREFIX = re.compile(r"\b(?:do not|don't|dont|no need to|not|never)\b[\w ]{0,20}$")
_ENGLISH_NEGATIVE_SUFFIX = re.compile(r"^\s*(?:is\s+)?(?:not needed|not required|unnecessary)\b")


def _checked(request: Request) -> None:
    if (type(request) is not Request or type(request.message) is not str
            or not 1 <= len(request.message) <= 4000 or type(request.intent) is not TravelIntent
            or request.urgency is not None and type(request.urgency) is not UrgencyLevel
            or type(request.intent_confidence) not in (int, float)
            or not math.isfinite(request.intent_confidence) or not 0 <= request.intent_confidence <= 1
            or type(request.entities) is not dict
            or set(request.entities) - {"date", "day_index", "amount"}
            or any(type(values) is not list or len(values) > 16 or any(
                type(value) is not str or len(value) > 160 for value in values)
                for values in request.entities.values())
            or request.intent_group is not None and request.intent_group != TRAVEL_DOMAIN.groups.get(
                request.intent, request.intent).value):
        raise ExecutionError("travel_routing_invalid")


def _hits(message: str, words: tuple[str, ...]) -> tuple[str, ...]:
    """Bounded lexical routing hints, never a semantic or permission proof.

    Reject nearby explicit negations conservatively.  False negatives here only
    keep a request on one Agent; they never permit an extra business operation.
    """
    text = message.casefold()
    found = []
    for word in words:
        for match in re.finditer(re.escape(word.casefold()), text):
            prefix, suffix = text[max(0, match.start() - 36):match.start()], text[match.end():match.end() + 24]
            if (_NEGATIVE_PREFIX.search(prefix) or _NEGATIVE_SUFFIX.search(suffix)
                    or _ENGLISH_NEGATIVE_PREFIX.search(prefix) or _ENGLISH_NEGATIVE_SUFFIX.search(suffix)):
                continue
            found.append(word)
            break
    return tuple(found)


def needs_travel_clarification(request: Request) -> bool:
    _checked(request)
    return (request.intent is TravelIntent.CLARIFY or request.intent is TravelIntent.OTHER
            and len(request.message.strip()) > 2 and request.intent_confidence < .5)


def is_independent_read(request: Request) -> bool:
    """Only the first supported pair: current schedule + current total costs.

    This is original single-label routing plus conservative domain hints, not a
    new question decomposition/DAG.  A mutation, sequencing or dependency marker
    denies parallelism even if the two domain nouns occur in the same message.
    The execution layer must still verify both reads use the same live snapshot.
    """
    _checked(request)
    return bool(request.intent in {TravelIntent.SCHEDULE, TravelIntent.BUDGET}
                and not _WRITE_OR_DEPENDENCY.search(request.message)
                and not _hits(request.message, _OTHER_DOMAIN_WORDS)
                and _JOIN.search(request.message)
                and _hits(request.message, _READ_VERBS)
                and _hits(request.message, _SCHEDULE_READ_WORDS)
                and _hits(request.message, _BUDGET_READ_WORDS))


def routing_projection(decision: RoutingDecision) -> dict[str, Any]:
    """Project only finite public travel roles; no request text or owner IDs."""
    if (type(decision) is not RoutingDecision or type(decision.primary_agent) is not AgentType
            or type(decision.supporting_agents) is not list
            or any(type(slot) is not AgentType for slot in decision.supporting_agents)
            or len(decision.supporting_agents) > 1
            or len(set(decision.agent_types)) != len(decision.agent_types)
            or type(decision.reason) is not str or len(decision.reason) > 2000
            or type(decision.confidence) not in (int, float)
            or not math.isfinite(decision.confidence) or not 0 <= decision.confidence <= 1):
        raise ExecutionError("travel_routing_invalid")
    return {"schema_version": VERSION, "primary_agent": ROLE_FOR_SLOT[decision.primary_agent].value,
            "supporting_agents": [ROLE_FOR_SLOT[slot].value for slot in decision.supporting_agents],
            "routing_reason": decision.reason, "routing_confidence": decision.confidence}


class TravelRoutingPolicy:
    """Mixin for the full candidate, never a standalone service/config switch.

    Do not override _execute or run_parallel: their inherited implementations
    are the original algorithms.  Health filtering also covers the place and
    coordinator compatibility slots, unlike the original customer-only filter.
    """
    _pool: dict[AgentType, list[BaseAgent]]
    _INTENT_ROUTING = SLOT_FOR_INTENT
    is_independent_read = staticmethod(is_independent_read)
    needs_travel_clarification = staticmethod(needs_travel_clarification)
    routing_projection = staticmethod(routing_projection)

    @staticmethod
    def _needs_clarification(request: Request) -> bool:
        # Upstream run's True branch has hard-coded order/refund/account prose.
        # Route to the travel coordinator instead, whose finite tool produces
        # the appropriate travel clarification, without inventing a human queue.
        _checked(request)
        return False

    def _monitor_degraded(self, slot: AgentType) -> bool:
        if type(slot) is not AgentType:
            raise ExecutionError("travel_routing_invalid")
        agents = self._pool.get(slot, [])
        return not agents or all(not agent.stats.available() for agent in agents)

    def _best_agent(self, slot: AgentType) -> BaseAgent | None:
        if type(slot) is not AgentType:
            raise ExecutionError("travel_routing_invalid")
        # Call the original performance-based selection on a local filtered
        # view; never swap/mutate the real pool while sibling Agents run.
        eligible = [agent for agent in self._pool.get(slot, []) if agent.stats.available()]
        view = SimpleNamespace(_pool={slot: eligible})
        return AgentOrchestrator._best_agent(view, slot)

    def _domain_scores(self, request: Request) -> dict[AgentType, float]:
        _checked(request)
        scores = {slot: (.1 if slot is AgentType.GENERAL else 0.0) for slot in AgentType}
        target = SLOT_FOR_INTENT[request.intent]
        # Preserve original category and domain-keyword/entity weights; only
        # customer data (error/refund/order) is replaced by finite travel data.
        keywords = _KEYWORDS[target]
        denied = (any(word.casefold() in request.message.casefold() for word in keywords)
                  and not _hits(request.message, keywords))
        if not denied:
            scores[target] += .55 if target is AgentType.GENERAL else .75
        for slot, words in _KEYWORDS.items():
            scores[slot] += min(.35 if slot is AgentType.GENERAL else .45,
                                len(_hits(request.message, words)) * (.12 if slot is AgentType.GENERAL else .18))
        if request.entities.get("date") or request.entities.get("day_index"):
            scores[AgentType.TECHNICAL] += .2
        if request.entities.get("amount"):
            scores[AgentType.BILLING] += .15
        return {slot: round(score, 3) if not self._monitor_degraded(slot) else 0.0
                for slot, score in scores.items()}

    def _collaboration_targets(self, request: Request) -> list[AgentType]:
        if not is_independent_read(request):
            return []
        return [slot for slot in (AgentType.TECHNICAL, AgentType.BILLING)
                if not self._monitor_degraded(slot)]

    def _route_decision(self, request: Request) -> RoutingDecision:
        _checked(request)
        if request.urgency is UrgencyLevel.CRITICAL or needs_travel_clarification(request):
            # ESCALATION is our internal place slot, not a real human handoff.
            reason = ("travel_priority_requires_coordinator" if request.urgency is UrgencyLevel.CRITICAL
                      else "travel_intent_requires_clarification")
            return RoutingDecision(AgentType.GENERAL, reason=reason, confidence=request.intent_confidence)
        decision = AgentOrchestrator._route_decision(self, request)
        if decision.supporting_agents and not is_independent_read(request):
            # Original score fallback can nominate secondary domains even when
            # _collaboration_targets returned []; explicitly guard that branch.
            scores = self._domain_scores(request)
            decision = replace(decision, supporting_agents=[], reason=self._routing_reason(
                request, scores, decision.primary_agent, []) + ", travel_parallel=not_independent_read")
        routing_projection(decision)
        return decision
