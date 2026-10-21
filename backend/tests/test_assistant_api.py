"""Real auth and isolated disk stores with controlled native-agent responses.

No model, AMap, weather, route or production database is contacted.
"""
from __future__ import annotations

import asyncio
import json
from concurrent.futures import CancelledError as FutureCancelledError, ThreadPoolExecutor
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from agents.agent_orchestrator import AgentOrchestrator
from core.llm_utils import NativeToolCall, ProviderResponse, RuntimeFailure
from core import auth
from api import assistant, chat, chat_places
from api.auth import router as auth_router
from api.explanation import get_qwen_http_client
from memory.chat_places_storage import ChatPlacesStore
from memory.chat_storage import ChatStore
from memory.chat_storage import ChatError
from core.config import Settings, get_settings
from core.business_usage import DailyLimit, UsageError
from tooling.providers.chat_places import _place
from core.schemas.chat import Conversation, Intent
from memory.storage import engine_for
from core.usage_runtime import MeteredAsyncClient
from memory.assistant_store import AssistantStore
from tools.assistant_storage import provision_assistant_extension
from tools.chat_places_storage import provision_chat_places_extension
from tools.chat_storage import provision_chat_extension
from tests.usage_fixture import UsageFixture
from main import business_usage_error

ORIGIN = "http://127.0.0.1:3000"


def operation() -> str:
    return str(uuid4())


def tool(name: str, arguments: dict) -> ProviderResponse:
    return ProviderResponse(tool_call=NativeToolCall("call_" + uuid4().hex, name, arguments), output_tokens=20)


class ControlledProvider:
    def __init__(self) -> None:
        self.answers: list[ProviderResponse | Exception] = []
        self.calls: list[dict] = []
        self.entered, self.release = threading.Event(), threading.Event()
        self.pause = False

    async def complete(self, messages, tools, *, max_tokens):
        self.calls.append({"messages": messages, "tools": tools, "max_tokens": max_tokens})
        answer = self.answers.pop(0)
        if self.pause:
            self.pause = False
            self.entered.set()
            while not self.release.is_set():
                await asyncio.sleep(.005)
        if isinstance(answer, Exception):
            raise answer
        return answer


