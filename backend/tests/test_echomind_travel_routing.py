"""Controlled travel policy tests; no model, database, service or network calls."""
from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import replace
import importlib.util
import unittest
from unittest.mock import patch

if any(importlib.util.find_spec(name) is None for name in ("jsonschema", "prometheus_client")):
    raise unittest.SkipTest("Travel routing checks require the explicit echomind extra")

from echomind_core.execution import ExecutionError
from echomind_core.travel_domain import TravelIntent, TravelRole
from echomind_core.travel_routing import (
    ROLE_FOR_SLOT, SLOT_FOR_INTENT, VERSION, TravelRoutingPolicy,
    is_independent_read, needs_travel_clarification, routing_projection,
)
from echomind_core.upstream.agents.agent_orchestrator import (
    AgentCallResult, AgentOrchestrator, AgentProfile, AgentResponse, AgentStats, AgentType, BaseAgent,
    Request, RoutingDecision,
)
from echomind_core.upstream.agents.tools import AgentToolSpec
from echomind_core.upstream.core.intent_recognizer import IntentCategory, UrgencyLevel, _TEMPLATES


def request(message="核对当前费用", intent=TravelIntent.BUDGET, **values):
    return Request(message, "controlled-owner", "controlled-conversation", intent=intent,
                   intent_group="schedule" if intent is TravelIntent.ADJUSTMENT else intent.value,
                   urgency=UrgencyLevel.LOW, **values)


class Agent:
    def __init__(self, slot, *, success=True):
        self.agent_type, self.stats, self.success, self.calls = slot, AgentStats(), success, []

    async def handle(self, captured):
        self.calls.append(captured)
        return AgentResponse(self.agent_type, "controlled readonly output", self.success,
                             status="success" if self.success else "failed")


class Composer:
    def __init__(self):
        self.calls = []

    async def compose_result(self, captured, responses):
        self.calls.append((captured, responses))
        return AgentCallResult("controlled composed output")


class Harness(TravelRoutingPolicy, AgentOrchestrator):
    def __init__(self):
        self._pool = {slot: [Agent(slot)] for slot in AgentType}
        self._recent_tool_traces = deque(maxlen=10)
        self._composer = Composer()


