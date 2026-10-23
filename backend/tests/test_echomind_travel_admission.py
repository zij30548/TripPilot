"""Real durable admission with native MockTransport; no paid/business calls."""
from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from unittest.mock import patch

import httpx
from pydantic import ValidationError

if any(importlib.util.find_spec(name) is None for name in ("jsonschema", "prometheus_client")):
    raise unittest.SkipTest("New runtime admission requires the explicit echomind extra")

from api.explanation import get_qwen_http_client
from core.business_usage import DailyLimit
from core.config import Settings
from core.schemas.chat import Intent
from core.usage_runtime import MeteredAsyncClient
from memory.chat_storage import ChatError
from tests import test_assistant_api as fixtures
from tests.test_assistant_api import operation
from tests.test_chat_storage import intent
from tests.test_echomind_travel_engine import FABRICATED, native_text, native_tools
from tests.usage_fixture import UsageFixture


class TravelAdmissionTests(unittest.TestCase):
    setUp_base = fixtures.AssistantAPITests.setUp
    login = fixtures.AssistantAPITests.login

    def setUp(self):
        self.setUp_base()
        self.settings.assistant_runtime = "echomind_travel_v1"
        self.fixture = UsageFixture()
        self.addCleanup(self.fixture.close)
        self.enterContext(patch("core.usage_runtime.get_usage_store", return_value=self.fixture.store))
        self.responses, self.sent = [], []
        async def controlled_http():
            async def wire(request):
                self.sent.append({"path": request.url.path, "payload": json.loads(request.content)})
                value = self.responses.pop(0)
                if isinstance(value, BaseException):
                    raise value
                return httpx.Response(200, json=value)
            async with MeteredAsyncClient(transport=httpx.MockTransport(wire), follow_redirects=False) as client:
                yield client
        self.a.app.dependency_overrides[get_qwen_http_client] = controlled_http

    def submit(self, message="你好", *, op=None, view=None, surface="dialogue-v2", extra=None):
        view = view or self.chat.get(self.owner, self.view["id"])
        headers = self.headers | ({"X-TripPilot-Surface": surface} if surface is not None else {})
        return self.a.post(f"/chat/conversations/{view['id']}/assistant-turns", headers=headers,
            json={"operation_id": op or operation(), "expected_revision": view["revision"], "text": message} | (extra or {}))

    def fixture_intent(self, *, confirm=False):
        view = self.chat.get(self.owner, self.view["id"])
        op = operation()
        begun = self.chat.begin_turn(self.owner, view["id"], op, view["revision"], "杭州旅行")
        view = self.chat.finish_turn(self.owner, view["id"], op, begun["revision"], intent(), "请核对。", [])
        if confirm:
            view = self.chat.confirm(self.owner, view["id"], view["revision"], operation())
        self.view = view
        return view

    def test_server_setting_is_closed_and_surface_cannot_choose_another_engine(self):
        self.assertEqual(Settings(_env_file=None).assistant_runtime, "legacy")
        with self.assertRaises(ValidationError):
            Settings(_env_file=None, assistant_runtime="arbitrary-agent")
        for surface in (None, "dialogue-v1"):
            with self.subTest(surface=surface):
                response = self.submit(surface=surface)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["detail"]["code"], "assistant_runtime_surface_required")
        self.assertEqual(self.sent, [])
        self.assertEqual(self.chat.get(self.owner, self.view["id"])["revision"], 0)

    def test_missing_optional_dependencies_fails_feature_before_durable_reservation(self):
        with patch.dict(sys.modules, {"echomind_core.integration": None}):
            response = self.submit()
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["detail"]["code"], "assistant_runtime_dependencies_unavailable")
        self.assertEqual(self.sent, [])
        self.assertEqual(self.chat.get(self.owner, self.view["id"])["revision"], 0)

    def test_request_cannot_override_runtime_or_supply_an_owner(self):
        for field in ("assistant_runtime", "owner_id", "tools", "upstream_url"):
            with self.subTest(field=field):
                response = self.submit(extra={field: "legacy"})
                self.assertEqual(response.status_code, 422)
        self.assertEqual(self.sent, [])

    def test_native_protocol_executes_fixed_clarification_and_discards_free_model_body(self):
        self.responses = [native_tools(("clarify_once", "travel_clarify", {"reason": "destination_missing"})), native_text()]
        response = self.submit("我想去旅行")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("请明确本次想去的城市", response.json()["messages"][-1]["text"])
        self.assertNotIn(FABRICATED, response.text)
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.sent[0]["path"], "/compatible-mode/v1/chat/completions")
        self.assertEqual(len(self.sent[0]["payload"]["tools"]), 16)
        self.assertEqual(self.sent[1]["payload"]["messages"][-1]["role"], "tool")
        task = self.tasks.latest(self.owner, self.view["id"])
        self.assertEqual(task["state"], "waiting")
        self.assertEqual(task["checkpoint"]["result"]["runtime"]["runtime"], "echomind_travel_v1")
        state = self.a.get(f"/chat/conversations/{self.view['id']}/assistant-state", headers=self.headers).json()
        self.assertEqual((state["task"]["model_calls"], state["task"]["tool_calls"]), (2, 1))
        self.assertTrue(all(row["state"] == "succeeded" for row in state["task"]["steps"]))
        self.assertNotIn("request_failed", str(state))
        metadata = task["checkpoint"]["result"]["runtime"]
        self.assertEqual(metadata["usage"], "unknown")
        self.assertEqual(metadata["per_step_elapsed"], "unknown")
        self.assertEqual(metadata["upstream_model"], "unknown")

    def test_browser_action_wire_is_unchanged_and_proposal_has_not_executed(self):
        self.responses = [native_tools(("schedule_once", "travel_schedule", {"action": "generate_schedule"})), native_text()]
        response = self.submit("生成行程")
        self.assertEqual(response.status_code, 200, response.text)
        action = self.a.get(f"/chat/conversations/{self.view['id']}/dialogue-action", headers=self.headers)
        self.assertEqual(action.status_code, 200, action.text)
        self.assertEqual(set(action.json()), {"schema_version", "conversation_id", "revision", "operation_id", "action"})
        self.assertEqual(action.json()["action"], {"name": "generate_schedule", "arguments": {}})
        self.assertIn("尚未执行", response.json()["messages"][-1]["text"])
        task = self.tasks.latest(self.owner, self.view["id"])
        public = task["checkpoint"]["result"]
        self.assertNotIn("owner_id", str(public))
        self.assertNotIn("session_id", str(public))
        self.assertNotIn("latitude", str(public))
        self.assertEqual(task["state"], "waiting")

    def test_requirement_proposal_is_accurate_persisted_draft_then_explicit_cas_confirmation(self):
        value = intent()
        selection = {"requirements": value, "sources": [
            {"field": "destination_id", "quote": "杭州"}, {"field": "start_date", "quote": "2026-11-01"},
            {"field": "end_date", "quote": "2026-11-03"}, {"field": "travelers", "quote": "两人"},
            {"field": "budget_cny", "quote": "3000"}, {"field": "accommodation_hint", "quote": "西湖附近"},
            {"field": "required_place_hints", "quote": "西湖"}, {"field": "transport_mode", "quote": "步行"}], "issues": []}
        self.responses = [native_tools(("requirements_once", "update_trip_requirements", selection)), native_text()]
        response = self.submit("杭州，2026-11-01到2026-11-03，两人预算3000，住西湖附近，想去西湖，步行")
        self.assertEqual(response.status_code, 200, response.text)
        draft = response.json()
        self.assertEqual(draft["requirements"], value)
        self.assertIsNone(draft["confirmed_revision"])
        self.assertIn("待确认需求草稿", draft["messages"][-1]["text"])
        self.assertNotIn("尚未写入", draft["messages"][-1]["text"])
        command = {"operation_id": operation(), "expected_revision": draft["revision"]}
        confirmed = self.a.post(f"/chat/conversations/{draft['id']}/confirm", headers=self.headers, json=command)
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertEqual(confirmed.json()["confirmed_revision"], confirmed.json()["revision"])
        # Same explicit confirmation is idempotent; an old different operation
        # cannot mutate a newer requirement version.
        replay = self.a.post(f"/chat/conversations/{draft['id']}/confirm", headers=self.headers, json=command)
        self.assertEqual(replay.json(), confirmed.json())
        self.assertEqual(len(self.sent), 2)

    def test_successful_unchanged_turn_carries_valid_confirmation_but_changes_make_draft(self):
        initial = self.fixture_intent(confirm=True)
        self.responses = [native_tools(("read_once", "inspect_trip", {})), native_text()]
        response = self.submit("查看当前需求")
        self.assertEqual(response.status_code, 200, response.text)
        current = response.json()
        self.assertEqual(current["confirmed_revision"], current["revision"])
        self.assertEqual(current["requirements"], initial["requirements"])
        proposed = {**current["requirements"], "travelers": 3}
        self.responses = [native_tools(("change_once", "update_trip_requirements", {"requirements": proposed,
            "sources": [{"field": "travelers", "quote": "三人"}], "issues": []})), native_text()]
        changed = self.submit("改成三人")
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertIsNone(changed.json()["confirmed_revision"])
        self.assertEqual(changed.json()["requirements"]["travelers"], 3)

    def test_repeat_operation_is_persistently_denied_before_another_native_send(self):
        op = operation()
        self.responses = [native_tools(("read_once", "inspect_trip", {})), native_text()]
        first = self.submit("查看需求", op=op)
        self.assertEqual(first.status_code, 200, first.text)
        response = self.submit("查看需求", op=op)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(len(self.sent), 2)

    def test_unrecognized_tool_and_bad_provenance_fail_without_writes_or_fallback(self):
        for answer in (native_tools(("unknown", "arbitrary_tool", {})),
                       native_tools(("bad_source", "update_trip_requirements", {"requirements": Intent(travelers=2).model_dump(),
                           "sources": [{"field": "travelers", "quote": "不是用户说的"}], "issues": []}))):
            with self.subTest(answer=answer):
                self.responses = [answer]
                response = self.submit("你好")
                self.assertEqual(response.status_code, 502, response.text)
                self.assertIsNone(self.chat.get(self.owner, self.view["id"])["requirements"]["travelers"])
                self.assertEqual(self.tasks.latest(self.owner, self.view["id"])["state"], "failed")
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.provider.calls, [])

    def test_quota_denial_preserves_public_error_and_starts_zero_upstream_sends(self):
        self.fixture.store.settings.business_daily_limits["qwen_text"] = DailyLimit(account=0, **{"global": 10})
        response = self.submit("查看需求")
        self.assertEqual(response.status_code, 429, response.text)
        self.assertEqual(response.json()["detail"]["code"], "business_service_disabled")
        self.assertEqual(response.json()["detail"]["service"], "qwen_text")
        self.assertEqual(self.sent, [])
        self.assertEqual(self.tasks.latest(self.owner, self.view["id"])["state"], "failed")
        state = self.a.get(f"/chat/conversations/{self.view['id']}/assistant-state", headers=self.headers).json()
        self.assertEqual(state["task"]["state"], "failed")
        self.assertTrue(all(row["state"] == "failed" for row in state["task"]["steps"]))

    def test_readonly_reply_limit_preserves_consumed_attempt_counts_without_truncation_or_retry(self):
        for index in range(7):
            self.tasks.set_preference(self.owner, f"preference_{index}", "测" * 500, consent=True)
        self.responses = [native_tools(("read_once", "read_preferences", {})), native_text()]
        response = self.submit("查看已记住的偏好")
        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(response.json()["detail"]["code"], "assistant_reply_limit")
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.provider.calls, [])
        task = self.tasks.latest(self.owner, self.view["id"])
        self.assertEqual(task["state"], "failed")
        self.assertEqual(task["checkpoint"]["stop_reason"], "reply_limit")
        self.assertEqual(sum(row.get("tool") == "model" for row in task["checkpoint"]["trace"]), 2)
        self.assertEqual(sum(row.get("tool") == "tool_attempt" for row in task["checkpoint"]["trace"]), 1)
        self.assertEqual(self.fixture.store.own_usage(self.owner)["services"]["qwen_text"]["used"], 2)
        self.assertEqual(self.chat.get(self.owner, self.view["id"])["last_turn"]["state"], "failed")
        self.assertEqual(len(self.tasks.list_preferences(self.owner)), 7)

    def test_committed_receipt_ack_loss_requires_readback_and_never_resends(self):
        self.responses = [native_tools(("read_once", "inspect_trip", {})), native_text()]
        original = self.tasks.finalize
        def lost(*args, **kwargs):
            original(*args, **kwargs)
            raise ChatError("storage_unavailable")
        op = operation()
        with patch.object(self.tasks, "finalize", side_effect=lost):
            response = self.submit("查看需求", op=op)
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["detail"]["code"], "assistant_post_commit_readback_required")
        self.assertEqual(self.tasks.latest(self.owner, self.view["id"])["state"], "succeeded")
        retry = self.submit("查看需求", op=op)
        self.assertEqual(retry.status_code, 409)
        self.assertEqual(len(self.sent), 2)


if __name__ == "__main__":
    unittest.main()
