"""Preserved travel execution paths, controlled wire and independent counters.

These are mechanical integrations, not live intent/retrieval quality. No active
product route, real credential, user database or business API is contacted.
"""
from __future__ import annotations

import asyncio
from contextlib import closing
from dataclasses import replace
import importlib.util
import json
import sqlite3
import unittest
from unittest.mock import patch

import httpx

if any(importlib.util.find_spec(name) is None for name in ("jsonschema", "prometheus_client")):
    raise unittest.SkipTest("Travel engine checks require the explicit echomind extra")

from prometheus_client import CollectorRegistry
from core.config import Settings
from core.llm_utils import RuntimeFailure
from core.usage_runtime import MeteredAsyncClient
from echomind_core.embedding import DIMENSIONS, EMBEDDING_MODEL, QwenIntentEmbedding
from echomind_core.execution import ExecutionError, ExecutionGate, SafeToolManager, bind_execution
from echomind_core.provider import MODEL, MAX_INPUT_BYTES, TaskBudget, bind_provider
from echomind_core.travel_bridge import CLARIFICATIONS, TravelToolBridge
from echomind_core.travel_domain import TravelIntent, TravelIntentRecognizer, TravelRole
from echomind_core.travel_engine import (
    ROLE_FOR_SLOT, TravelAssistantCandidate, TravelMode, TravelOrchestrator, _TravelAgent,
)
from echomind_core.upstream.agents.agent_orchestrator import (
    AgentOrchestrator, AgentType, BaseAgent, RoutingDecision,
)
from echomind_core.upstream.monitor.performance_monitor import PerformanceMonitor
from tests.test_chat_storage import intent
from tests.test_echomind_travel_bridge import BINDING, OP, SCOPE, capture, request
from tests.usage_fixture import UsageFixture

PATH = f"/chat/conversations/{SCOPE.conversation_id}/assistant-turns"
FABRICATED = "模型伪造正文：已免费预订全部酒店，预算确定为零。"


