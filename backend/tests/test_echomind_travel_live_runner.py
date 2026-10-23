"""Offline controlled checks of the live-fixture API driver, not live grades."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

from tools.echomind_travel_controlled import ControlledError, initialize, native_text, native_tool
from tools.echomind_travel_live_runner import (
    ORIGIN, NativeSession, SyntheticBusinessUpstream, TurnBinding, digest, fixture_app,
)


class FixturePolicyTests(unittest.TestCase):
    def test_public_binding_has_no_owner_or_question_text(self):
        binding = TurnBinding("m01", str(uuid4()), "private-owner", str(uuid4()), str(uuid4()),
            0, 0, "private-question", *(["a" * 64] * 7), b"{}")
        public = binding.public()
        self.assertNotIn("private-owner", str(public))
        self.assertNotIn("private-question", str(public))
        self.assertRegex(binding.sha256, r"^[a-f0-9]{64}$")
        public["revision"] = 50
        self.assertEqual(binding.revision, 0)
        self.assertNotEqual(digest(public), binding.sha256)

    def test_business_peer_never_scripts_a_model_answer(self):
        async def run():
            upstream = SyntheticBusinessUpstream()
            for url in ("https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                        "https://example.com/anything"):
                with self.assertRaisesRegex(ControlledError, "non_business"):
                    await upstream.handle(httpx.Request("POST", url, json={}))
            self.assertEqual(upstream.events, [])
        asyncio.run(run())


class NativeFixtureMySQLTests(unittest.TestCase):
    def setUp(self):
        if any(importlib.util.find_spec(name) is None for name in ("jsonschema", "prometheus_client")):
            self.skipTest("requires explicit optional candidate dependencies")
        from tests.mysql_support import temporary_mysql
        from core.config import Settings
        from core.usage_runtime import MeteredAsyncClient
        from fastapi.testclient import TestClient
        from tools.echomind_travel_controlled import ControlledUpstream
        self.engine, cleanup = temporary_mysql()
        self.addCleanup(cleanup)
        folder = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="live-fixture-"))).resolve()
        self.anchor = folder / "usage.identity"
        self.anchor.write_text(uuid4().hex + "\n", encoding="ascii")
        self.anchor.chmod(0o600)
        initialize(self.engine, self.anchor)
        self.peer = ControlledUpstream()
        self.observed = []
        self.runner = None

        async def dependency():
            async def request_hook(_request):
                if self.runner is not None:
                    self.observed.append(self.runner.current_binding())
            async with MeteredAsyncClient(transport=httpx.MockTransport(self.peer.handle), follow_redirects=False,
                    trust_env=False, event_hooks={"request": [request_hook]}) as client:
                yield client

        settings = Settings(_env_file=None, qwen_api_key="controlled-not-real", qwen_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                            assistant_runtime="echomind_travel_v1")
        self.app, self.business = self.enterContext(fixture_app(self.engine, self.anchor, settings=settings, qwen_dependency=dependency))
        self.client = self.enterContext(TestClient(self.app))
        self.runner = NativeSession(self.client, self.engine)

    def test_native_clarification_normal_cookie_csrf_mysql_and_exact_client(self):
        self.peer.responses = [native_tool("travel_clarify", {"reason": "destination_missing"}), native_text()]
        result = self.runner.submit("m01")
        self.assertEqual(result["http_status"], 200, result)
        self.assertEqual(result["readback"]["last_turn"]["state"], "succeeded")
        self.assertEqual(result["task_state"]["task"]["model_calls"], 1)
        self.assertEqual(self.observed, [result["binding_sha256"]])
        self.assertEqual(len(self.peer.responses), 1)  # Unsent model text is not required to finish.
        self.assertFalse(result["browser_execution_verified"])
        self.assertEqual(self.business.events, [])
        self.assertIsNone(result["readback"]["confirmed_revision"])
        self.assertIsNone(self.runner.active)

    def test_duplicate_same_operation_never_reaches_upstream_again(self):
        self.peer.responses = [native_tool("travel_clarify", {"reason": "date_missing"}), native_text()]
        self.runner.submit("m01")
        count = len(self.peer.events)
        with self.assertRaisesRegex(ControlledError, "duplicate"):
            self.runner.submit("m01")
        self.assertEqual(len(self.peer.events), count)

    def test_failed_m02_cannot_be_replaced_with_synthetic_success_for_m03(self):
        self.peer.responses = [native_tool("unknown_tool", {})]
        result = self.runner.submit("m02")
        self.assertEqual(result["http_status"], 502)
        with self.assertRaisesRegex(ControlledError, "dependency"):
            self.runner.submit("m03")
        self.assertNotIn("m03", self.runner.attempted)
        self.assertEqual(len(self.peer.events), 1)

    def test_formal_and_local_operations_not_faked_by_native_driver(self):
        for case in ("m04", "m05", "m06", "m07"):
            with self.subTest(case=case), self.assertRaisesRegex(ControlledError, "formal_browser|dependency"):
                self.runner.submit(case)
        for case in ("l01", "l02", "m08", "unknown"):
            with self.assertRaisesRegex(ControlledError, "unplanned"):
                self.runner.submit(case)
        self.assertEqual(self.peer.events, [])

    def test_selection_is_validated_before_operation_is_consumed(self):
        self.runner.conversations["formal"] = self.runner.conversation("empty")
        for selection in ({"workspace_version": "not-uuid", "display": "none", "items": []},
                          {"workspace_version": str(uuid4()), "display": "none", "items": [], "owner_id": "other"}):
            with self.assertRaises(ValueError):
                self.runner.submit("m04", selection=selection)
        self.assertNotIn("m04", self.runner.attempted)
        self.assertEqual(self.peer.events, [])

    def test_context_change_during_request_rejects_before_mock_transport(self):
        from memory.chat_storage import ChatStore
        conversation = self.runner.conversation("empty")
        original = self.runner.current_binding

        def changed():
            binding = self.runner.active
            assert binding is not None
            ChatStore(self.engine).cancel_turn(self.runner.owner, conversation, binding.operation_id)
            return original()

        self.runner.current_binding = changed
        result = self.runner.submit("m01")
        self.assertEqual(result["http_status"], 502, result)
        self.assertEqual(self.peer.events, [])
        self.assertIsNone(self.runner.active)

    def test_unchanged_revision_does_not_accept_changed_requirement_sources_or_issues(self):
        for key, value in (("requirements", {"destination_id": "beijing"}),
                           ("sources", [{"field": "destination_id", "quote": "different"}]),
                           ("issues", ["ambiguous"])):
            with self.subTest(key=key):
                self.runner = NativeSession(self.client, self.engine)
                original = self.runner.chat.get
                def modified(owner, conversation):
                    view = original(owner, conversation)
                    if view["last_turn"] and view["last_turn"]["state"] == "running":
                        view = deepcopy(view)
                        view[key] = value
                    return view
                with patch.object(self.runner.chat, "get", side_effect=modified):
                    result = self.runner.submit("m01")
                self.assertEqual(result["http_status"], 502)
        self.assertEqual(self.peer.events, [])

    def test_unchanged_place_revision_does_not_accept_changed_place_content(self):
        original = self.runner.places.get
        def modified(owner, conversation):
            value = original(owner, conversation)
            if self.runner.active is not None:
                value = deepcopy(value)
                value["request_text"] = "untrusted changed place input"
            return value
        # Freeze ordinary place input first, then change only the guard read.
        original_current = self.runner.current_binding
        def guard():
            with patch.object(self.runner.places, "get", side_effect=modified):
                return original_current()
        self.runner.current_binding = guard
        result = self.runner.submit("m01")
        self.assertEqual(result["http_status"], 502)
        self.assertEqual(self.peer.events, [])

    def test_preference_content_change_and_wrong_task_identity_reject_before_transport(self):
        for kind in ("preferences", "task"):
            with self.subTest(kind=kind):
                self.runner = NativeSession(self.client, self.engine)
                original_current = self.runner.current_binding
                def guard():
                    if kind == "preferences":
                        with patch.object(self.runner.tasks, "list_preferences", return_value=[{"value": "changed"}]):
                            return original_current()
                    original_task = self.runner.tasks.latest(self.runner.owner, self.runner.active.conversation_id)
                    with patch.object(self.runner.tasks, "latest", return_value=original_task | {"operation_id": str(uuid4())}):
                        return original_current()
                self.runner.current_binding = guard
                result = self.runner.submit("m01")
                self.assertEqual(result["http_status"], 502)
        self.assertEqual(self.peer.events, [])

    def test_m02_m03_use_real_prior_messages_not_injected_success_history(self):
        from core.schemas.chat import Intent
        first = Intent(destination_id="shanghai", travelers=2, budget_cny="3000", transport_mode="walking")
        second = Intent(destination_id="hangzhou", start_date="2026-11-01", end_date="2026-11-03",
                        travelers=2, budget_cny="3000", transport_mode="walking")

        def selection(intent, city_quote, date_quotes=()):
            sources = [{"field": field, "quote": quote} for field, quote in (
                ("destination_id", city_quote), ("travelers", "两个人" if city_quote == "上海" else "两人"),
                ("budget_cny", "总预算3000元"), ("transport_mode", "步行"), *date_quotes)]
            return {"requirements": intent.model_dump(mode="json"), "sources": sources, "issues": []}

        corrected = selection(second, "杭州", (("start_date", "2026-11-01"), ("end_date", "2026-11-03")))
        # The current contract requires sources exactly for changed fields,
        # not redundant quotes for preserved people/budget/transport values.
        corrected["sources"] = [row for row in corrected["sources"]
                                 if row["field"] in {"destination_id", "start_date", "end_date"}]
        self.peer.responses = [native_tool("update_trip_requirements", selection(first, "上海")),
                              native_tool("update_trip_requirements", corrected)]
        first_result = self.runner.submit("m02")
        self.assertEqual(first_result["http_status"], 200, first_result)
        second_result = self.runner.submit("m03")
        self.assertEqual(second_result["http_status"], 200, second_result)
        self.assertEqual(first_result["binding"]["conversation_id"], second_result["binding"]["conversation_id"])
        self.assertEqual(second_result["readback"]["requirements"]["destination_id"], "hangzhou")
        self.assertIsNone(second_result["readback"]["confirmed_revision"])
        first_messages = first_result["readback"]["messages"]
        self.assertEqual(second_result["readback"]["messages"][:len(first_messages)], first_messages)
        self.assertEqual(self.business.events, [])

    def test_unsafe_direct_request_has_zero_upstream(self):
        conversation = self.runner.conversation("empty")
        response = self.client.post(f"/chat/conversations/{conversation}/assistant-turns", json={
            "operation_id": str(uuid4()), "expected_revision": 0, "text": "查看当前需求"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.peer.events, [])

    def test_browser_prepare_binds_actual_command_and_finish_does_not_claim_local_execution(self):
        conversation = self.runner.conversation("empty")
        question = self.runner.cases["m01"].text
        command = {"operation_id": str(uuid4()), "expected_revision": 0, "text": question,
                   "dialogue_context": {"workspace_version": str(uuid4()), "display": "none", "items": []}}
        for mutation in ({"text": "a different question"}, {"expected_revision": 1}):
            with self.assertRaisesRegex(ControlledError, "question_revision"):
                self.runner.prepare_browser_turn("m01", conversation, command | mutation)
        self.peer.responses = [native_tool("travel_clarify", {"reason": "date_missing"}), native_text()]
        binding = self.runner.prepare_browser_turn("m01", conversation, command)
        self.assertEqual(binding.command_sha256, digest(command))
        self.assertEqual(self.runner.current_binding(), binding.sha256)
        response = self.client.post(f"/chat/conversations/{conversation}/assistant-turns", headers=self.runner.headers, json=command)
        result = self.runner.finish_browser_turn(response.status_code, response.json())
        self.assertEqual(result["http_status"], 200)
        self.assertFalse(result["browser_execution_verified"])
        self.assertIsNone(self.runner.active)
        with self.assertRaisesRegex(ControlledError, "no_active"):
            self.runner.finish_browser_turn(response.status_code, response.json())

    def test_original_m03_correctly_leaves_missing_accommodation_and_cannot_confirm(self):
        from core.schemas.chat import Intent
        from memory.chat_storage import ChatStore
        conversation = self.runner.conversation("requirements")
        store = ChatStore(self.engine)
        operation = str(uuid4())
        reserved = store.begin_turn(self.runner.owner, conversation, operation, 0, "受控计划可执行性检查")
        value = Intent(destination_id="hangzhou", start_date="2026-11-01", end_date="2026-11-03",
                       travelers=2, budget_cny="3000", transport_mode="walking").model_dump(mode="json")
        completed = store.finish_turn(self.runner.owner, conversation, operation, reserved["revision"], value,
                                     "受控离线前提，不是模型评分", [], [])
        response = self.client.post(f"/chat/conversations/{conversation}/confirm", headers=self.runner.headers,
            json={"operation_id": str(uuid4()), "expected_revision": completed["revision"]})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "chat_requirements_incomplete")
        self.assertIsNone(store.get(self.runner.owner, conversation)["confirmed_revision"])
        self.assertEqual(self.peer.events, [])
