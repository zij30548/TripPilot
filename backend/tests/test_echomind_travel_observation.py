"""Real original stats/monitor, controlled Agents only: zero external requests."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import gc
import importlib.util
import json
import unittest
from unittest.mock import patch
import weakref

if any(importlib.util.find_spec(name) is None for name in ("jsonschema", "prometheus_client")):
    raise unittest.SkipTest("Travel observation checks require the explicit echomind extra")

from prometheus_client import REGISTRY

from echomind_core.execution import ExecutionError
from echomind_core.provider import MODEL
from echomind_core.travel_domain import TravelIntent
from echomind_core.travel_observation import TravelObservationState
from echomind_core.travel_routing import ROLE_FOR_SLOT, TravelRoutingPolicy
from echomind_core.upstream.agents.agent_orchestrator import (
    AgentCallResult, AgentOrchestrator, AgentProfile, AgentType, BaseAgent, Request,
)
from echomind_core.upstream.agents.tools import AgentToolSpec
from echomind_core.upstream.core.intent_recognizer import UrgencyLevel
from echomind_core.upstream.monitor.performance_monitor import PerformanceMonitor
from echomind_core.upstream.tooling.tool_manager import MCPToolManager


class PrivateClient:
    pass


class ControlledAgent(BaseAgent):
    agent_type = AgentType.GENERAL
    fail = False

    async def _call_llm(self, captured):
        if self.fail:
            raise RuntimeError("private failure text must not enter summary")
        return AgentCallResult("controlled technical success, not a travel-quality claim")


class Harness(TravelRoutingPolicy, AgentOrchestrator):
    def __init__(self):
        self._pool = {}
        for slot in AgentType:
            agent = ControlledAgent(PrivateClient(), MODEL, profile=AgentProfile(
                role=ROLE_FOR_SLOT[slot].value, mission="private mission", workflow=("private workflow",),
                input_contract=(), output_contract=()))
            agent.agent_type = slot
            agent.set_shared_tools({"inspect_trip": AgentToolSpec("inspect_trip", "private description",
                {"type": "object"}, lambda *_: {"private": "result"})})
            self._pool[slot] = [agent]


class ToolManager(MCPToolManager):
    def __init__(self):
        self.private_client = PrivateClient()
        self.rows = {"knowledge_search": {"total": 1, "success_rate": 1.0, "avg_latency_ms": 12.0,
                                          "consecutive_fails": 0, "circuit_state": "closed"}}

    def get_stats(self):
        return deepcopy(self.rows)


def request():
    return Request("查看当前行程", "private-owner", "private-conversation",
                   intent=TravelIntent.SCHEDULE, intent_group="schedule", urgency=UrgencyLevel.LOW)


class TravelObservationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.state = TravelObservationState()
        self.addAsyncCleanup(self.state.close)

    async def test_original_monitor_collects_and_penalizes_next_fresh_orchestrator(self):
        first = Harness()
        self.state.attach(first)
        specialist = first._pool[AgentType.TECHNICAL][0]
        specialist.fail = True
        for _ in range(5):
            await specialist.handle(request())
        original, calls = PerformanceMonitor._collect, []

        async def observed(monitor):
            calls.append(monitor)
            await original(monitor)

        with patch.object(PerformanceMonitor, "_collect", observed):
            await self.state.collect_once()
        self.assertEqual(calls, [self.state._monitor])
        self.assertGreaterEqual(specialist.stats.monitor_penalty, .5)
        second = Harness()
        self.state.attach(second)
        self.assertIs(second._pool[AgentType.TECHNICAL][0].stats, specialist.stats)
        decision = second._route_decision(request())
        self.assertIs(decision.primary_agent, AgentType.GENERAL)
        summary = self.state.summary()
        stats = summary["agent_stats"][ROLE_FOR_SLOT[AgentType.TECHNICAL].value]
        self.assertEqual(stats["total"], 5)
        self.assertEqual(stats["success_rate"], 0)
        self.assertEqual(summary["attachment_count"], 2)

    async def test_stats_survive_new_agents_without_sharing_clients_or_other_private_objects(self):
        first = Harness()
        self.state.attach(first)
        before_client = first._pool[AgentType.GENERAL][0]._client
        await first._pool[AgentType.GENERAL][0].handle(request())
        second = Harness()
        self.state.attach(second)
        self.assertIsNot(second._pool[AgentType.GENERAL][0]._client, before_client)
        self.assertEqual(second._pool[AgentType.GENERAL][0].stats.total, 1)
        text = json.dumps(self.state.summary(), ensure_ascii=False)
        for private in ("private-owner", "private-conversation", "private mission", "private workflow",
                        "private description", "private failure text", "查看当前行程"):
            self.assertNotIn(private, text)

    async def test_observer_retains_no_orchestrator_agent_manager_or_private_client(self):
        engine, manager = Harness(), ToolManager()
        agent = engine._pool[AgentType.GENERAL][0]
        refs = [weakref.ref(value) for value in (engine, manager, agent, agent._client, manager.private_client)]
        self.state.attach(engine, manager)
        await self.state.collect_once(manager)
        del engine, manager, agent
        gc.collect()
        self.assertTrue(all(reference() is None for reference in refs))

    async def test_private_registry_and_explicit_background_lifecycle_only(self):
        default_before = set(REGISTRY._names_to_collectors)
        second = TravelObservationState(interval_seconds=.05)
        try:
            self.assertFalse(second.summary()["running"])
            self.assertIsNone(second._monitor._task)
            self.assertIsNone(second._monitor._prometheus_port)
            self.assertIsNone(second._monitor._webhook)
            self.assertIsNot(second._registry, REGISTRY)
            await second.start()
            running = second._monitor._task
            await second.start()
            self.assertIs(second._monitor._task, running)
            await asyncio.sleep(0)
            self.assertTrue(second.summary()["running"])
            await second.stop()
            self.assertTrue(running.done())
            self.assertFalse(second.summary()["running"])
            await second.start()
            await second.close()
            self.assertTrue(second.summary()["closed"])
        finally:
            await second.close()
        self.assertEqual(set(REGISTRY._names_to_collectors), default_before)

    async def test_tool_snapshot_is_bounded_sanitized_detached_and_not_claimed_cumulative(self):
        engine, manager = Harness(), ToolManager()
        self.state.attach(engine, manager)
        manager.rows["knowledge_search"]["total"] = 2
        manager.rows["knowledge_search"]["avg_latency_ms"] = 25
        await self.state.collect_once(manager)
        summary = self.state.summary()
        self.assertEqual(summary["tool_stats"]["knowledge_search"]["total"], 2)
        self.assertEqual(summary["tool_stats_scope"], "latest_sanitized_snapshot")
        manager.rows.clear()
        self.assertEqual(self.state.summary()["tool_stats"], summary["tool_stats"])
        summary["tool_stats"].clear()
        self.assertTrue(self.state.summary()["tool_stats"])

    async def test_invalid_tool_fields_cannot_enter_summary_or_mutate_agent_pool(self):
        for changes in ({"input": "private prompt"}, {"total": True}, {"success_rate": float("nan")},
                        {"avg_latency_ms": float("inf")}, {"consecutive_fails": 2}, {"circuit_state": "private"}):
            with self.subTest(changes=changes):
                engine, manager = Harness(), ToolManager()
                manager.rows["knowledge_search"].update(changes)
                before = [agents[0].stats for agents in engine._pool.values()]
                with self.assertRaisesRegex(ExecutionError, "travel_observation_invalid"):
                    self.state.attach(engine, manager)
                self.assertEqual([agents[0].stats for agents in engine._pool.values()], before)
        manager = ToolManager()
        manager.rows["bad name with secret"] = manager.rows.pop("knowledge_search")
        with self.assertRaisesRegex(ExecutionError, "travel_observation_invalid"):
            await self.state.collect_once(manager)

    async def test_all_pool_fields_are_prevalidated_before_any_stats_attach(self):
        engine = Harness()
        before = {slot: agents[0].stats for slot, agents in engine._pool.items()}
        engine._pool[AgentType.ESCALATION][0].profile = AgentProfile(
            role="customer escalation", mission="", workflow=(), input_contract=(), output_contract=())
        with self.assertRaisesRegex(ExecutionError, "travel_observation_invalid"):
            self.state.attach(engine)
        for slot, agents in engine._pool.items():
            self.assertIs(agents[0].stats, before[slot])

    async def test_attach_cannot_silently_discard_preexisting_work(self):
        engine = Harness()
        await engine._pool[AgentType.GENERAL][0].handle(request())
        with self.assertRaisesRegex(ExecutionError, "travel_observation_invalid"):
            self.state.attach(engine)

    async def test_duplicate_attach_reuses_stats_instead_of_resetting_cooldown(self):
        engine = Harness()
        self.state.attach(engine)
        stats = engine._pool[AgentType.ESCALATION][0].stats
        stats.set_penalty(.9)
        cooldown = stats.retry_at
        self.state.attach(engine)
        self.assertIs(engine._pool[AgentType.ESCALATION][0].stats, stats)
        self.assertEqual(stats.retry_at, cooldown)

    async def test_closed_state_rejects_new_work_and_close_is_idempotent(self):
        self.state.attach(Harness())
        await self.state.close()
        await self.state.close()
        for operation in (self.state.collect_once, self.state.start):
            with self.assertRaisesRegex(ExecutionError, "travel_observation_closed"):
                await operation()
        with self.assertRaisesRegex(ExecutionError, "travel_observation_closed"):
            self.state.attach(Harness())
        self.assertTrue(all(agents[0].stats.observer is None for agents in self.state._pool._pool.values()))

    async def test_alerts_are_bounded_and_summary_json_contains_only_technical_data(self):
        engine = Harness()
        self.state.attach(engine)
        stats = engine._pool[AgentType.BILLING][0].stats
        stats.total, stats.success = 5, 0
        for _ in range(5):
            stats.record(False, 50)
        for _ in range(120):
            await self.state.collect_once()
        self.assertEqual(len(self.state._monitor._alerts), 100)
        summary = self.state.summary()
        self.assertLessEqual(len(summary["active_alerts"]), 10)
        self.assertEqual(summary["semantic_quality"], "not_assessed")
        self.assertEqual(summary["persistence"], "none")
        json.dumps(summary, allow_nan=False)

    async def test_process_mismatch_refuses_inherited_state(self):
        with patch("echomind_core.travel_observation.os.getpid", return_value=self.state._pid + 1):
            with self.assertRaisesRegex(ExecutionError, "travel_observation_closed"):
                self.state.attach(Harness())

    async def test_cross_event_loop_collection_is_rejected(self):
        await self.state.collect_once()

        def another_loop():
            with self.assertRaisesRegex(ExecutionError, "travel_observation_invalid"):
                asyncio.run(self.state.collect_once())

        await asyncio.to_thread(another_loop)

    async def test_invalid_intervals_do_not_construct_monitor(self):
        for value in (True, 0, .001, 61, float("nan"), "10"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ExecutionError, "travel_observation_invalid"):
                    TravelObservationState(interval_seconds=value)


if __name__ == "__main__":
    unittest.main()
