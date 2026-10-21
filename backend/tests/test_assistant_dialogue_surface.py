"""Pure-dialogue capability reduction with real isolated stores and fake Qwen.

These tests cover wiring and strict rejection, not model semantic quality.
The legacy card composition and its tool contracts remain independently tested.
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from core.llm_utils import ProviderResponse
from core.schemas.chat import Intent
from tests import test_assistant_api as fixtures
from tests.test_assistant_api import operation, tool


class AssistantDialogueSurfaceTests(unittest.TestCase):
    # Reuse setup only: inheriting AssistantAPITests would silently rerun its
    # entire suite and inflate the apparent number of new regression checks.
    setUp = fixtures.AssistantAPITests.setUp
    login = fixtures.AssistantAPITests.login

    def submit(self, text_value: str = "你好", *, surface: str | None = "dialogue-v1", headers=None):
        selected = dict(self.headers)
        if surface is not None:
            selected["X-TripPilot-Surface"] = surface
        return self.a.post(f"/chat/conversations/{self.view['id']}/assistant-turns",
            headers=selected if headers is None else headers,
            json={"operation_id": operation(), "expected_revision": self.view["revision"], "text": text_value})

    def test_dialogue_has_only_three_server_selected_tools_and_explicit_limits(self):
        self.provider.answers = [tool("inspect_trip", {}), ProviderResponse(content="请告诉我你的旅行需求。", output_tokens=12)]
        response = self.submit()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.provider.calls), 2)
        expected = {"inspect_trip", "update_trip_requirements", "request_clarification"}
        for call in self.provider.calls:
            self.assertEqual({item["function"]["name"] for item in call["tools"]}, expected)
        context = json.loads(self.provider.calls[0]["messages"][1]["content"])["context"]
        self.assertEqual(context["interaction_surface"]["id"], "dialogue-v1")
        self.assertEqual(set(context["interaction_surface"]["available_actions"]), expected)
        self.assertIn("未提供真实POI预览确认", context["interaction_surface"]["limitations"])
        self.assertIn("不能声称已执行", context["interaction_surface"]["limitations"])
        self.assertEqual(self.places.get(self.owner, self.view["id"])["required_places"], [])

    def test_dialogue_still_uses_strict_provenance_for_requirement_updates(self):
        self.provider.answers = [tool("update_trip_requirements", {
            "requirements": Intent(destination_id="shanghai", travelers=2).model_dump(),
            "sources": [{"field": "destination_id", "quote": "上海"}, {"field": "travelers", "quote": "两个人"}],
            "issues": [],
        }), ProviderResponse(content="已整理上海、两人的需求；尚未生成行程。", output_tokens=15)]
        response = self.submit("去上海，两个人。")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["requirements"]["travelers"], 2)
        self.assertEqual(response.json()["requirements"]["destination_id"], "shanghai")
        self.assertEqual(len(self.provider.calls), 2)

    def test_dialogue_clarification_ends_without_an_extra_model_summary(self):
        self.provider.answers = [tool("request_clarification", {"reason": "unsupported"})]
        response = self.submit("帮我生成正式行程")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("暂不能执行", response.json()["messages"][-1]["text"])
        self.assertEqual(len(self.provider.calls), 1)

    def test_model_cannot_select_removed_tools_or_trigger_their_providers(self):
        for name, arguments in (("search_places", {"city_id": "310000", "keyword": "外滩", "role": "required"}),
                                ("prepare_itinerary", {}), ("get_day_schedule", {"date": "2026-11-01"}),
                                ("get_budget_summary", {})):
            with self.subTest(tool=name):
                self.view = self.chat.get(self.owner, self.view["id"])
                self.provider.answers = [tool(name, arguments)]
                before = len(self.provider.calls)
                with patch("api.assistant.ChatPlacesClient.search", side_effect=AssertionError("must not reach AMap")):
                    response = self.submit("请搜索外滩并帮我规划")
                self.assertEqual(response.status_code, 502, response.text)
                self.assertEqual(response.json()["detail"]["code"], "assistant_tool_not_allowed")
                self.assertEqual(len(self.provider.calls), before + 1)
                self.assertEqual(self.places.get(self.owner, self.view["id"])["required_places"], [])
                self.assertEqual(self.tasks.latest(self.owner, self.view["id"])["state"], "failed")

    def test_unknown_empty_combined_and_duplicate_headers_fail_before_reservation(self):
        invalid = [[("x-trippilot-surface", value)] for value in ("", "unknown", "legacy-v1", "dialogue-v1,dialogue-v1", "Dialogue-V1")]
        invalid.append([("x-trippilot-surface", "dialogue-v1"), ("x-trippilot-surface", "dialogue-v1")])
        invalid.append([("x-trippilot-surface", "dialogue-v1"), ("X-TripPilot-Surface", "unknown")])
        with patch.object(self.chat, "begin_turn", side_effect=AssertionError("must not reserve a turn")), \
             patch.object(self.tasks, "begin", side_effect=AssertionError("must not reserve a task")):
            for entries in invalid:
                with self.subTest(headers=entries):
                    response = self.submit(headers=list(self.headers.items()) + entries)
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertEqual(response.json()["detail"]["code"], "assistant_invalid_surface")
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.chat.get(self.owner, self.view["id"])["revision"], 0)
        self.assertIsNone(self.tasks.latest(self.owner, self.view["id"]))

    def test_missing_header_preserves_the_legacy_registry_and_context(self):
        self.provider.answers = [ProviderResponse(content="你好。", output_tokens=6)]
        response = self.submit(surface=None)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual({item["function"]["name"] for item in self.provider.calls[0]["tools"]}, {
            "inspect_trip", "update_trip_requirements", "request_clarification", "search_places",
            "prepare_itinerary", "get_day_schedule", "get_budget_summary",
        })
        context = json.loads(self.provider.calls[0]["messages"][1]["content"])["context"]
        self.assertNotIn("interaction_surface", context)

    def test_surface_does_not_allow_client_defined_tool_lists_or_urls(self):
        with patch.object(self.chat, "begin_turn", side_effect=AssertionError("must not reserve a turn")):
            for extra in ({"tools": ["search_places"]}, {"upstream_url": "https://untrusted.invalid"}):
                with self.subTest(extra=extra):
                    response = self.a.post(f"/chat/conversations/{self.view['id']}/assistant-turns",
                        headers=self.headers | {"X-TripPilot-Surface": "dialogue-v1"},
                        json={"operation_id": operation(), "expected_revision": self.view["revision"], "text": "你好"} | extra)
                    self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.provider.calls, [])


if __name__ == "__main__":
    unittest.main()