class TravelRoutingTests(unittest.TestCase):
    def setUp(self):
        self.engine = Harness()

    def test_roles_intents_and_policy_version_are_finite(self):
        self.assertEqual(set(ROLE_FOR_SLOT), set(AgentType))
        self.assertEqual(set(ROLE_FOR_SLOT.values()), set(TravelRole))
        self.assertEqual(set(SLOT_FOR_INTENT), set(TravelIntent))
        self.assertEqual(SLOT_FOR_INTENT[TravelIntent.PREFERENCES], AgentType.GENERAL)
        self.assertEqual(SLOT_FOR_INTENT[TravelIntent.ARCHIVES], AgentType.GENERAL)
        self.assertEqual(VERSION, "travel-structured-routing-v1")
        with self.assertRaises(TypeError):
            SLOT_FOR_INTENT[TravelIntent.BUDGET] = AgentType.ESCALATION

    def test_original_category_keyword_and_entity_weights(self):
        scores = self.engine._domain_scores(request("费用预算花费小计", entities={"amount": ["100元"]}))
        self.assertEqual(scores[AgentType.BILLING], 1.35)
        self.assertEqual(scores[AgentType.GENERAL], .1)
        scores = self.engine._domain_scores(request("看看安排", TravelIntent.SCHEDULE,
                                                    entities={"day_index": ["2"]}))
        self.assertEqual(scores[AgentType.TECHNICAL], 1.13)
        scores = self.engine._domain_scores(request("记住明确偏好", TravelIntent.PREFERENCES))
        self.assertEqual(scores[AgentType.GENERAL], .77)

    def test_each_intent_routes_to_its_travel_role_without_customer_words(self):
        for intent, slot in SLOT_FOR_INTENT.items():
            with self.subTest(intent=intent):
                decision = self.engine._route_decision(request("当前请求", intent))
                self.assertIs(decision.primary_agent, slot)
                public = routing_projection(decision)
                self.assertEqual(public["primary_agent"], ROLE_FOR_SLOT[slot].value)
                self.assertNotIn("technical", public["primary_agent"])
                self.assertEqual(public["supporting_agents"], [])

    def test_original_route_method_is_delegated_without_mutating_request(self):
        captured = request()
        before, original = deepcopy(captured.__dict__), AgentOrchestrator._route_decision
        calls = []

        def observed(engine, value):
            calls.append(value)
            return original(engine, value)

        with patch.object(AgentOrchestrator, "_route_decision", observed):
            self.engine._route_decision(captured)
        self.assertEqual(calls, [captured])
        self.assertEqual(captured.__dict__, before)

    def test_negated_domains_do_not_start_parallel_or_outweigh_active_request(self):
        for text in ("不要查看费用，看看第二天行程", "查看行程，预算不用查", "不要看预算和费用，只看行程",
                     "show itinerary and do not read budget", "read schedule, cost is not needed"):
            with self.subTest(text=text):
                captured = request(text, TravelIntent.SCHEDULE)
                self.assertFalse(is_independent_read(captured))
                self.assertEqual(self.engine._route_decision(captured).supporting_agents, [])
        denied = request("不要看费用，查看行程", TravelIntent.BUDGET)
        self.assertIs(self.engine._route_decision(denied).primary_agent, AgentType.TECHNICAL)

    def test_independent_current_schedule_and_cost_reads_have_primary_and_support(self):
        for intent, primary, support in ((TravelIntent.SCHEDULE, AgentType.TECHNICAL, AgentType.BILLING),
                                         (TravelIntent.BUDGET, AgentType.BILLING, AgentType.TECHNICAL)):
            with self.subTest(intent=intent):
                captured = request("查看第二天行程，同时核对当前全程费用", intent)
                self.assertTrue(is_independent_read(captured))
                decision = self.engine._route_decision(captured)
                self.assertIs(decision.primary_agent, primary)
                self.assertEqual(decision.supporting_agents, [support])

    def test_dependent_mutating_and_ambiguous_requests_never_parallel(self):
        for text in ("生成行程然后核对费用预算花费小计", "排除公园后查看行程和预算", "采用预览并查看安排和费用",
                     "先查看行程，再看费用", "调整行程和核对费用", "保存行程以及核对预算", "行程和预算", "查看费用",
                     "查看行程和预算，并搜索住宿", "查看行程和费用，还有天气预报"):
            with self.subTest(text=text):
                captured = request(text, TravelIntent.SCHEDULE, entities={"amount": ["100元"]})
                self.assertFalse(is_independent_read(captured))
                # This includes enough secondary score to trigger upstream's
                # automatic secondary-score fallback without our final guard.
                self.assertEqual(self.engine._route_decision(captured).supporting_agents, [])

    def test_secondary_score_fallback_cannot_bypass_read_only_gate(self):
        captured = request("修改行程，核对费用预算花费小计", TravelIntent.SCHEDULE,
                           entities={"amount": ["100元"]})
        raw = AgentOrchestrator._route_decision(self.engine, captured)
        self.assertEqual(raw.supporting_agents, [AgentType.BILLING])
        checked = self.engine._route_decision(captured)
        self.assertEqual(checked.supporting_agents, [])
        self.assertIn("not_independent_read", checked.reason)

    def test_critical_and_low_confidence_use_coordinator_not_place_slot(self):
        critical = request("紧急核对当前安排", TravelIntent.SCHEDULE)
        critical.urgency = UrgencyLevel.CRITICAL
        self.assertIs(self.engine._route_decision(critical).primary_agent, AgentType.GENERAL)
        low = request("这个事情怎么办", TravelIntent.OTHER, intent_confidence=.2)
        self.assertTrue(needs_travel_clarification(low))
        self.assertFalse(self.engine._needs_clarification(low))
        self.assertIs(self.engine._route_decision(low).primary_agent, AgentType.GENERAL)
        self.assertFalse(needs_travel_clarification(request("你好", TravelIntent.OTHER, intent_confidence=.2)))

    def test_health_fallback_covers_all_specialist_slots(self):
        for intent, slot in ((TravelIntent.SCHEDULE, AgentType.TECHNICAL),
                             (TravelIntent.BUDGET, AgentType.BILLING),
                             (TravelIntent.PLACES, AgentType.ESCALATION)):
            with self.subTest(slot=slot):
                engine = Harness()
                engine._pool[slot][0].stats.set_penalty(.9)
                self.assertTrue(engine._monitor_degraded(slot))
                self.assertIsNone(engine._best_agent(slot))
                decision = engine._route_decision(request("当前请求", intent))
                self.assertIs(decision.primary_agent, AgentType.GENERAL)

    def test_original_best_score_selection_filters_without_mutating_pool(self):
        blocked, available = Agent(AgentType.ESCALATION), Agent(AgentType.ESCALATION)
        blocked.stats.set_penalty(.9)
        self.engine._pool[AgentType.ESCALATION] = [blocked, available]
        original_pool = self.engine._pool
        before = list(original_pool[AgentType.ESCALATION])
        original, calls = AgentOrchestrator._best_agent, []

        def observed(view, slot):
            calls.append((view, slot))
            return original(view, slot)

        with patch.object(AgentOrchestrator, "_best_agent", observed):
            self.assertIs(self.engine._best_agent(AgentType.ESCALATION), available)
        self.assertEqual(len(calls), 1)
        self.assertIs(self.engine._pool, original_pool)
        self.assertEqual(original_pool[AgentType.ESCALATION], before)

    def test_degraded_support_is_not_dispatched(self):
        self.engine._pool[AgentType.BILLING][0].stats.set_penalty(.9)
        captured = request("查看安排和核对费用", TravelIntent.SCHEDULE)
        decision = self.engine._route_decision(captured)
        self.assertEqual(decision.supporting_agents, [])

    def test_projection_rejects_invalid_slots_duplicates_or_confidence(self):
        for decision in (RoutingDecision("technical"),
                         RoutingDecision(AgentType.TECHNICAL, [AgentType.TECHNICAL]),
                         RoutingDecision(AgentType.TECHNICAL, [AgentType.BILLING, AgentType.ESCALATION]),
                         RoutingDecision(AgentType.GENERAL, confidence=True),
                         RoutingDecision(AgentType.GENERAL, confidence=float("nan"))):
            with self.subTest(decision=decision):
                with self.assertRaisesRegex(ExecutionError, "travel_routing_invalid"):
                    routing_projection(decision)

    def test_invalid_request_fields_fail_before_routing(self):
        valid = request()
        for changes in ({"intent": IntentCategory.BILLING}, {"intent": "budget"}, {"message": ""},
                        {"intent_confidence": True}, {"intent_confidence": float("inf")},
                        {"intent_group": "technical"}, {"entities": {"owner_id": ["forged"]}},
                        {"entities": {"date": "2026-10-24"}}, {"entities": {"date": ["x"] * 17}}):
            with self.subTest(changes=changes):
                invalid = replace(valid, **changes)
                with self.assertRaisesRegex(ExecutionError, "travel_routing_invalid"):
                    self.engine._route_decision(invalid)

    def test_policy_never_mutates_original_enum_templates_or_route_table(self):
        templates = deepcopy(_TEMPLATES)
        table = dict(AgentOrchestrator._INTENT_ROUTING)
        members = dict(AgentType.__members__)
        for intent in TravelIntent:
            self.engine._route_decision(request("查看当前请求", intent))
        self.assertEqual(_TEMPLATES, templates)
        self.assertEqual(AgentOrchestrator._INTENT_ROUTING, table)
        self.assertEqual(AgentType.__members__, members)
        self.assertIs(Harness._execute, AgentOrchestrator._execute)
        self.assertIs(Harness.run_parallel, AgentOrchestrator.run_parallel)

    def test_routing_and_fallback_do_not_expand_registered_agent_tools(self):
        class ScopedAgent(BaseAgent):
            agent_type = AgentType.GENERAL

        tools = {name: AgentToolSpec(name, "controlled only", {"type": "object"}, lambda *_: {},
                                     allowed_agents=(slot.value,))
                 for name, slot in (("inspect_trip", AgentType.GENERAL),
                                    ("travel_schedule", AgentType.TECHNICAL),
                                    ("travel_budget", AgentType.BILLING),
                                    ("travel_places", AgentType.ESCALATION))}
        for slot in AgentType:
            agent = ScopedAgent(None, "controlled-model", profile=AgentProfile(
                role=ROLE_FOR_SLOT[slot].value, mission="controlled", workflow=(),
                input_contract=(), output_contract=()))
            agent.agent_type = slot
            agent.set_shared_tools(tools)
            self.engine._pool[slot] = [agent]
        before = {slot: set(agents[0].get_tools()) for slot, agents in self.engine._pool.items()}
        self.engine._pool[AgentType.TECHNICAL][0].stats.set_penalty(.9)
        decision = self.engine._route_decision(request("查看行程", TravelIntent.SCHEDULE))
        self.assertIs(decision.primary_agent, AgentType.GENERAL)
        chosen = self.engine._best_agent(decision.primary_agent)
        self.assertEqual(set(chosen.get_tools()), {"inspect_trip"})
        self.assertNotIn("travel_schedule", chosen.get_tools())
        self.assertNotIn("travel_places", chosen.get_tools())
        self.assertEqual({slot: set(agents[0].get_tools()) for slot, agents in self.engine._pool.items()}, before)


class OriginalDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_original_execution_fallback_uses_registered_coordinator(self):
        engine = Harness()
        engine._pool[AgentType.TECHNICAL][0].success = False
        captured = request("查看安排", TravelIntent.SCHEDULE)
        result = await engine._execute(captured, AgentType.TECHNICAL)
        self.assertIs(result.agent_type, AgentType.GENERAL)
        self.assertEqual(len(engine._pool[AgentType.TECHNICAL][0].calls), 1)
        self.assertEqual(len(engine._pool[AgentType.GENERAL][0].calls), 1)

    async def test_unsafe_unknown_attempt_does_not_trigger_fallback(self):
        engine = Harness()
        engine._pool[AgentType.TECHNICAL][0].success = False
        captured = request("查看安排", TravelIntent.SCHEDULE)
        captured.tool_attempts.append({"tool_name": "controlled_proposal", "retry_safe": False})
        result = await engine._execute(captured, AgentType.TECHNICAL)
        self.assertFalse(result.success)
        self.assertEqual(engine._pool[AgentType.GENERAL][0].calls, [])

    async def test_original_parallel_dispatch_and_composer_receive_same_snapshot(self):
        engine = Harness()
        captured = request("查看安排以及核对费用", TravelIntent.SCHEDULE)
        decision = engine._route_decision(captured)
        result = await engine.run_parallel(captured, decision)
        self.assertEqual(result.agent_types, [AgentType.TECHNICAL, AgentType.BILLING])
        self.assertEqual(result.supporting_agents, [AgentType.BILLING])
        self.assertIs(engine._pool[AgentType.TECHNICAL][0].calls[0], captured)
        self.assertIs(engine._pool[AgentType.BILLING][0].calls[0], captured)
        self.assertIs(engine._composer.calls[0][0], captured)
        self.assertEqual(len(engine._composer.calls), 1)
        self.assertEqual(engine._pool[AgentType.ESCALATION][0].calls, [])


if __name__ == "__main__":
    unittest.main()
