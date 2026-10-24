"""Finite role leaves and complete local evidence; no network or paid calls."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date
import importlib.util
import socket
import threading
import unittest
from unittest.mock import patch

if any(importlib.util.find_spec(name) is None for name in ("jsonschema", "prometheus_client")):
    raise unittest.SkipTest("Travel capability checks require the explicit echomind extra")

from core.llm_utils import RuntimeFailure, validate_arguments
from core.schemas.dialogue import ACTION_FIELDS, GROUPS, TOOL_VERSION
from echomind_core.execution import ExecutionError, ExecutionGate, GuardedToolRegistry, SafeToolManager, ToolDeclaration, bind_execution
from echomind_core.provider import TaskBudget
from echomind_core.travel_bridge import NO_EXTRA_CONFIRMATION, TravelToolBridge
from echomind_core.travel_capabilities import (
    MATERIAL_VERSION, ROLE_ACTIONS, SOURCE_VERSION, Effect, ReviewedKnowledgePort,
    capability_registry, make_role_tools, validate_role_action,
)
from echomind_core.travel_domain import TravelRole
from echomind_core.upstream.tooling.tool_manager import Tool
from tests.test_echomind_travel_bridge import BINDING, capture, request
from tests.test_knowledge_national import controlled_index


class CapabilityRegistryTests(unittest.TestCase):
    def test_exact_38_original_leaves_and_10_groups_no_replacement_algorithm(self):
        rows = capability_registry()
        self.assertEqual(len(rows), 38)
        self.assertEqual({row.name for row in rows}, set(ACTION_FIELDS))
        self.assertEqual({row.group for row in rows}, set(GROUPS))
        self.assertEqual(len({row.name for row in rows}), len(rows))
        self.assertEqual({row.effect for row in rows}, set(Effect))
        for row in rows:
            self.assertEqual(row.tool_version, TOOL_VERSION)
            self.assertEqual(row.execution_port, "browser_proposal_only")
            self.assertEqual(row.confirmation_required, row.name not in NO_EXTRA_CONFIRMATION)
            self.assertTrue(row.skills)

    def test_roles_are_explicit_finite_and_metadata_is_immutable(self):
        for role in TravelRole:
            self.assertEqual({row.name for row in capability_registry(role)}, set(ROLE_ACTIONS[role]))
        with self.assertRaises(TypeError):
            ROLE_ACTIONS[TravelRole.BUDGET] = frozenset(ACTION_FIELDS)
        with self.assertRaises((AttributeError, TypeError)):
            capability_registry()[0].name = "forged"
        for value in ("travel_budget", None, object()):
            with self.assertRaises(ExecutionError):
                validate_role_action(value, "travel_budget", {"action": "read_budget"})

    def test_group_and_action_both_checked_not_only_group_name(self):
        selected = validate_role_action(TravelRole.PLANNING, "travel_places", {
            "action": "exclude_candidate", "selector": "1"})
        self.assertEqual(selected, {"action": "exclude_candidate", "selector": "1"})
        for role, group, arguments in (
            (TravelRole.PLANNING, "travel_places", {"action": "confirm_place", "number": 1}),
            (TravelRole.BUDGET, "travel_schedule", {"action": "generate_schedule"}),
            (TravelRole.PLACE_REFERENCE, "travel_views", {"action": "explain_schedule"}),
            (TravelRole.COORDINATOR, "travel_budget", {"action": "generate_schedule"}),
            (TravelRole.COORDINATOR, "arbitrary_remote_tool", {}),
        ):
            with self.subTest(role=role, group=group, arguments=arguments), self.assertRaises(ExecutionError):
                validate_role_action(role, group, arguments)

    def test_strict_original_args_reject_authority_url_extra_bool_and_invalid_date(self):
        for arguments in (
            {"action": "read_budget", "owner_id": "forged"},
            {"action": "read_budget", "input_version": 1},
            {"action": "read_budget", "url": "https://example.test/arbitrary"},
            {"action": "set_cost", "kind": "meal", "selector": None, "value": "0"},
            {"action": "read_schedule", "date": "2026-02-30", "day": None},
            {"action": "read_schedule", "date": "2026-10-24", "day": 1},
        ):
            group = "travel_schedule" if arguments["action"] == "read_schedule" else "travel_budget"
            with self.subTest(arguments=arguments), self.assertRaises(ExecutionError):
                validate_role_action(TravelRole.COORDINATOR, group, arguments)
        with self.assertRaises(ExecutionError):
            validate_role_action(TravelRole.PLACE_REFERENCE, "travel_places", {"action": "confirm_place", "number": True})


class RoleToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.current = BINDING
        self.snapshot, *_ = capture()
        self.bridge = TravelToolBridge(self.snapshot, lambda: self.current)
        self.index = controlled_index()
        self.port = ReviewedKnowledgePort(self.index)
        self.no_network = patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden"))
        self.no_network.start()
        self.addCleanup(self.no_network.stop)

    def gate(self):
        return ExecutionGate(BINDING.scope, lambda: self.current.scope, TaskBudget())

    async def call(self, role, name, arguments, *, national=None):
        spec = make_role_tools(self.bridge, role, national=national)[name]
        wrapped = GuardedToolRegistry((ToolDeclaration(spec, True),)).wrap(spec)
        with bind_execution(self.gate()):
            return await wrapped.handler(request(), arguments)

    async def test_subset_schema_is_visible_and_execution_rechecks_even_after_schema_mutation(self):
        tools = make_role_tools(self.bridge, TravelRole.PLANNING)
        allowed = tools["travel_places"].input_schema["properties"]["action"]["enum"]
        self.assertEqual(allowed, ["prepare_candidates", "exclude_candidate", "restore_candidate"])
        self.assertNotIn("travel_budget", tools)
        forged = {"action": "confirm_place", "number": 1}
        with self.assertRaises(RuntimeFailure):
            validate_arguments(forged, tools["travel_places"].input_schema)
        # A consumer changing its schema cannot expand the frozen handler grant.
        tools["travel_places"].input_schema.clear()
        with bind_execution(self.gate()), self.assertRaises(ExecutionError):
            await tools["travel_places"].handler(request(), forged)
        self.assertEqual((await self.bridge.project()).tool_results, [])

    async def test_existing_browser_proposal_not_schedule_or_write(self):
        result = await self.call(TravelRole.PLANNING, "travel_schedule", {"action": "generate_schedule"})
        row = (await self.bridge.project()).tool_results[0]["data"]
        self.assertEqual(row["action"], {"name": "generate_schedule", "arguments": {}})
        self.assertFalse(row["executed"])
        self.assertTrue(row["browser_context_check_required"])
        self.assertTrue(row["confirmation_required"])
        self.assertIn("尚未执行", result["message"])

    async def test_extra_authority_never_reaches_original_handler(self):
        with self.assertRaises((RuntimeFailure, ExecutionError)):
            await self.call(TravelRole.BUDGET, "travel_budget", {"action": "read_budget", "owner_id": "b"})
        self.assertEqual((await self.bridge.project()).tool_results, [])

    async def test_role_views_and_requirements_do_not_grant_other_actions(self):
        places = make_role_tools(self.bridge, TravelRole.PLACE_REFERENCE)
        self.assertEqual(places["travel_views"].input_schema["properties"]["action"]["enum"], ["open_photo", "open_video"])
        self.assertNotIn("update_trip_requirements", places)
        self.assertNotIn("read_preferences", places)
        budget = make_role_tools(self.bridge, TravelRole.BUDGET)
        self.assertNotIn("travel_places", budget)
        self.assertNotIn("search_materials", budget)
        self.assertNotIn("search_reviewed_materials", make_role_tools(self.bridge, TravelRole.BUDGET, national=self.port))

    async def test_old_material_tool_and_contract_unchanged_new_port_is_explicit(self):
        old = self.bridge.tools()["search_materials"]
        mapped = make_role_tools(self.bridge, TravelRole.PLACE_REFERENCE)["search_materials"]
        self.assertEqual(old.input_schema, mapped.input_schema)
        self.assertNotIn("search_reviewed_materials", make_role_tools(self.bridge, TravelRole.COORDINATOR))
        self.assertIn("search_reviewed_materials", make_role_tools(self.bridge, TravelRole.PLACE_REFERENCE, national=self.port))

    async def test_full_material_result_is_recorded_and_visible_without_answer_generation(self):
        result = await self.call(TravelRole.PLACE_REFERENCE, "search_reviewed_materials", {
            "city_id": "310000", "venue_id": "shanghai-museum", "question": "儿童免票陪同", "limit": 2,
            "visit_date": "2026-10-24"}, national=self.port)
        projection = await self.bridge.project()
        row = projection.tool_results[0]["data"]
        self.assertEqual(row["kind"], "read")
        self.assertTrue(row["executed"])
        self.assertTrue(row["retrieval_only"])
        self.assertEqual(row["material_version"], MATERIAL_VERSION)
        self.assertEqual(row["retrieval"]["request"]["venue_id"], "shanghai-museum")
        self.assertIn("AI问答未开放", result["message"])
        for match in row["retrieval"]["matches"]:
            self.assertIn(match["section"]["summary"], result["message"])
            self.assertIn(match["section"]["excerpt"], result["message"])
            self.assertIn(match["source"]["url"], result["message"])
            self.assertIn(match["source"]["fetched_at"], result["message"])
        self.assertNotIn("binding", result)
        self.assertNotIn(BINDING.scope.owner_id, str(result))
        self.assertEqual(projection.state, "read_complete")

    async def test_expired_bridge_refuses_new_read_and_no_result_is_published(self):
        self.bridge.invalidate()
        with self.assertRaises(ExecutionError):
            await self.call(TravelRole.PLACE_REFERENCE, "search_reviewed_materials", {
                "city_id": "310000", "venue_id": "shanghai-museum", "question": "儿童"}, national=self.port)
        self.assertEqual(self.bridge._rows, [])

    async def test_material_read_late_completion_cannot_publish_old_workspace(self):
        entered, release = threading.Event(), threading.Event()
        original = self.port.read

        def delayed(arguments):
            entered.set()
            if not release.wait(3):
                raise AssertionError("controlled thread was not released")
            return original(arguments)

        with patch.object(self.port, "read", side_effect=delayed):
            task = asyncio.create_task(self.call(TravelRole.PLACE_REFERENCE, "search_reviewed_materials", {
                "city_id": "310000", "venue_id": "shanghai-museum", "question": "儿童"}, national=self.port))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                self.bridge.invalidate()
            finally:
                release.set()
            with self.assertRaises(ExecutionError):
                await task
        self.assertEqual(self.bridge._rows, [])


class ReviewedMaterialTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.index = controlled_index()
        self.port = ReviewedKnowledgePort(self.index)
        self.args = {"city_id": "310000", "venue_id": "shanghai-museum", "question": "儿童免票陪同"}
        self.no_network = patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden"))
        self.no_network.start()
        self.addCleanup(self.no_network.stop)

    def test_complete_unit_source_versions_dates_and_conditions_preserved(self):
        result = self.port.read(self.args)
        self.assertEqual(result["schema_version"], MATERIAL_VERSION)
        self.assertEqual(result["source_schema_version"], SOURCE_VERSION)
        self.assertTrue(result["retrieval_only"])
        self.assertEqual(result["status"], "matches")
        hit = result["retrieval"]["matches"][0]
        for condition in ("一名成人全程陪同", "未满6周岁", "但不含另行收费项目", "法定节假日除外"):
            self.assertIn(condition, hit["section"]["summary"])
        self.assertIsNone(hit["source"]["published_at"])
        self.assertEqual(hit["source"]["fetched_at"], "2026-10-24T00:00:00Z")
        self.assertEqual(result["retrieval"]["corpus_hash"], self.port.corpus_hash)
        self.assertEqual(hit["applicability_check"]["status"], "no_visit_date")
        self.assertEqual(hit, self.index.search(self.port._request(self.args)).model_dump(mode="json")["matches"][0])

    def test_no_match_does_not_generate_price_free_entry_or_answer(self):
        result = self.port.read({**self.args, "question": "量子发动机"})
        self.assertEqual(result["status"], "no_match")
        self.assertEqual(result["retrieval"]["matches"], [])
        self.assertNotIn("answer", result)

    def test_historical_reference_and_real_date_warning_not_updated_to_now(self):
        result = self.port.read({**self.args, "question": "历史现场取票", "visit_date": "2026-10-24"})
        historical = next(hit for hit in result["retrieval"]["matches"] if hit["source"]["kind"] == "historical")
        self.assertEqual(historical["source"]["published_at"], "2025-01-01")
        self.assertEqual(historical["applicability_check"]["status"], "historical_reference")

    def test_wrong_city_venue_unknown_source_and_extra_authority_are_rejected(self):
        for changed in (
            {"city_id": "110000"}, {"venue_id": "beijing-museum"}, {"city_id": "999999"},
            {"venue_id": "unknown"}, {"owner_id": "b"}, {"url": "https://example.test/"},
            {"corpus_hash": "0" * 64}, {"question": "a"}, {"question": "a" * 161},
            {"limit": 6}, {"limit": True}, {"visit_date": "2026-02-30"},
        ):
            with self.subTest(changed=changed), self.assertRaises(ExecutionError):
                self.port.read({**self.args, **changed})

    def test_response_structure_scope_source_and_complete_text_are_revalidated(self):
        baseline = self.port._index.search(self.port._request(self.args))
        for mutate in (
            lambda value: setattr(value, "corpus_hash", "0" * 64),
            lambda value: setattr(value.matches[0].section, "summary", "改写为全部免费"),
            lambda value: setattr(value.matches[0].source, "url", "https://elsewhere.example.test/"),
            lambda value: setattr(value.matches[0].source, "published_at", date(2026, 1, 1)),
            lambda value: setattr(value.matches[0], "content_hash", "0" * 64),
            lambda value: setattr(value, "status", "no_match"),
            lambda value: value.__dict__.update(unknown_field="unsafe"),
            lambda value: value.matches[0].source.__dict__.update(unknown_field="unsafe"),
        ):
            forged = baseline.model_copy(deep=True)
            mutate(forged)
            # Pydantic assignment/model_copy are not a fresh validation boundary.
            with self.subTest(mutate=mutate), patch.object(self.port._index, "search", return_value=forged), self.assertRaises(ExecutionError):
                self.port.read(self.args)

    def test_constructor_has_independent_registry_and_result_copies(self):
        expected = self.port.read(self.args)
        self.index.corpus.documents[0].sections[0].summary = "外部修改"
        expected["retrieval"]["matches"][0]["section"]["summary"] = "结果修改"
        actual = self.port.read(self.args)
        self.assertNotIn("外部修改", str(actual))
        self.assertNotIn("结果修改", str(actual))
        self.port._index.corpus.documents[0].sections[0].summary = "内部版本漂移"
        with self.assertRaises(ExecutionError):
            self.port.read(self.args)

    async def test_original_tool_manager_callback_keeps_trusted_venue_and_full_source(self):
        recall = self.port.bound_recall("310000", "shanghai-museum", date(2026, 10, 24))
        gate = ExecutionGate(BINDING.scope, lambda: BINDING.scope, TaskBudget())
        context = {"owner_id": gate.scope.owner_id, "conversation_id": gate.scope.conversation_id,
            "session_id": gate.scope.session_id, "input_version": gate.scope.input_version}
        with bind_execution(gate):
            rows = await recall({"query": "北京儿童预约", "top_k": 5}, context)
        self.assertTrue(rows)
        self.assertTrue(all(row["source"]["venue_id"] == "shanghai-museum" for row in rows))
        self.assertTrue(all(row["content"] == row["section"]["summary"] for row in rows))
        self.assertTrue(all(row["retrieval_only"] for row in rows))
        self.assertTrue(all(row["corpus_hash"] == self.port.corpus_hash for row in rows))
        self.assertEqual(gate.budget.counts, (0, 0, 0))  # Owning manager reserves, not a duplicate counter here.

    async def test_bound_callback_rejects_extra_scope_forgery_and_late_context(self):
        recall = self.port.bound_recall("310000", "shanghai-museum")
        gate = ExecutionGate(BINDING.scope, lambda: BINDING.scope, TaskBudget())
        context = {"owner_id": gate.scope.owner_id, "conversation_id": gate.scope.conversation_id,
            "session_id": gate.scope.session_id, "input_version": gate.scope.input_version}
        with bind_execution(gate):
            for args, ctx in (
                ({"query": "儿童", "top_k": 5, "venue_id": "beijing-museum"}, context),
                ({"query": "儿童", "top_k": 5}, {**context, "owner_id": "b"}),
            ):
                with self.assertRaises(ExecutionError):
                    await recall(args, ctx)
            gate.invalidate()
            with self.assertRaises(ExecutionError):
                await recall({"query": "儿童", "top_k": 5}, context)

    async def test_callback_no_bound_execution_cannot_read(self):
        recall = self.port.bound_recall("310000", "shanghai-museum")
        with self.assertRaises(ExecutionError):
            await recall({"query": "儿童", "top_k": 5}, {})

    async def test_existing_safe_tool_manager_executes_and_caches_bound_local_port(self):
        manager = SafeToolManager()
        manager.register_read_only(Tool("knowledge_search", "local reviewed material", self.port.bound_recall(
            "310000", "shanghai-museum"), {"type": "object", "properties": {
                "query": {"type": "string", "minLength": 2, "maxLength": 160},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 5}},
                "required": ["query", "top_k"], "additionalProperties": False}, cache_ttl=60))
        gate = ExecutionGate(BINDING.scope, lambda: BINDING.scope, TaskBudget())
        with bind_execution(gate):
            result = await manager.call("knowledge_search", {"query": "儿童免票陪同", "top_k": 5})
            cached = await manager.call("knowledge_search", {"query": "儿童免票陪同", "top_k": 5})
        self.assertTrue(result.success)
        self.assertTrue(cached.cached)
        self.assertTrue(result.data)
        self.assertEqual(result.data, cached.data)
        self.assertEqual(result.data[0]["content"], result.data[0]["section"]["summary"])
        self.assertEqual(gate.budget.counts, (0, 1, 0))


if __name__ == "__main__":
    unittest.main()
