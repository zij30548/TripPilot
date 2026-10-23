"""Offline server-only transport selection, unchanged confirmation authority.

The MCP adapter below is a controlled replacement, not a remote MCP or AMap
acceptance. Real existing stores/operation receipts run only in temporary DBs.
"""
from __future__ import annotations

import sys
from types import ModuleType
import unittest
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import httpx
from pydantic import SecretStr, TypeAdapter, ValidationError

from core.config import Settings
from core.schemas.chat_places import ChatPlacesRequest
from tests import test_assistant_api as assistant_fixture
from tests import test_chat_places_api as places_fixture
from tooling.providers.chat_places import ChatPlacesClient, ChatPlacesError, _place
from tooling.providers.chat_places_factory import chat_places_configured, create_chat_places_client


def mcp_module(constructor: object) -> ModuleType:
    module = ModuleType("tooling.providers.amap_mcp")
    module.ChatPlacesMCPClient = constructor
    return module


class ChatPlacesTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_rest_default_uses_existing_client_and_does_not_import_optional_mcp(self):
        settings = Settings(_env_file=None, amap_web_key="controlled-rest")
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"status": "1", "pois": []}))) as http:
            with patch.dict(sys.modules, {"tooling.providers.amap_mcp": None}):
                client = create_chat_places_client(settings, http)
                self.assertIsInstance(client, ChatPlacesClient)
                self.assertEqual(await client.search("测试地点", "310000"), [])
        self.assertEqual(settings.chat_places_transport, "rest")

    async def test_mcp_key_is_independent_of_rest_key_and_is_never_passed_to_rest(self):
        settings = Settings(_env_file=None, chat_places_transport="amap_mcp", amap_web_key="",
                            amap_mcp_key="  controlled-mcp  ")
        adapter = Mock(search=AsyncMock(return_value=[]))
        constructor = Mock(return_value=adapter)
        async with httpx.AsyncClient() as http:
            with patch.dict(sys.modules, {"tooling.providers.amap_mcp": mcp_module(constructor)}), patch(
                    "tooling.providers.chat_places_factory.ChatPlacesClient", side_effect=AssertionError("no REST fallback")):
                client = create_chat_places_client(settings, http)
                self.assertEqual(await client.search("测试地点", "310000"), [])
        constructor.assert_called_once_with("controlled-mcp")
        adapter.search.assert_awaited_once_with("测试地点", "310000")
        self.assertTrue(chat_places_configured(settings))
        self.assertNotIn("controlled-mcp", repr(settings))

    async def test_each_transport_requires_its_own_key_without_cross_key_fallback(self):
        variants = [Settings(_env_file=None, amap_web_key="", amap_mcp_key="controlled-mcp"),
                    Settings(_env_file=None, chat_places_transport="amap_mcp", amap_web_key="controlled-rest", amap_mcp_key=" ")]
        async with httpx.AsyncClient() as http:
            for settings in variants:
                with self.subTest(transport=settings.chat_places_transport):
                    self.assertFalse(chat_places_configured(settings))
                    with self.assertRaises(ChatPlacesError) as error:
                        create_chat_places_client(settings, http)
                    self.assertEqual(error.exception.code, "places_not_configured")

    async def test_missing_optional_dependency_fails_only_selected_mcp_without_fallback(self):
        settings = Settings(_env_file=None, chat_places_transport="amap_mcp", amap_web_key="controlled-rest",
                            amap_mcp_key="controlled-mcp")
        async with httpx.AsyncClient() as http:
            with patch.dict(sys.modules, {"tooling.providers.amap_mcp": None}), patch(
                    "tooling.providers.chat_places_factory.ChatPlacesClient", side_effect=AssertionError("no REST fallback")):
                with self.assertRaises(ChatPlacesError) as error:
                    create_chat_places_client(settings, http)
        self.assertEqual(error.exception.code, "chat_place_mcp_unavailable")

    async def test_mcp_search_failure_is_preserved_without_automatic_rest_send(self):
        settings = Settings(_env_file=None, chat_places_transport="amap_mcp", amap_web_key="controlled-rest",
                            amap_mcp_key="controlled-mcp")
        expected = ChatPlacesError("chat_place_region_error")
        adapter = Mock(search=AsyncMock(side_effect=expected))
        async with httpx.AsyncClient() as http:
            with patch.dict(sys.modules, {"tooling.providers.amap_mcp": mcp_module(Mock(return_value=adapter))}), patch(
                    "tooling.providers.chat_places_factory.ChatPlacesClient", side_effect=AssertionError("no REST fallback")):
                with self.assertRaises(ChatPlacesError) as error:
                    await create_chat_places_client(settings, http).search("测试地点", "310000")
        self.assertIs(error.exception, expected)
        adapter.search.assert_awaited_once()

    async def test_optional_dependency_failure_during_construction_is_controlled(self):
        settings = Settings(_env_file=None, chat_places_transport="amap_mcp", amap_mcp_key="controlled-mcp")
        constructor = Mock(side_effect=ImportError("controlled optional dependency missing"))
        async with httpx.AsyncClient() as http:
            with patch.dict(sys.modules, {"tooling.providers.amap_mcp": mcp_module(constructor)}):
                with self.assertRaises(ChatPlacesError) as error:
                    create_chat_places_client(settings, http)
        self.assertEqual(error.exception.code, "chat_place_mcp_unavailable")

    def test_transport_is_finite_server_configuration_and_not_a_request_field(self):
        for value in ("mcp", "auto", "https://arbitrary.example/mcp", "", None):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                Settings(_env_file=None, chat_places_transport=value)
        self.assertNotIn("amap_mcp_url", Settings.model_fields)
        base = {"operation_id": str(uuid4()), "expected_intent_revision": 0, "expected_revision": 0,
                "action": "search", "keyword": "测试地点", "role": "required"}
        for field in ("transport", "chat_places_transport", "provider", "url", "amap_mcp_key", "owner_id"):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                TypeAdapter(ChatPlacesRequest).validate_python(base | {field: "arbitrary"})