class AssistantAPITests(unittest.TestCase):
    def setUp(self):
        folder = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="trippilot-assistant-api-")))
        self.enterContext(patch.dict(os.environ, {"TRIPPILOT_DATABASE_PATH": str(folder / "independent.sqlite3"), "TRIPPILOT_MYSQL_URL": ""}))
        auth_settings = auth.AuthSettings(_env_file=None, auth_cookie_secure=False)
        self.enterContext(patch("core.auth.get_auth_settings", return_value=auth_settings))
        self.enterContext(patch("api.auth.get_auth_settings", return_value=auth_settings))
        self.owner = auth.create_account("assistant-a", "controlled-assistant-a-password")
        self.other = auth.create_account("assistant-b", "controlled-assistant-b-password")
        self.engine = engine_for(str(folder / "independent.sqlite3"))
        self.addCleanup(self.engine.dispose)
        self.addCleanup(engine_for.cache_clear)
        provision_chat_extension(self.engine)
        provision_chat_places_extension(self.engine)
        provision_assistant_extension(self.engine)
        self.chat = ChatStore(self.engine)
        self.places = ChatPlacesStore(self.engine)
        self.tasks = AssistantStore(self.engine)
        self.provider = ControlledProvider()
        app = FastAPI()
        app.add_middleware(auth.AuthenticationMiddleware)
        app.add_exception_handler(UsageError, business_usage_error)
        for router in (auth_router, chat.router, chat_places.router, assistant.router):
            app.include_router(router)
        self.settings = Settings(_env_file=None, qwen_api_key="controlled-not-a-real-key",
                                 qwen_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", amap_web_key="controlled-not-a-real-key")
        async def controlled_http():
            def unexpected(_):
                raise AssertionError("no real or unplanned HTTP calls allowed")
            async with MeteredAsyncClient(transport=httpx.MockTransport(unexpected)) as client:
                yield client
        app.dependency_overrides.update({chat.get_chat_store: lambda: self.chat,
            chat_places.get_chat_places_store: lambda: self.places,
            assistant.get_assistant_store: lambda: self.tasks,
            assistant.get_orchestrator: lambda: lambda provider, tools, **kwargs: AgentOrchestrator(self.provider, tools, **kwargs),
            get_qwen_http_client: controlled_http, get_settings: lambda: self.settings})
        self.a = self.enterContext(TestClient(app))
        self.b = self.enterContext(TestClient(app))
        self.anonymous = self.enterContext(TestClient(app))
        self.headers = self.login(self.a, "a")
        self.other_headers = self.login(self.b, "b")
        self.view = self.chat.create(self.owner, operation())

    def login(self, client, suffix):
        csrf = client.get("/auth/session").json()["csrf_token"]
        response = client.post("/auth/login", headers={"origin": ORIGIN, "x-csrf-token": csrf},
            json={"username": f"assistant-{suffix}", "password": f"controlled-assistant-{suffix}-password"})
        self.assertEqual(response.status_code, 200, response.text)
        return {"origin": ORIGIN, "x-csrf-token": response.json()["csrf_token"]}

    def submit(self, text_value: str, *, op: str | None = None, view: dict | None = None):
        view = view or self.view
        return self.a.post(f"/chat/conversations/{view['id']}/assistant-turns", headers=self.headers,
            json={"operation_id": op or operation(), "expected_revision": view["revision"], "text": text_value})

    def state(self, client=None, headers=None):
        return (client or self.a).get(f"/chat/conversations/{self.view['id']}/assistant-state", headers=headers or self.headers)

    def set_shanghai(self):
        reservation = self.chat.begin_turn(self.owner, self.view["id"], operation(), self.view["revision"], "上海")
        self.view = self.chat.finish_turn(self.owner, self.view["id"], reservation["conversation"]["last_turn"]["operation_id"],
            reservation["revision"], Intent(destination_id="shanghai").model_dump(), "已整理待确认需求", [{"field": "destination_id", "quote": "上海"}])
        return self.places.sync(self.owner, self.view["id"], operation(), self.view["revision"], 0)

    def seed_confirmed_place(self):
        workspace = self.set_shanghai()
        from datetime import datetime, timezone
        place = _place({"id": "controlled-old-poi", "name": "旧城已确认地点", "address": "受控地址", "location": "121.45,31.23", "type": "博物馆",
                        "citycode": "021", "cityname": "上海市", "adcode": "310101"}, "310000").model_dump(mode="json")
        reserved = self.places.begin_operation(self.owner, self.view["id"], operation(), self.view["revision"], workspace["revision"],
                                               "search", {"keyword": "受控地点", "role": "required"})
        batch_id = operation()
        result = self.places.finish_operation(self.owner, self.view["id"], reserved["operation_id"], reserved["revision"], self.view["revision"],
            batch={"id": batch_id, "city_id": "310000", "keyword": "受控地点", "role": "required", "queried_at": datetime.now(timezone.utc).isoformat(), "places": [place]})
        return self.places.confirm(self.owner, self.view["id"], operation(), self.view["revision"], result["revision"], batch_id, place["id"], "required")

    def test_identity_csrf_owner_and_exact_state_boundary(self):
        path = f"/chat/conversations/{self.view['id']}/assistant-turns"
        payload = {"operation_id": operation(), "expected_revision": 0, "text": "你好"}
        self.assertEqual(self.anonymous.post(path, json=payload).status_code, 401)
        self.assertEqual(self.a.post(path, json=payload).status_code, 403)
        self.assertEqual(self.b.post(path, headers=self.other_headers, json=payload).status_code, 404)
        self.assertEqual(self.state(self.b, self.other_headers).status_code, 404)
        result = self.state()
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(set(result.json()), {"schema_version", "conversation_id", "revision", "task", "preferences", "skills", "migration_ready"})
        self.assertTrue(result.json()["migration_ready"])
        self.assertIsNone(result.json()["task"])
        self.assertEqual(self.provider.calls, [])

    def test_missing_migration_has_readonly_state_and_no_turn_or_provider(self):
        with self.engine.begin() as db:
            db.execute(text("DROP TABLE assistant_preferences"))
            db.execute(text("DROP TABLE assistant_tasks"))
            db.execute(text("DELETE FROM storage_metadata WHERE `key`='assistant_version'"))
        self.assertFalse(self.state().json()["migration_ready"])
        response = self.submit("你好")
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["detail"]["code"], "assistant_migration_required")
        self.assertEqual(self.chat.get(self.owner, self.view["id"])["revision"], 0)
        self.assertFalse(self.provider.calls)

    def test_update_uses_native_tool_and_existing_provenance_then_persists(self):
        original = "去上海，两个人。"
        requirements = Intent(destination_id="shanghai", travelers=2).model_dump()
        selection = {"requirements": requirements, "sources": [
            {"field": "destination_id", "quote": "上海"}, {"field": "travelers", "quote": "两个人"}], "issues": []}
        self.provider.answers = [tool("update_trip_requirements", selection), ProviderResponse(content="已整理，请核对需求卡。", output_tokens=20)]
        op = operation()
        response = self.submit(original, op=op)
        self.assertEqual(response.status_code, 200, response.text)
        checked = Conversation.model_validate(response.json())
        self.assertEqual(checked.requirements.travelers, 2)
        self.assertEqual(checked.requirements.destination_id, "shanghai")
        self.assertTrue(checked.messages[-1].text.startswith("助手建议（尚需核实）\n"))
        task = self.tasks.latest(self.owner, self.view["id"])
        self.assertFalse(task["stale"])
        self.assertEqual(task["conversation_revision"], checked.revision)
        self.assertEqual(self.state().json()["task"]["model_calls"], 2)
        self.assertEqual(self.submit(original, op=op).status_code, 409)
        self.assertEqual(len(self.provider.calls), 2)
        self.assertNotIn("上海", str(task["checkpoint"]["trace"]))

    def test_bad_provenance_fails_both_receipts_without_partial_changes(self):
        self.provider.answers = [tool("update_trip_requirements", {"requirements": Intent(travelers=2).model_dump(),
                                   "sources": [{"field": "travelers", "quote": "从未说过的内容"}], "issues": []})]
        response = self.submit("你好")
        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(self.chat.get(self.owner, self.view["id"])["last_turn"]["state"], "failed")
        self.assertEqual(self.tasks.latest(self.owner, self.view["id"])["state"], "failed")
        self.assertIsNone(self.chat.get(self.owner, self.view["id"])["requirements"]["travelers"])

    def test_search_publishes_after_turn_not_during_busy_and_never_confirms(self):
        self.set_shanghai()
        self.provider.answers = [tool("search_places", {"city_id": "310000", "keyword": "上海博物馆", "role": "required"})]
        place = _place({"id": "controlled-poi", "name": "受控博物馆", "address": "受控地址", "location": "121.45,31.23", "type": "博物馆",
                        "citycode": "021", "cityname": "上海市", "adcode": "310101"}, "310000")
        queried = []
        async def search(_self, keyword, city):
            queried.append((keyword, city))
            self.assertEqual(self.chat.get(self.owner, self.view["id"])["last_turn"]["state"], "running")
            return [place]
        original_begin = self.places.begin_operation
        def begin(*args, **kwargs):
            self.assertEqual(self.chat.get(self.owner, self.view["id"])["last_turn"]["state"], "succeeded")
            return original_begin(*args, **kwargs)
        with patch("api.assistant.ChatPlacesClient.search", search), patch.object(self.places, "begin_operation", side_effect=begin):
            response = self.submit("帮我搜索上海博物馆")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(queried, [("上海博物馆", "310000")])
        workspace = self.places.get(self.owner, self.view["id"])
        self.assertFalse(workspace["stale"])
        self.assertEqual(workspace["batch"]["places"][0]["id"], "controlled-poi")
        self.assertEqual(workspace["required_places"], [])
        self.assertIsNone(workspace["accommodation"])

    def test_search_not_explicit_and_cross_city_never_query(self):
        self.set_shanghai()
        for statement, city in (("不要搜索上海博物馆", "310000"), ("搜索上海博物馆", "110000")):
            self.view = self.chat.get(self.owner, self.view["id"])
            self.provider.answers = [tool("search_places", {"city_id": city, "keyword": "上海博物馆", "role": "required"})]
            with patch("api.assistant.ChatPlacesClient.search", side_effect=AssertionError("must not search")):
                response = self.submit(statement)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn("未", response.json()["messages"][-1]["text"])

    def test_real_persistent_search_quota_keeps_429_wait_headers_and_no_http_dispatch(self):
        self.set_shanghai()
        fixture = UsageFixture()
        self.addCleanup(fixture.close)
        fixture.store.settings.business_daily_limits["amap_poi"] = DailyLimit(account=1, **{"global": 10})
        # A prior uncertain reservation consumes an attempt; no upstream request
        # is made to prepare this controlled, independent quota store.
        fixture.store.reserve(self.owner, "amap_poi")
        self.provider.answers = [tool("search_places", {"city_id": "310000", "keyword": "上海博物馆", "role": "required"})]
        with patch("core.usage_runtime.get_usage_store", return_value=fixture.store):
            response = self.submit("搜索上海博物馆")
        self.assertEqual(response.status_code, 429, response.text)
        self.assertEqual(response.json()["detail"]["code"], "business_quota_exhausted")
        self.assertEqual(response.headers["x-business-usage-code"], "business_quota_exhausted")
        self.assertEqual(response.headers["x-business-usage-service"], "amap_poi")
        self.assertEqual(response.headers["x-business-usage-scope"], "account")
        self.assertGreater(int(response.headers["retry-after"]), 0)
        self.assertIn("x-business-usage-reset-at", response.headers)
        self.assertEqual(fixture.store.own_usage(self.owner)["services"]["amap_poi"]["used"], 1)
        latest = self.tasks.latest(self.owner, self.view["id"])
        self.assertEqual(latest["state"], "failed")
        self.assertEqual(latest["checkpoint"]["stop_reason"], "business_quota_exhausted")
        self.assertEqual(self.state().json()["task"]["model_calls"], 1)
        self.assertEqual(self.state().json()["task"]["tool_calls"], 1)
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(self.chat.get(self.owner, self.view["id"])["last_turn"]["state"], "failed")

    def test_ordinary_turn_preserves_valid_checkpoint_and_explicit_preferences_only(self):
        self.set_shanghai()
        old = self.tasks.begin(self.owner, self.view["id"], operation(), self.view["revision"])
        self.tasks.checkpoint(self.owner, old["id"], old["revision"], {"workspace": {"input": "same"}, "plan": {"controlled": "reference"}}, "waiting")
        self.tasks.set_preference(self.owner, "travel_preferences", "希望少走路", consent=True)
        self.provider.answers = [ProviderResponse(content="可以，请继续。", output_tokens=10)]
        response = self.submit("谢谢")
        self.assertEqual(response.status_code, 200, response.text)
        latest = self.tasks.latest(self.owner, self.view["id"])
        self.assertEqual(latest["checkpoint"]["plan"], {"controlled": "reference"})
        self.assertEqual(latest["checkpoint"]["workspace"], {"input": "same"})
        self.assertIn("希望少走路", self.provider.calls[0]["messages"][1]["content"])
        self.assertEqual(self.state().json()["preferences"], [])

    def test_failure_does_not_leave_busy_and_next_operation_can_start(self):
        self.provider.answers = [RuntimeFailure("truncated")]
        response = self.submit("你好")
        self.assertEqual(response.status_code, 502)
        failed = self.chat.get(self.owner, self.view["id"])
        self.assertEqual(failed["last_turn"]["state"], "failed")
        self.assertEqual(self.tasks.latest(self.owner, self.view["id"])["state"], "failed")
        self.provider.answers = [tool("request_clarification", {"reason": "missing_information"})]
        response = self.submit("继续", view=failed)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.tasks.latest(self.owner, self.view["id"])["state"], "waiting")

    def test_requirement_change_discards_old_plan_and_preserves_sources(self):
        self.set_shanghai()
        old = self.tasks.begin(self.owner, self.view["id"], operation(), self.view["revision"])
        self.tasks.checkpoint(self.owner, old["id"], 0, {"workspace": {"old": True}, "plan": {"controlled": "old"}}, "waiting")
        changed = self.view["requirements"] | {"travelers": 3}
        self.provider.answers = [tool("update_trip_requirements", {"requirements": changed,
            "sources": [{"field": "travelers", "quote": "3人"}], "issues": []}), ProviderResponse(content="已整理。", output_tokens=10)]
        response = self.submit("改为3人")
        self.assertEqual(response.status_code, 200, response.text)
        latest = self.tasks.latest(self.owner, self.view["id"])
        self.assertNotIn("plan", latest["checkpoint"])
        self.assertNotIn("workspace", latest["checkpoint"])
        self.assertEqual(response.json()["sources"], [{"field": "travelers", "quote": "3人"}])

    def test_stale_confirmed_places_are_not_sent_as_current_context(self):
        self.seed_confirmed_place()
        reserved = self.chat.begin_turn(self.owner, self.view["id"], operation(), self.view["revision"], "受控失效")
        self.view = self.chat.fail_turn(self.owner, self.view["id"], reserved["conversation"]["last_turn"]["operation_id"], reserved["revision"])
        self.assertTrue(self.places.get(self.owner, self.view["id"])["stale"])
        self.provider.answers = [ProviderResponse(content="请核对地点。", output_tokens=10)]
        response = self.submit("你好")
        self.assertEqual(response.status_code, 200, response.text)
        context = json.loads(self.provider.calls[0]["messages"][1]["content"])["context"]
        self.assertEqual(context["confirmed_places"]["required_places"], [])
        self.assertEqual(context["confirmed_places"]["state"], "requires_sync_and_confirmation")

    def test_city_update_then_inspection_cannot_read_old_city_places(self):
        self.seed_confirmed_place()
        selection = {"requirements": self.view["requirements"] | {"destination_id": "beijing"},
                     "sources": [{"field": "destination_id", "quote": "北京"}], "issues": []}
        self.provider.answers = [tool("update_trip_requirements", selection), tool("inspect_trip", {}),
                                 ProviderResponse(content="请重新确认北京地点。", output_tokens=10)]
        response = self.submit("改为北京")
        self.assertEqual(response.status_code, 200, response.text)
        inspected = json.loads(self.provider.calls[2]["messages"][-1]["content"])
        self.assertEqual(inspected["confirmed_places"]["required_places"], [])
        self.assertEqual(inspected["confirmed_places"]["city_id"], "110000")
        self.assertNotIn("旧城已确认地点", str(inspected))

    def test_unvalidated_checkpoint_never_becomes_formal_day_schedule(self):
        old = self.tasks.begin(self.owner, self.view["id"], operation(), self.view["revision"])
        self.tasks.checkpoint(self.owner, old["id"], 0, {"plan": {"days": [{"date": "2026-10-25", "items": [{"name": "伪造计划"}]}]}}, "waiting")
        self.provider.answers = [tool("get_day_schedule", {"date": "2026-10-25"})]
        response = self.submit("查看2026-10-25的安排")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("本地工作区", response.json()["messages"][-1]["text"])
        self.assertNotIn("伪造计划", response.json()["messages"][-1]["text"])

    def test_cancel_before_task_registration_cannot_start_model_or_leave_task_busy(self):
        original = self.chat.begin_turn
        def cancel_after_reservation(*args):
            reserved = original(*args)
            self.chat.cancel_turn(self.owner, self.view["id"], args[2])
            return reserved
        with patch.object(self.chat, "begin_turn", side_effect=cancel_after_reservation):
            response = self.submit("你好")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.chat.get(self.owner, self.view["id"])["last_turn"]["state"], "cancelled")
        self.assertEqual(self.provider.calls, [])

    def test_cancel_uses_shared_active_map_and_closes_task(self):
        op = operation()
        self.provider.answers = [ProviderResponse(content="这个晚响应不应提交", output_tokens=10)]
        self.provider.pause = True
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.submit, "你好", op=op)
            self.assertTrue(self.provider.entered.wait(2))
            cancelled = self.a.post(f"/chat/conversations/{self.view['id']}/cancel", headers=self.headers, json={"operation_id": op})
            self.assertEqual(cancelled.status_code, 200, cancelled.text)
            self.provider.release.set()
            try:
                future.result(timeout=3)
            except FutureCancelledError:
                pass
        latest = self.chat.get(self.owner, self.view["id"])
        self.assertEqual(latest["last_turn"]["state"], "cancelled")
        self.assertEqual(self.tasks.latest(self.owner, self.view["id"])["state"], "cancelled")
        self.assertFalse(any(item["text"] == "这个晚响应不应提交" for item in latest["messages"]))

    def test_chat_commit_then_publish_failure_is_uncertain_not_successful_wait(self):
        self.set_shanghai()
        self.provider.answers = [ProviderResponse(content="本轮已整理建议。", output_tokens=10)]
        with patch.object(self.places, "sync", side_effect=ChatError("controlled_publication_failure")):
            response = self.submit("你好")
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["detail"]["code"], "assistant_post_commit_readback_required")
        self.assertEqual(self.chat.get(self.owner, self.view["id"])["last_turn"]["state"], "succeeded")
        task = self.tasks.latest(self.owner, self.view["id"])
        self.assertEqual(task["state"], "uncertain")
        self.assertEqual(task["checkpoint"]["stop_reason"], "post_commit_uncertain")
        self.assertEqual(task["checkpoint"]["continuation"]["kind"], "read_receipts_only")
        self.assertEqual(len(self.provider.calls), 1)

    def test_candidate_publication_failure_closes_reserved_place_as_uncertain(self):
        self.set_shanghai()
        self.provider.answers = [tool("search_places", {"city_id": "310000", "keyword": "上海博物馆", "role": "required"})]
        place = _place({"id": "controlled-poi", "name": "受控博物馆", "address": "受控地址", "location": "121.45,31.23", "type": "博物馆",
                        "citycode": "021", "cityname": "上海市", "adcode": "310101"}, "310000")
        async def search(_self, keyword, city):
            return [place]
        original = self.places.finish_operation
        states = []
        def fail_publication(*args, **kwargs):
            states.append("publish" if kwargs.get("batch") is not None else kwargs.get("error_code"))
            if kwargs.get("batch") is not None:
                raise ChatError("controlled_publication_failure")
            return original(*args, **kwargs)
        with patch("api.assistant.ChatPlacesClient.search", search), patch.object(self.places, "finish_operation", side_effect=fail_publication):
            response = self.submit("搜索上海博物馆")
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(states, ["publish", "request_uncertain"])
        self.assertEqual(self.tasks.latest(self.owner, self.view["id"])["state"], "uncertain")
        current = self.places.get(self.owner, self.view["id"])
        self.assertEqual(current["last_operation"]["state"], "uncertain")
        self.assertEqual(current["required_places"], [])
        self.assertEqual(len(self.provider.calls), 1)

    def test_finalize_failure_before_commit_records_uncertainty_without_resending(self):
        self.provider.answers = [ProviderResponse(content="请继续核对。", output_tokens=10)]
        original = self.tasks.finalize
        invocations = []
        def fail_once(*args, **kwargs):
            invocations.append(args[-1])
            if len(invocations) == 1:
                raise ChatError("controlled_finalize_failure")
            return original(*args, **kwargs)
        with patch.object(self.tasks, "finalize", side_effect=fail_once):
            response = self.submit("你好")
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(invocations, ["succeeded", "uncertain"])
        self.assertEqual(self.tasks.latest(self.owner, self.view["id"])["state"], "uncertain")
        self.assertEqual(len(self.provider.calls), 1)

    def test_finalize_commit_ack_loss_reads_existing_receipt_without_overwriting(self):
        self.provider.answers = [ProviderResponse(content="请继续核对。", output_tokens=10)]
        original = self.tasks.finalize
        invocations = []
        def commit_then_lose_ack(*args, **kwargs):
            invocations.append(args[-1])
            original(*args, **kwargs)
            raise ChatError("controlled_finalize_ack_lost")
        op = operation()
        with patch.object(self.tasks, "finalize", side_effect=commit_then_lose_ack):
            response = self.submit("你好", op=op)
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(invocations, ["succeeded"])
        recorded = self.tasks.latest(self.owner, self.view["id"])
        self.assertEqual(recorded["state"], "succeeded")
        self.assertEqual(recorded["revision"], 1)
        self.assertEqual(self.submit("你好", op=op).status_code, 409)
        self.assertEqual(len(self.provider.calls), 1)

    def test_post_commit_cancellation_preserves_chat_and_marks_followup_uncertain(self):
        self.provider.answers = [ProviderResponse(content="请继续核对。", output_tokens=10)]
        with patch.object(self.places, "sync", side_effect=asyncio.CancelledError):
            try:
                self.submit("你好")
            except (FutureCancelledError, asyncio.CancelledError):
                pass
        self.assertEqual(self.chat.get(self.owner, self.view["id"])["last_turn"]["state"], "succeeded")
        task = self.tasks.latest(self.owner, self.view["id"])
        self.assertEqual(task["state"], "uncertain")
        self.assertEqual(task["checkpoint"]["stop_reason"], "post_commit_uncertain")
        self.assertEqual(len(self.provider.calls), 1)

    def test_restart_cancel_stale_task_projects_uncertain_and_never_resumes(self):
        op = operation()
        reserved = self.chat.begin_turn(self.owner, self.view["id"], op, self.view["revision"], "重启前的受控请求")
        task = self.tasks.begin(self.owner, self.view["id"], op, reserved["revision"])
        self.assertNotIn((self.owner, self.view["id"], op), chat._ACTIVE)
        cancelled = self.a.post(f"/chat/conversations/{self.view['id']}/cancel", headers=self.headers, json={"operation_id": op})
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        state = self.state().json()["task"]
        self.assertEqual(state["state"], "uncertain")
        self.assertEqual(state["stop_reason"], "context_changed")
        self.assertEqual(self.tasks.get(self.owner, task["id"])["state"], "running")
        self.assertFalse(self.provider.calls)
        self.provider.answers = [tool("request_clarification", {"reason": "missing_information"})]
        response = self.submit("继续新请求", view=cancelled.json())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(self.submit("重启前的受控请求", op=op).status_code, 409)
        self.assertEqual(len(self.provider.calls), 1)