def native_text(content=FABRICATED):
    return {"model": MODEL, "id": "controlled_native", "choices": [{
        "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 19, "completion_tokens": 7}}


def native_tools(*calls):
    return {"model": MODEL, "id": "controlled_native", "choices": [{
        "finish_reason": "tool_calls", "message": {"role": "assistant", "content": None,
            "tool_calls": [{"id": identifier, "type": "function", "function": {
                "name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}
                for identifier, name, arguments in calls]}}],
        "usage": {"prompt_tokens": 19, "completion_tokens": 7}}


class TravelEngineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = UsageFixture()
        self.addCleanup(self.fixture.close)
        self.settings = Settings(_env_file=None, qwen_api_key="controlled-not-real",
            qwen_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", qwen_model=MODEL)
        self.current, self.scope, self.sent = BINDING, SCOPE, []
        snapshot, self.conversation, self.places, self.preferences = capture()
        self.bridge = TravelToolBridge(snapshot, lambda: self.current)

    def gate(self, budget=None):
        return ExecutionGate(SCOPE, lambda: self.scope, budget or TaskBudget())

    def rows(self):
        with closing(sqlite3.connect(self.fixture.store.settings.business_usage_path)) as db:
            return db.execute("SELECT service,state FROM usage_attempts ORDER BY rowid").fetchall()

    def client(self, responses, hook=None):
        async def controlled(message):
            body = json.loads(message.content)
            self.sent.append({"path": message.url.path, "bytes": len(message.content), "payload": body})
            if hook is not None:
                value = hook(len(self.sent), body)
                if asyncio.iscoroutine(value):
                    await value
            response = responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return httpx.Response(200, json=response)
        return MeteredAsyncClient(transport=httpx.MockTransport(controlled), follow_redirects=False)

    async def run_candidate(self, responses, *, candidate=None, req=None, gate=None, hook=None):
        candidate = candidate or TravelAssistantCandidate(self.bridge)
        with self.fixture.bind(PATH):
            async with self.client(responses, hook) as client:
                return await candidate.run(req or request(), gate or self.gate(), self.settings, client)

    async def test_original_run_and_route_execution_stats_are_actually_used(self):
        candidate = TravelAssistantCandidate(self.bridge)
        self.assertIs(TravelOrchestrator._route_decision, AgentOrchestrator._route_decision)
        self.assertIs(TravelOrchestrator._execute, AgentOrchestrator._execute)
        self.assertIs(TravelOrchestrator._best_agent, AgentOrchestrator._best_agent)
        self.assertIs(TravelOrchestrator.update_routing_penalties, AgentOrchestrator.update_routing_penalties)
        invoked, original_run = [], AgentOrchestrator.run
        calls, original_call = [], BaseAgent._call_llm

        async def observed(instance, captured):
            invoked.append((instance, captured.intent))
            return await original_run(instance, captured)

        async def delegated_call(instance, captured):
            calls.append((instance, captured.request_id))
            return await original_call(instance, captured)

        with patch.object(AgentOrchestrator, "run", observed), \
                patch.object(BaseAgent, "_call_llm", delegated_call):
            result = await self.run_candidate([
                native_tools(("inspect_once", "inspect_trip", {})), native_text()], candidate=candidate,
                req=request(intent=TravelIntent.REQUIREMENTS, intent_group="requirements"))
        self.assertEqual(len(invoked), 1)
        self.assertIs(invoked[0][1], TravelIntent.REQUIREMENTS)
        self.assertEqual(calls, [(candidate.orchestrator._pool[AgentType.GENERAL][0], OP)])
        self.assertEqual(result.role, TravelRole.COORDINATOR.value)
        stats = candidate.orchestrator.get_stats()["general_0"]
        self.assertEqual((stats["total"], stats["success_rate"]), (1, 1.0))
        self.assertEqual(result.counts, (2, 1, 1800))

    async def test_four_roles_only_expose_accurate_finite_travel_tools(self):
        engine = TravelOrchestrator(self.bridge)
        self.assertEqual(set(ROLE_FOR_SLOT.values()), set(TravelRole))
        expected = {
            AgentType.GENERAL: set(self.bridge.tools()),
            AgentType.TECHNICAL: {"inspect_trip", "travel_clarify", "travel_information", "travel_schedule",
                "travel_adjustment", "travel_weather", "travel_workspace", "travel_archives", "travel_views", "update_trip_requirements"},
            AgentType.BILLING: {"inspect_trip", "travel_clarify", "travel_information", "travel_budget",
                               "travel_preferences", "read_preferences"},
            AgentType.ESCALATION: {"inspect_trip", "travel_clarify", "travel_information", "travel_places",
                                  "travel_materials", "search_materials"},
        }
        old_customer_tools = {"compare_amounts", "lookup_error_code", "check_billing_fields",
                              "suggest_required_fields", "create_handoff_summary", "search_knowledge_base"}
        for slot, agents in engine._pool.items():
            agent = agents[0]
            self.assertIs(type(agent).handle, BaseAgent.handle)
            # Only a request-local program handoff wraps the source-preserved
            # loop. The delegation above still exercises that original loop.
            self.assertIs(type(agent)._call_llm, _TravelAgent._call_llm)
            self.assertIsNot(type(agent)._call_llm, BaseAgent._call_llm)
            self.assertEqual(set(agent.get_tools()), expected[slot])
            self.assertFalse(old_customer_tools & agent.get_tools().keys())
            self.assertEqual(agent.profile.role, ROLE_FOR_SLOT[slot].value)
        self.assertEqual(len(engine._shared_tools), 16)

    async def test_actual_sixteen_tool_payload_fits_unraised_input_limit_and_native_feedback(self):
        result = await self.run_candidate([native_tools(("inspect_once", "inspect_trip", {})), native_text()])
        self.assertEqual(result.state, "read_complete")
        self.assertEqual(len(self.sent[0]["payload"]["tools"]), 16)
        self.assertTrue(all(row["bytes"] <= MAX_INPUT_BYTES for row in self.sent))
        self.assertEqual(self.sent[1]["payload"]["messages"][-1]["role"], "tool")
        self.assertEqual(self.sent[1]["payload"]["messages"][-1]["tool_call_id"], "inspect_once")
        self.assertEqual(self.rows(), [("qwen_text", "received")] * 2)
        self.assertNotIn(FABRICATED, result.message)
        self.assertEqual(result.projection["tool_results"][0]["name"], "inspect_trip")
        self.assertIn("受控地点", result.message)

    async def test_requirements_proposal_is_quote_bound_unexecuted_and_program_rendered(self):
        before = self.bridge.snapshot.payload()
        result = await self.run_candidate([native_tools(("requirements_once", "update_trip_requirements", {
            "requirements": {**intent(), "travelers": 3},
            "sources": [{"field": "travelers", "quote": "三人"}], "issues": []}))],
            req=request(message="改为三人去杭州"))
        self.assertEqual(result.state, "awaiting_confirmation")
        proposal = result.projection["tool_results"][0]["data"]
        self.assertEqual(proposal["requirements"]["travelers"], 3)
        self.assertFalse(proposal["executed"])
        self.assertTrue(proposal["confirmation_required"])
        self.assertEqual(self.bridge.snapshot.payload(), before)
        self.assertNotIn(FABRICATED, result.message)
        self.assertEqual(result.counts, (1, 1, 900))
        self.assertEqual(self.rows(), [("qwen_text", "received")])

    async def test_budget_is_browser_proposal_not_second_backend_calculator(self):
        result = await self.run_candidate([native_tools(("budget_once", "travel_budget", {"action": "read_budget"}))],
            req=request(message="费用多少，哪些未知", intent=TravelIntent.BUDGET, intent_group="budget"))
        self.assertEqual(result.state, "pending_browser")
        self.assertEqual(result.role, TravelRole.BUDGET.value)
        proposal = result.projection["tool_results"][0]["data"]
        self.assertFalse(proposal["executed"])
        self.assertTrue(proposal["browser_context_check_required"])
        self.assertEqual(proposal["action"], {"name": "read_budget", "arguments": {}})
        self.assertNotIn("total", proposal)
        self.assertNotIn(FABRICATED, result.message)
        self.assertEqual(result.counts, (1, 1, 900))
        self.assertEqual(self.rows(), [("qwen_text", "received")])

    async def test_no_tool_free_prose_is_not_a_business_answer(self):
        result = await self.run_candidate([native_text()])
        self.assertEqual((result.state, result.code), ("failed", "travel_tool_required"))
        self.assertIsNone(result.projection)
        self.assertNotIn(FABRICATED, result.message)
        self.assertEqual(result.counts, (1, 0, 900))

    async def test_unsupported_and_ambiguous_requests_only_show_program_clarification(self):
        for reason in ("unsupported", "multiple_requests", "target_ambiguous"):
            snapshot, *_ = capture()
            bridge = TravelToolBridge(snapshot, lambda: self.current)
            before = len(self.sent)
            result = await self.run_candidate([native_tools(("clarify_once", "travel_clarify", {"reason": reason}))],
                candidate=TravelAssistantCandidate(bridge))
            self.assertEqual(result.state, "clarification")
            self.assertEqual(result.message, CLARIFICATIONS[reason])
            self.assertFalse(result.projection["tool_results"][0]["data"]["executed"])
            self.assertEqual(result.counts, (1, 1, 900))
            self.assertEqual(len(self.sent), before + 1)

    async def test_illegal_tool_or_arguments_are_failed_closed_not_repaired(self):
        bad = [("unknown_payment", {}), ("travel_budget", {"action": "read_budget", "total": 0}),
               ("inspect_trip", {"owner_id": "b" * 32}),
               ("update_trip_requirements", {"requirements": {**intent(), "travelers": 3},
                    "sources": [{"field": "travelers", "quote": "三人"}], "issues": []})]
        for name, arguments in bad:
            snapshot, *_ = capture()
            bridge = TravelToolBridge(snapshot, lambda: self.current)
            before = len(self.sent)
            result = await self.run_candidate([native_tools(("illegal_once", name, arguments))],
                candidate=TravelAssistantCandidate(bridge), req=request(message="保持原需求"))
            self.assertEqual(result.state, "failed")
            self.assertIsNone(result.projection)
            self.assertEqual(len(self.sent), before + 1)
            self.assertNotIn("三人", result.message)

    async def test_specialist_cannot_invoke_another_roles_tool(self):
        result = await self.run_candidate([native_tools(("wrong_role", "travel_places", {"action": "open_photo"}))],
            req=request(intent=TravelIntent.BUDGET, intent_group="budget"))
        self.assertEqual(result.state, "failed")
        self.assertIsNone(result.projection)
        self.assertEqual(len(self.sent), 1)

    async def test_late_success_after_binding_change_is_not_published(self):
        def changed(index, _):
            if index == 2:
                self.current = replace(BINDING, place_revision=4)
        result = await self.run_candidate([native_tools(("inspect_once", "inspect_trip", {})), native_text()], hook=changed)
        self.assertEqual((result.state, result.code), ("failed", "travel_context_changed"))
        self.assertIsNone(result.projection)
        self.assertEqual(len(self.sent), 2)

    async def test_aba_scope_change_discards_old_response(self):
        def changed(index, _):
            if index == 2:
                # Same city/input text A again is still a newer input version.
                self.scope = replace(SCOPE, input_version=SCOPE.input_version + 2)
        result = await self.run_candidate([native_tools(("inspect_once", "inspect_trip", {})), native_text()], hook=changed)
        self.assertEqual(result.state, "failed")
        self.assertIsNone(result.projection)
        self.assertEqual(len(self.sent), 2)

    async def test_authority_failure_inside_projection_returns_safe_failed_turn(self):
        original_project = self.bridge.project

        async def changed_project():
            self.current = replace(BINDING, intent_revision=3)
            return await original_project()

        with patch.object(self.bridge, "project", changed_project):
            result = await self.run_candidate([native_tools(("inspect_once", "inspect_trip", {})), native_text()])
        self.assertEqual((result.state, result.code), ("failed", "travel_context_changed"))
        self.assertIsNone(result.projection)
        self.assertEqual(result.counts, (2, 1, 1800))

    async def test_scope_change_after_projection_await_is_checked_before_publication(self):
        original_project = self.bridge.project

        async def changed_after_projection():
            result = await original_project()
            self.scope = replace(SCOPE, input_version=SCOPE.input_version + 2)
            return result

        with patch.object(self.bridge, "project", changed_after_projection):
            result = await self.run_candidate([native_tools(("inspect_once", "inspect_trip", {})), native_text()])
        self.assertEqual((result.state, result.code), ("failed", "travel_context_changed"))
        self.assertIsNone(result.projection)
        self.assertNotIn("受控地点", result.message)

    async def test_late_transport_error_does_not_replace_new_scope_with_old_error(self):
        def changed(index, _):
            self.scope = replace(SCOPE, input_version=SCOPE.input_version + 2)
        result = await self.run_candidate([httpx.ConnectError("private controlled upstream exception")], hook=changed)
        self.assertEqual((result.state, result.code), ("failed", "travel_context_changed"))
        self.assertIsNone(result.projection)
        self.assertNotIn("private", result.message)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(self.rows()), 1)

    async def test_same_captured_candidate_operation_cannot_send_twice(self):
        candidate = TravelAssistantCandidate(self.bridge)
        result = await self.run_candidate([native_tools(("inspect_once", "inspect_trip", {})), native_text()], candidate=candidate)
        self.assertEqual(result.state, "read_complete")
        with self.assertRaisesRegex(ExecutionError, "travel_context_changed"):
            await self.run_candidate([], candidate=candidate)
        self.assertEqual(len(self.sent), 2)
        # This object fence is not claimed to replace durable MySQL admission.

    async def test_cancellation_is_counted_and_same_operation_stays_closed(self):
        started, finish = asyncio.Event(), asyncio.Event()
        candidate = TravelAssistantCandidate(self.bridge)
        async def held(index, _):
            started.set()
            await finish.wait()
        task = asyncio.create_task(self.run_candidate([native_text()], candidate=candidate, hook=held))
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(len(self.rows()), 1)
        with self.assertRaisesRegex(ExecutionError, "travel_context_changed"):
            await self.run_candidate([], candidate=candidate)
        self.assertEqual(len(self.sent), 1)

    async def test_false_customer_state_or_wrong_intent_group_is_rejected_before_sending(self):
        for changes in ({"intent": TravelIntent.BUDGET, "intent_group": "places"},
                        {"intent_group": "budget"}, {"conversation_state": {"domain": "commerce"}},
                        {"context": "fake known official price"}, {"entities": {"amount": ["0元"]}}):
            snapshot, *_ = capture()
            candidate = TravelAssistantCandidate(TravelToolBridge(snapshot, lambda: self.current))
            with self.assertRaisesRegex(ExecutionError, "travel_request_invalid"):
                await self.run_candidate([], candidate=candidate, req=request(**changes))
        self.assertEqual(self.sent, [])

    async def test_original_monitor_feedback_changes_specialist_routing(self):
        candidate = TravelAssistantCandidate(self.bridge)
        engine = candidate.orchestrator
        specialist = engine._pool[AgentType.TECHNICAL][0]
        for _ in range(11):
            specialist.stats.total += 1
            specialist.stats.total_ms += 6000
            specialist.stats.record(False, 6000)
        monitor = PerformanceMonitor(engine, SafeToolManager(), registry=CollectorRegistry())
        try:
            self.assertIs(engine._best_agent(AgentType.TECHNICAL), specialist)
            await monitor._collect()
            self.assertIsNone(engine._best_agent(AgentType.TECHNICAL))
            self.assertGreaterEqual(specialist.stats.monitor_penalty, .5)
            decision = engine._route_decision(request(intent=TravelIntent.SCHEDULE, intent_group="schedule"))
            self.assertIs(decision.primary_agent, AgentType.GENERAL)
            self.assertIn("monitor_fallback", decision.reason)
        finally:
            await monitor.stop()

    async def test_parallel_tool_batch_is_rejected_before_any_execution(self):
        four = native_tools(*[(f"read_{index}", "inspect_trip", {}) for index in range(4)])
        result = await self.run_candidate([four])
        self.assertEqual(result.state, "failed")
        self.assertEqual(result.counts, (1, 0, 900))
        self.assertIsNone(result.projection)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(self.bridge._rows), 0)

    async def test_original_task_limits_are_not_expanded_by_the_program_handoff(self):
        budget = TaskBudget()
        for _ in range(4):
            await budget.reserve_tool()
        self.assertEqual(budget.counts, (0, 4, 0))
        with self.assertRaisesRegex(RuntimeFailure, "task_budget"):
            await budget.reserve_tool()
        self.assertEqual(budget.counts, (0, 4, 0))
        for field, value in (("model_limit", 4), ("tool_limit", 5), ("seconds", 46)):
            with self.assertRaises(Exception):
                TaskBudget(**{field: value})

    async def test_repeated_tool_selection_never_obtains_a_fourth_model_send(self):
        result = await self.run_candidate([
            native_tools((f"inspect_{index}", "inspect_trip", {})) for index in range(3)])
        self.assertEqual(result.state, "failed")
        self.assertIsNone(result.projection)
        self.assertEqual(result.counts, (3, 3, 2700))
        self.assertEqual(len(self.sent), 3)
        self.assertEqual(len(self.rows()), 3)

    async def test_original_parallel_path_is_explicitly_gated_not_silently_expanded(self):
        engine = TravelOrchestrator(self.bridge)
        with self.assertRaisesRegex(ExecutionError, "travel_parallel_gated"):
            await engine.run_parallel(request(), RoutingDecision(AgentType.TECHNICAL, [AgentType.BILLING]))
        self.assertEqual(self.sent, [])

    async def test_full_fusion_classifies_with_two_calls_without_chaining_agents(self):
        embedding = QwenIntentEmbedding(SCOPE.owner_id, "travel-intent-index-v1")
        recognizer = TravelIntentRecognizer(embedding)
        candidate = TravelAssistantCandidate(self.bridge, recognizer, mode=TravelMode.CLASSIFY)
        vectors = []
        for category, texts in recognizer.templates.items():
            for _ in texts:
                value = [0.0] * DIMENSIONS
                value[list(TravelIntent).index(category)] = 1.0
                vectors.append(value)
        gate = self.gate()
        with self.fixture.bind(PATH):
            async with self.client([]) as client:
                # Explicit controlled index preparation is separate and sends 0.
                with bind_execution(gate), bind_provider(self.settings, client, gate.budget):
                    embedding.load_prepared(recognizer.templates, vectors,
                        fingerprint=embedding.template_fingerprint(recognizer.templates),
                        model=EMBEDDING_MODEL, dimensions=DIMENSIONS)
        gate = self.gate()
        query = [0.0] * DIMENSIONS
        query[list(TravelIntent).index(TravelIntent.BUDGET)] = 1.0
        async def controlled(message):
            payload = json.loads(message.content)
            self.sent.append({"path": message.url.path, "bytes": len(message.content), "payload": payload})
            if message.url.path.endswith("/embeddings"):
                return httpx.Response(200, json={"object": "list", "model": EMBEDDING_MODEL, "data": [{
                    "index": 0, "object": "embedding", "embedding": query}]})
            return httpx.Response(200, json=native_text(json.dumps({"intent": "budget", "confidence": .95,
                                                                   "reasoning": "controlled classification"})))
        with self.fixture.bind(PATH):
            async with MeteredAsyncClient(transport=httpx.MockTransport(controlled)) as client:
                result = await candidate.run(request(message="费用是什么情况"), gate, self.settings, client)
        self.assertEqual(result.state, "classified")
        self.assertEqual(result.projection["intent"], "budget")
        self.assertEqual(result.counts, (2, 0, 256))
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.bridge._rows, [])
        self.assertTrue(all(agent.stats.total == 0 for pool in candidate.orchestrator._pool.values() for agent in pool))


if __name__ == "__main__":
    unittest.main()