# Reuse only setup/helpers, not inherited tests or a second business fixture.
class ChatPlacesTransportAPITests(unittest.TestCase):
    setUp = places_fixture.ChatPlacesAPITests.setUp
    login = places_fixture.ChatPlacesAPITests.login
    create = places_fixture.ChatPlacesAPITests.create
    dispose = places_fixture.ChatPlacesAPITests.dispose
    usage_rows = places_fixture.ChatPlacesAPITests.usage_rows
    upstream = places_fixture.ChatPlacesAPITests.upstream
    path_for = places_fixture.ChatPlacesAPITests.path_for
    command = places_fixture.ChatPlacesAPITests.command
    set_city = places_fixture.ChatPlacesAPITests.set_city

    def test_explicit_mcp_choice_keeps_preview_confirmation_and_operation_replay(self):
        self.set_city()
        self.settings.chat_places_transport = "amap_mcp"
        self.settings.amap_web_key = SecretStr("")
        self.settings.amap_mcp_key = SecretStr("controlled-mcp")
        adapter = Mock(search=AsyncMock(return_value=[_place(places_fixture.poi(), "310000")]))
        before, operation = self.view, str(uuid4())
        with patch.dict(sys.modules, {"tooling.providers.amap_mcp": mcp_module(Mock(return_value=adapter))}):
            result = self.command("search", op=operation, keyword="测试地点", role="required")
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(self.view["required_places"], [])
            batch = self.view["batch"]
            self.assertEqual(batch["places"][0]["id"], "controlled-poi")
            replay = self.command("search", view=before, op=operation, keyword="测试地点", role="required")
            self.assertEqual(replay.status_code, 200, replay.text)
        adapter.search.assert_awaited_once_with("测试地点", "310000")
        confirmed = self.command("confirm", batch_id=batch["id"], place_id="controlled-poi", role="required")
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertEqual(self.view["required_places"][0], batch["places"][0])
        self.assertEqual(self.calls, [])
        # This controlled adapter does no transport; quota sends are separately
        # tested against the real MCP transport, not manufactured here.
        self.assertEqual(self.usage_rows(), [])

    def test_selected_mcp_missing_key_does_not_use_configured_rest_or_remain_running(self):
        self.set_city()
        self.settings.chat_places_transport = "amap_mcp"
        result = self.command("search", keyword="测试地点", role="required")
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.view["last_operation"]["error_code"], "places_not_configured")
        self.assertEqual(self.view["last_operation"]["state"], "failed")
        self.assertIsNone(self.view["batch"])
        self.assertEqual(self.calls, [])

    def test_mcp_failure_persists_original_safe_code_and_no_confirmable_candidates(self):
        self.set_city()
        self.settings.chat_places_transport = "amap_mcp"
        self.settings.amap_mcp_key = SecretStr("controlled-mcp")
        adapter = Mock(search=AsyncMock(side_effect=ChatPlacesError("chat_place_region_error")))
        with patch.dict(sys.modules, {"tooling.providers.amap_mcp": mcp_module(Mock(return_value=adapter))}):
            result = self.command("search", keyword="测试地点", role="required")
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.view["last_operation"]["state"], "failed")
        self.assertEqual(self.view["last_operation"]["error_code"], "chat_place_region_error")
        self.assertIsNone(self.view["batch"])
        self.assertEqual(self.view["required_places"], [])
        self.assertEqual(self.calls, [])


