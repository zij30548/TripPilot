"""User-facing full-candidate scenarios, with CONTROLLED model responses only.

The real preserved fusion/router/Agent/tool/Composer methods execute over the
existing MockTransport fixture and a new temporary durable counter. Intent
labels, vectors and tool selections are scripted: these checks prove wiring and
business boundaries, NOT natural-language understanding or model quality.
There are no real credentials, external requests, service changes or user DBs.
"""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import unittest
from unittest.mock import patch

if any(importlib.util.find_spec(name) is None for name in ("jsonschema", "prometheus_client")):
    raise unittest.SkipTest("Explicit echomind extra required")

import httpx
from fastapi import FastAPI

from api.knowledge_answer_disabled import router as disabled_knowledge_router
from echomind_core.provider import TaskBudget
from echomind_core.travel_bridge import CLARIFICATIONS, TravelToolBridge, TrustedTravelSnapshot
from echomind_core.travel_budget import TravelBudgetTier, TravelTaskBudget
from echomind_core.travel_domain import TravelIntent
from echomind_core.upstream.agents.agent_orchestrator import AgentOrchestrator, ResponseComposer
from tests import test_echomind_travel_full_engine as full_fixture
from tests.test_chat_storage import intent
from tests.test_echomind_travel_bridge import capture
from tests.test_echomind_travel_engine import FABRICATED, native_text, native_tools


class FullTravelFunctionalAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    """Small scenario matrix, not another copy of the low-level protocol suite."""

    def setUp(self):
        # Reuse setup/prepare/run_full helpers, without inheriting and rerunning
        # the complete lower-level suite or replacing a production component.
        self.harness = full_fixture.FullTravelTests()
        self.harness.setUp()
        self.addCleanup(self.harness.doCleanups)

    async def _scenario(self, message, category, tool_name, arguments):
        before = deepcopy(self.harness.bridge.snapshot.payload())
        await self.harness.prepare()
        result, runtime = await self.harness.run_full(
            message=message, intent=category,
            tool=lambda _: native_tools(("controlled_scenario", tool_name, arguments)),
        )
        # No scenario may convert a model choice into a new authoritative
        # requirement, confirmed POI, adopted plan or persisted preference.
        self.assertEqual(self.harness.bridge.snapshot.payload(), before)
        self.assertEqual(result.versions["classification"]["source"], "original_fusion")
        self.assertNotIn(FABRICATED, result.message)
        self.assertEqual(result.counts[:2], (3, 1))
        self.assertEqual(len(self.harness.sent), 3)
        self.assertEqual(result.projection["tool_results"][0]["name"], tool_name)
        return result, runtime

    async def test_coordinator_missing_date_yields_program_clarification_not_an_itinerary(self):
        _, conversation, places, preferences = capture()
        conversation["requirements"]["start_date"] = None
        conversation["requirements"]["end_date"] = None
        snapshot = TrustedTravelSnapshot.capture(self.harness.binding, conversation, places, preferences)
        self.harness.bridge = TravelToolBridge(
            snapshot, lambda: self.harness.binding, parallel_reads=True,
        )
        self.assertIsNone(snapshot.payload()["requirements"]["start_date"])
        self.assertIsNone(snapshot.payload()["requirements"]["end_date"])
        result, _ = await self._scenario(
            "还没定日期，先告诉我需要补充什么", TravelIntent.CLARIFY,
            "travel_clarify", {"reason": "date_missing"},
        )
        self.assertEqual(result.role, "travel_coordinator")
        self.assertEqual(result.state, "clarification")
        self.assertEqual(result.message, CLARIFICATIONS["date_missing"])
        self.assertFalse(result.projection["tool_results"][0]["data"]["executed"])

    async def test_planning_role_day_query_preserves_browser_read_boundary(self):
        result, _ = await self._scenario(
            "查看第一天的安排", TravelIntent.SCHEDULE,
            "travel_schedule", {"action": "read_schedule", "date": None, "day": 1},
        )
        self.assertEqual(result.role, "travel_planning")
        self.assertEqual(result.state, "pending_browser")
        data = result.projection["tool_results"][0]["data"]
        self.assertEqual(data["action"], {"name": "read_schedule", "arguments": {"date": None, "day": 1}})
        self.assertFalse(data["executed"])
        self.assertTrue(data["browser_context_check_required"])
        self.assertIn("尚未执行", result.message)

    async def test_budget_role_does_not_present_server_inputs_as_a_computed_total(self):
        result, _ = await self._scenario(
            "核对当前全程费用，还有哪些没填", TravelIntent.BUDGET,
            "travel_budget", {"action": "read_budget"},
        )
        self.assertEqual(result.role, "travel_budget")
        self.assertEqual(result.state, "pending_browser")
        data = result.projection["tool_results"][0]["data"]
        self.assertEqual(data["action"], {"name": "read_budget", "arguments": {}})
        self.assertFalse(data["executed"])
        self.assertTrue(data["browser_context_check_required"])
        # The server has no formal browser schedule/cost snapshot here. These
        # checks intentionally do NOT label unknown costs as zero or claim the
        # existing cost calculator has run; frontend receipt tests own that.
        self.assertNotIn("total_cents", data)
        self.assertNotIn("remaining_budget_cents", data)

    async def test_place_role_confirmation_is_only_a_version_checked_proposal(self):
        result, _ = await self._scenario(
            "确认第1个地点加入必去", TravelIntent.PLACES,
            "travel_places", {"action": "confirm_place", "number": 1},
        )
        self.assertEqual(result.role, "travel_place_reference")
        self.assertEqual(result.state, "awaiting_confirmation")
        data = result.projection["tool_results"][0]["data"]
        self.assertEqual(data["action"], {"name": "confirm_place", "arguments": {"number": 1}})
        self.assertFalse(data["executed"])
        self.assertTrue(data["confirmation_required"])

    async def test_requirement_change_remains_a_draft_until_explicit_confirmation(self):
        result, _ = await self._scenario(
            "需求人数改为三人", TravelIntent.REQUIREMENTS,
            "update_trip_requirements", {"requirements": {**intent(), "travelers": 3},
                "sources": [{"field": "travelers", "quote": "三人"}], "issues": []},
        )
        self.assertEqual(result.role, "travel_coordinator")
        self.assertEqual(result.state, "awaiting_confirmation")
        data = result.projection["tool_results"][0]["data"]
        self.assertEqual(data["requirements"]["travelers"], 3)
        self.assertEqual(self.harness.bridge.snapshot.payload()["requirements"]["travelers"], 2)
        self.assertEqual(data["requirements"]["destination_id"], intent()["destination_id"])
        self.assertFalse(data["executed"])
        self.assertTrue(data["confirmation_required"])

    async def test_two_read_specialists_and_composer_share_one_real_reservation_budget(self):
        await self.harness.prepare()
        shared = self.harness.gate(TravelBudgetTier.COLLABORATION)
        self.harness.gate = lambda: shared
        stages, reservations = [], []
        original_parallel = AgentOrchestrator.run_parallel
        original_compose = ResponseComposer.compose_result
        original_model = TaskBudget.reserve_model
        original_embedding = TaskBudget.reserve_embedding
        original_tool = TaskBudget.reserve_tool

        async def parallel(runtime, req, decision):
            stages.append("original_parallel")
            return await original_parallel(runtime, req, decision)

        async def compose(composer, req, responses):
            stages.append("original_composer")
            return await original_compose(composer, req, responses)

        async def model(budget, tokens):
            reservations.append(("model", budget))
            return await original_model(budget, tokens)

        async def embedding(budget):
            reservations.append(("embedding", budget))
            return await original_embedding(budget)

        async def tool(budget):
            reservations.append(("tool", budget))
            return await original_tool(budget)

        with patch.object(AgentOrchestrator, "run_parallel", parallel), \
                patch.object(ResponseComposer, "compose_result", compose), \
                patch.object(TravelTaskBudget, "reserve_model", model), \
                patch.object(TravelTaskBudget, "reserve_embedding", embedding), \
                patch.object(TravelTaskBudget, "reserve_tool", tool):
            result, _ = await self.harness.run_full(
                message="查看当前行程和费用", intent=TravelIntent.SCHEDULE,
            )
        self.assertEqual(stages, ["original_parallel", "original_composer"])
        self.assertEqual(result.state, "pending_browser")
        self.assertEqual(result.counts[:2], (5, 2))
        self.assertEqual(shared.budget.counts, result.counts)
        self.assertEqual(len(reservations), 7)
        self.assertTrue(all(budget is shared.budget for _, budget in reservations))
        results = result.projection["tool_results"]
        self.assertEqual({row["name"] for row in results}, {"travel_schedule", "travel_budget"})
        self.assertTrue(all(row["data"]["executed"] is False for row in results))
        self.assertEqual({row["data"]["action"]["name"] for row in results}, {"read_schedule", "read_budget"})
        self.assertNotIn(FABRICATED, result.message)
        self.assertFalse(result.versions["composer_fact_authority"])

    async def test_missing_real_preparation_does_not_start_a_cold_paid_branch(self):
        # Synthetic fixture preparation is deliberately NOT called. An absent
        # real artifact must fail before even the classifier can send.
        result, _ = await self.harness.run_full(message="核对费用", intent=TravelIntent.BUDGET)
        self.assertEqual(result.state, "failed")
        self.assertEqual(result.code, "embedding_preparation_required")
        self.assertIsNone(result.projection)
        self.assertEqual(result.counts, (0, 0, 0))
        self.assertEqual(self.harness.sent, [])

    async def test_model_free_factual_answer_is_not_a_tool_backed_business_result(self):
        await self.harness.prepare()
        result, _ = await self.harness.run_full(
            message="核对费用", intent=TravelIntent.BUDGET,
            tool=lambda _: native_text(FABRICATED),
        )
        self.assertEqual(result.state, "failed")
        self.assertEqual(result.code, "travel_tool_required")
        self.assertIsNone(result.projection)
        self.assertNotIn(FABRICATED, result.message)
        self.assertEqual(result.counts[:2], (3, 0))

    async def test_knowledge_answer_remains_disabled_even_beside_full_candidate(self):
        # Execute the actual registered disabled handler, without identity or
        # storage fixtures. Production middleware isolation has its own suite.
        app = FastAPI()
        app.include_router(disabled_knowledge_router)
        with patch("tooling.providers.qwen.QwenClient.generate", side_effect=AssertionError("Model forbidden")) as generate:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://controlled.test") as client:
                response = await client.post("/knowledge/answer?enabled=true", json={"enabled": True})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["code"], "knowledge_answer_disabled")
        generate.assert_not_called()
        self.assertEqual(self.harness.sent, [])


if __name__ == "__main__":
    unittest.main()