class AssistantPlacesTransportAPITests(unittest.TestCase):
    setUp = assistant_fixture.AssistantAPITests.setUp
    login = assistant_fixture.AssistantAPITests.login
    submit = assistant_fixture.AssistantAPITests.submit
    set_shanghai = assistant_fixture.AssistantAPITests.set_shanghai

    def test_legacy_assistant_search_uses_same_server_selected_mcp_and_never_confirms(self):
        self.set_shanghai()
        self.settings.chat_places_transport = "amap_mcp"
        self.settings.amap_web_key = SecretStr("")
        self.settings.amap_mcp_key = SecretStr("controlled-mcp")
        self.provider.answers = [assistant_fixture.tool("search_places", {
            "city_id": "310000", "keyword": "上海博物馆", "role": "required"})]
        adapter = Mock(search=AsyncMock(return_value=[_place(places_fixture.poi(), "310000")]))
        with patch.dict(sys.modules, {"tooling.providers.amap_mcp": mcp_module(Mock(return_value=adapter))}):
            result = self.submit("帮我搜索上海博物馆")
        self.assertEqual(result.status_code, 200, result.text)
        adapter.search.assert_awaited_once_with("上海博物馆", "310000")
        workspace = self.places.get(self.owner, self.view["id"])
        self.assertEqual(workspace["batch"]["places"][0]["id"], "controlled-poi")
        self.assertEqual(workspace["required_places"], [])
        self.assertIsNone(workspace["accommodation"])
        self.assertEqual(len(self.provider.calls), 1)

    def _discovery_guard_readback(self, changed_field: str | None) -> None:
        """Controlled adapter phase, not a real SDK or public mutation race.

        Actual set_city is busy during an active turn. Here a changed persisted
        readback is injected only after the adapter's discovery phase to prove
        the supplied actual-send hook checks places as well as conversation.
        """
        self.set_shanghai()
        self.settings.chat_places_transport = "amap_mcp"
        self.settings.amap_mcp_key = SecretStr("controlled-mcp")
        self.provider.answers = [assistant_fixture.tool("search_places", {
            "city_id": "310000", "keyword": "上海博物馆", "role": "required"})]
        phase, actual_dispatches, observed_stale = [], [], []
        original_get = self.places.get

        def readback(*arguments):
            value = original_get(*arguments)
            observed_stale.append(value["stale"])
            if changed_field == "city_id":
                return value | {"city_id": "110000"}
            if changed_field is not None:
                return value | {changed_field: value[changed_field] + 1}
            return value

        def construct(_key, *, before_send):
            async def search(_keyword, _city):
                phase.append("discovery_complete")
                with patch.object(self.places, "get", side_effect=readback):
                    await before_send()
                    await before_send()
                actual_dispatches.append("controlled_tools_call")
                return [_place(places_fixture.poi(), "310000")]
            return Mock(search=search)

        with patch.dict(sys.modules, {"tooling.providers.amap_mcp": mcp_module(construct)}):
            response = self.submit("帮我搜索上海博物馆")
        self.assertEqual(phase, ["discovery_complete"])
        self.assertTrue(observed_stale)
        self.assertTrue(all(observed_stale))
        workspace = self.places.get(self.owner, self.view["id"])
        if changed_field is None:
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(actual_dispatches, ["controlled_tools_call"])
            self.assertEqual(workspace["batch"]["places"][0]["id"], "controlled-poi")
        else:
            self.assertEqual(response.status_code, 502, response.text)
            self.assertEqual(response.json()["detail"]["code"], "assistant_context_changed")
            self.assertEqual(actual_dispatches, [])
            self.assertIsNone(workspace["batch"])
            self.assertEqual(self.chat.get(self.owner, self.view["id"])["last_turn"]["state"], "failed")
        self.assertEqual(workspace["required_places"], [])
        self.assertEqual(len(self.provider.calls), 1)

    def test_legacy_mcp_send_guard_accepts_unchanged_places_despite_expected_stale_view(self):
        self._discovery_guard_readback(None)

    def test_legacy_mcp_send_guard_rejects_places_revision_change_after_discovery(self):
        self._discovery_guard_readback("revision")

    def test_legacy_mcp_send_guard_rejects_places_intent_revision_change_after_discovery(self):
        self._discovery_guard_readback("intent_revision")

    def test_legacy_mcp_send_guard_rejects_places_city_change_after_discovery(self):
        self._discovery_guard_readback("city_id")


if __name__ == "__main__":
    unittest.main()
