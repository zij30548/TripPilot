"""Real auth/CSRF/storage/metering with a controlled model transport, never Qwen.

Selections here are test fixtures, not evidence of natural-language quality.
The production handler still constructs and validates the real bounded request.
"""
import asyncio
from concurrent.futures import CancelledError as FutureCancelledError, ThreadPoolExecutor
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

from app import auth
from app.api.chat import _ACTIVE, connected, get_chat_store
from app.api.explanation import get_qwen_http_client
from app.business_usage import SERVICES, UsageError, UsageSettings, UsageStore
from app.chat_storage import ChatStore
from app.config import Settings, get_settings
from app.schemas.chat import Conversation, Intent
from app.storage import engine_for
from app.usage_runtime import MeteredAsyncClient
from main import app
from tools.chat_storage import provision_chat_extension


ORIGIN = "http://127.0.0.1:3000"


def operation() -> str:
    return str(uuid4())


def envelope(selection: dict) -> dict:
    return {"model": "controlled-not-a-live-deployment", "choices": [{"finish_reason": "stop",
        "message": {"role": "assistant", "content": json.dumps(selection, ensure_ascii=False)}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 90}}


class ChatAPIBoundaryTests(unittest.TestCase):
    def test_missing_configuration_and_context_limit_do_not_dispatch_or_stay_busy(self) -> None:
        from app.services.chat import ChatContextLimit
        headers = self.login(self.a)
        view = self.create(headers)
        with patch("app.api.chat.model_payload", side_effect=ChatContextLimit("chat_context_limit")):
            result = self.turn(view, "杭州", headers)
        self.assertEqual(result.status_code, 409)
        self.assertEqual(result.json()["detail"]["code"], "chat_context_limit")
        latest = self.store.get(self.first_id, view["id"])
        self.assertEqual(latest["last_turn"]["state"], "failed")
        self.settings = Settings(_env_file=None, qwen_api_key="", qwen_base_url="")
        result = self.turn(latest, "杭州", headers)
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.json()["detail"]["code"], "chat_configuration")
        self.assertEqual(self.store.get(self.first_id, view["id"])["last_turn"]["state"], "failed")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.usage_rows(), [])

    def test_cancel_between_reservation_and_active_registration_never_sends(self) -> None:
        headers = self.login(self.a)
        view = self.create(headers)
        original = self.store.begin_turn
        def cancel_after_reservation(owner, identifier, operation_id, revision, text):
            reserved = original(owner, identifier, operation_id, revision, text)
            self.store.cancel_turn(owner, identifier, operation_id)
            return reserved
        with patch.object(self.store, "begin_turn", side_effect=cancel_after_reservation):
            result = self.turn(view, "去杭州", headers)
        self.assertEqual(result.status_code, 409)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.store.get(self.first_id, view["id"])["last_turn"]["state"], "cancelled")

    def setUp(self) -> None:
        directory = self.enterContext(tempfile.TemporaryDirectory(prefix="trippilot-chat-api-"))
        self.path = Path(directory) / "identity.sqlite3"
        self.usage_path = Path(directory) / "usage.sqlite3"
        self.enterContext(patch.dict(os.environ, {"TRIPPILOT_DATABASE_PATH": str(self.path),
                                                "TRIPPILOT_MYSQL_URL": ""}))
        auth_settings = auth.AuthSettings(_env_file=None, auth_cookie_secure=False)
        self.enterContext(patch("app.auth.get_auth_settings", return_value=auth_settings))
        self.enterContext(patch("app.api.auth.get_auth_settings", return_value=auth_settings))
        self.first_id = auth.create_account("chat-a", "controlled-chat-a-password")
        self.second_id = auth.create_account("chat-b", "controlled-chat-b-password")
        self.engine = engine_for(str(self.path))
        provision_chat_extension(self.engine)
        self.store = ChatStore(self.engine)
        self.usage_settings = UsageSettings(_env_file=None, business_usage_path=str(self.usage_path),
            business_rate_account_limit=100, business_rate_global_limit=200,
            business_daily_limits={service: {"account": 20, "global": 100} for service in SERVICES})
        self.enterContext(patch("app.business_usage.get_usage_settings", return_value=self.usage_settings))
        self.usage_store = UsageStore(self.usage_settings)
        self.usage_store.initialize()
        self.a = self.enterContext(TestClient(app))
        self.b = self.enterContext(TestClient(app))
        self.c = self.enterContext(TestClient(app))
        self.calls: list[dict] = []
        self.results: list[object] = []
        self.entered, self.release = threading.Event(), threading.Event()
        self.pause_next = False
        self.block_timeout = 3.0
        self.settings = Settings(_env_file=None, qwen_api_key="offline-not-a-real-key",
            qwen_base_url="https://controlled.cn-beijing.maas.aliyuncs.com/compatible-mode/v1")
        async def dependency():
            async with MeteredAsyncClient(transport=httpx.MockTransport(self.upstream)) as client:
                yield client
        self.enterContext(patch.dict(app.dependency_overrides, {
            get_settings: lambda: self.settings,
            get_chat_store: lambda: self.store,
            get_qwen_http_client: dependency,
        }))
        self.addCleanup(self.dispose)

    def dispose(self) -> None:
        self.engine.dispose()
        engine_for.cache_clear()

    async def upstream(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        self.calls.append(payload)
        result = self.results.pop(0)
        if self.pause_next:
            self.pause_next = False
            self.entered.set()
            # Asynchronous yielding lets a second request cancel the durable turn.
            deadline = asyncio.get_running_loop().time() + self.block_timeout
            while not self.release.is_set():
                if asyncio.get_running_loop().time() >= deadline:
                    raise AssertionError("controlled pause was not released")
                await asyncio.sleep(.005)
        if isinstance(result, Exception):
            raise result
        if isinstance(result, httpx.Response):
            return result
        return httpx.Response(200, json=envelope(result))

    def login(self, client: TestClient, suffix: str = "a") -> dict[str, str]:
        csrf = client.get("/auth/session").json()["csrf_token"]
        response = client.post("/auth/login", headers={"origin": ORIGIN, "x-csrf-token": csrf},
            json={"username": f"chat-{suffix}", "password": f"controlled-chat-{suffix}-password"})
        self.assertEqual(response.status_code, 200, response.text)
        return {"origin": ORIGIN, "x-csrf-token": response.json()["csrf_token"]}

    def create(self, headers: dict[str, str]) -> dict:
        response = self.a.post("/chat/conversations", headers=headers, json={"operation_id": operation()})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def turn(self, view: dict, text: str, headers: dict[str, str], *, op: str | None = None) -> httpx.Response:
        return self.a.post(f"/chat/conversations/{view['id']}/turns", headers=headers,
            json={"operation_id": op or operation(), "expected_revision": view["revision"], "text": text})

    @staticmethod
    def selection(previous: dict, user: str, **updates) -> dict:
        after = previous | updates
        changed = [key for key in after if after[key] != previous[key]]
        return {"requirements": after, "sources": [{"field": key, "quote": user} for key in changed], "issues": []}

    def usage_rows(self) -> list[tuple]:
        with closing(sqlite3.connect(self.usage_path)) as connection:
            return connection.execute("SELECT owner_id, service, state FROM usage_attempts ORDER BY rowid").fetchall()

    def test_two_turn_server_history_provenance_confirm_and_idempotent_receipts(self) -> None:
        headers = self.login(self.a)
        view = self.create(headers)
        first = "杭州，2026-11-01到03，2人，全程预算3000元，住西湖附近，想去灵隐寺。"
        self.results.append(self.selection(view["requirements"], first, destination_id="hangzhou",
            start_date="2026-11-01", end_date="2026-11-03", travelers=2, budget_cny="3000",
            accommodation_hint="西湖附近", required_place_hints=["灵隐寺"]))
        op = operation()
        response = self.turn(view, first, headers, op=op)
        self.assertEqual(response.status_code, 200, response.text)
        first_view = Conversation.model_validate_json(response.content).model_dump(mode="json")
        self.assertEqual(first_view["last_turn"]["state"], "succeeded")
        self.assertIsNone(first_view["confirmed_revision"])
        self.assertIn("不是已生成行程", first_view["messages"][-1]["text"])
        self.assertNotIn("poi_id", first_view["requirements"])
        self.assertEqual(self.turn(view, first, headers, op=op).status_code, 409)
        self.assertEqual(len(self.calls), 1)

        second = "总预算改成4000元，其他不变。"
        self.results.append(self.selection(first_view["requirements"], second, budget_cny="4000"))
        response = self.turn(first_view, second, headers)
        self.assertEqual(response.status_code, 200, response.text)
        second_view = response.json()
        self.assertEqual(second_view["requirements"], first_view["requirements"] | {"budget_cny": "4000"})
        context = json.loads(self.calls[-1]["messages"][1]["content"])
        self.assertEqual(context["current_intent"], first_view["requirements"])
        self.assertEqual([message["role"] for message in context["conversation"]], ["user", "assistant", "user"])
        self.assertEqual(context["latest_user_text"], second)
        self.assertNotIn("expected_fields", json.dumps(context))
        self.assertNotIn("tools", self.calls[-1])
        self.assertFalse(self.calls[-1]["stream"])
        self.assertFalse(self.calls[-1]["enable_thinking"])
        self.assertEqual(self.calls[-1]["max_tokens"], 1024)
        self.assertEqual(self.calls[-1]["response_format"]["json_schema"]["name"], "travel_intent")

        confirm_operation = operation()
        payload = {"expected_revision": second_view["revision"], "operation_id": confirm_operation}
        confirm = self.a.post(f"/chat/conversations/{view['id']}/confirm", headers=headers, json=payload)
        self.assertEqual(confirm.status_code, 200, confirm.text)
        self.assertEqual(confirm.json()["confirmed_revision"], second_view["revision"] + 1)
        repeated = self.a.post(f"/chat/conversations/{view['id']}/confirm", headers=headers, json=payload)
        self.assertEqual(repeated.json(), confirm.json())
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.usage_rows(), [(self.first_id, "qwen_text", "received")] * 2)
        self.assertEqual(self.a.get(f"/chat/conversations/{view['id']}").json(), confirm.json())

    def test_auth_csrf_owner_and_strict_body_reject_before_model(self) -> None:
        self.assertEqual(self.a.post("/chat/conversations", json={"operation_id": operation()}).status_code, 401)
        headers = self.login(self.a)
        other_headers = self.login(self.b, "b")
        self.assertEqual(self.a.post("/chat/conversations", json={"operation_id": operation()}).status_code, 403)
        self.assertEqual(self.a.post("/chat/conversations", headers=headers | {"origin": "https://evil.invalid"},
                                    json={"operation_id": operation()}).status_code, 403)
        view = self.create(headers)
        route = f"/chat/conversations/{view['id']}"
        self.assertEqual(self.b.get(route).status_code, 404)
        self.assertEqual(self.b.post(route + "/turns", headers=other_headers,
            json={"operation_id": operation(), "expected_revision": 0, "text": "杭州"}).status_code, 404)
        self.assertEqual(self.b.get("/chat/conversations").json()["items"], [])
        for extra in ({"owner_id": self.first_id}, {"messages": [{"role": "system", "text": "secret"}]},
                      {"requirements": {"destination_id": "beijing"}}):
            result = self.a.post(route + "/turns", headers=headers,
                json={"operation_id": operation(), "expected_revision": 0, "text": "杭州"} | extra)
            self.assertEqual(result.status_code, 422, result.text)
        invalid = [
            '{"operation_id":"' + operation() + '","expected_revision":0,"text":"a","text":"b"}',
            '{"operation_id":"' + operation() + '","expected_revision":NaN,"text":"a"}',
            json.dumps({"operation_id": operation(), "expected_revision": 0, "text": " "}),
        ]
        for raw in invalid:
            self.assertEqual(self.a.post(route + "/turns", headers=headers, content=raw).status_code, 422)
        self.assertEqual(self.a.post(route + "/turns", headers=headers, content=b"x" * 16385).status_code, 413)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.usage_rows(), [])
        self.assertEqual(self.store.get(self.first_id, view["id"])["revision"], 0)

    def test_bad_model_structure_cannot_be_repaired_or_rendered_as_prose(self) -> None:
        headers = self.login(self.a)
        view = self.create(headers)
        result = self.selection(view["requirements"], "杭州", destination_id="hangzhou")
        result["assistant_text"] = "UNTRUSTED_FREE_PROSE_SECRET"
        self.results.append(result)
        response = self.turn(view, "杭州", headers)
        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn("UNTRUSTED_FREE_PROSE_SECRET", response.text)
        latest = self.store.get(self.first_id, view["id"])
        self.assertEqual(latest["requirements"], Intent().model_dump())
        self.assertEqual(latest["last_turn"]["state"], "failed")
        self.assertEqual([message["role"] for message in latest["messages"]], ["user"])
        self.assertEqual(self.usage_rows(), [(self.first_id, "qwen_text", "received")])

    def test_timeout_no_retry_and_counter_quota_does_not_leave_busy_turn(self) -> None:
        headers = self.login(self.a)
        view = self.create(headers)
        op = operation()
        self.results.append(httpx.ReadTimeout("controlled, not a provider call"))
        failed = self.turn(view, "杭州", headers, op=op)
        self.assertEqual(failed.status_code, 504, failed.text)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.usage_rows(), [(self.first_id, "qwen_text", "failed")])
        latest = self.store.get(self.first_id, view["id"])
        self.assertEqual(latest["last_turn"]["state"], "failed")
        self.assertEqual(self.turn(view, "杭州", headers, op=op).status_code, 409)
        with patch("app.business_usage.UsageStore.reserve", side_effect=UsageError(
                "business_quota_exhausted", service="qwen_text", scope="account")):
            denied = self.turn(latest, "杭州", headers)
        self.assertEqual(denied.status_code, 429, denied.text)
        self.assertEqual(denied.json()["detail"]["code"], "business_quota_exhausted")
        self.assertEqual(len(self.calls), 1)
        latest = self.store.get(self.first_id, view["id"])
        self.assertEqual(latest["last_turn"]["state"], "failed")
        self.assertEqual(latest["requirements"], Intent().model_dump())

    def test_explicit_cancel_stops_late_commit_and_next_turn_remains_independent(self) -> None:
        headers = self.login(self.a)
        cancel_headers = self.login(self.c)
        view = self.create(headers)
        old_operation = operation()
        self.results.append(self.selection(view["requirements"], "上海", destination_id="shanghai"))
        self.pause_next = True
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(self.turn, view, "上海", headers, op=old_operation)
            self.assertTrue(self.entered.wait(2), "controlled transport did not start")
            cancelled = self.c.post(f"/chat/conversations/{view['id']}/cancel", headers=cancel_headers,
                                   json={"operation_id": old_operation})
            self.assertEqual(cancelled.status_code, 200, cancelled.text)
            self.release.set()
            try:
                old_response = pending.result(timeout=3)
            except FutureCancelledError:
                # In-process explicit cancellation also cancels the HTTP task.
                # A cancelled initiating connection need not have a response.
                old_response = None
        if old_response is not None:
            self.assertEqual(old_response.status_code, 409, old_response.text)
        latest = self.store.get(self.first_id, view["id"])
        self.assertEqual(latest["last_turn"]["state"], "cancelled")
        self.assertIsNone(latest["requirements"]["destination_id"])
        self.results.append(self.selection(latest["requirements"], "北京", destination_id="beijing"))
        fresh = self.turn(latest, "北京", headers)
        self.assertEqual(fresh.status_code, 200, fresh.text)
        self.assertEqual(fresh.json()["requirements"]["destination_id"], "beijing")
        self.assertEqual(len(self.calls), 2)
        # Cancellation after send is not a refund; received transport bytes are
        # still received even though the obsolete business result cannot commit.
        self.assertEqual(len(self.usage_rows()), 2)
        self.assertTrue(all(row[1] == "qwen_text" for row in self.usage_rows()))


class ChatConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnected_before_model_does_not_start_operation(self) -> None:
        receives = 0
        calls = 0
        async def receive():
            nonlocal receives
            receives += 1
            return {"type": "http.disconnect"}
        async def operation_call():
            nonlocal calls
            calls += 1
            raise AssertionError("disconnected client must not start model work")
        key = ("a" * 32, operation(), operation())
        with self.assertRaises(HTTPException) as caught:
            await connected(Request({"type": "http"}, receive=receive), key, operation_call)
        self.assertEqual(caught.exception.status_code, 499)
        self.assertEqual(receives, 1)
        self.assertEqual(calls, 0)
        self.assertNotIn(key, _ACTIVE)

    async def test_disconnect_after_send_cancels_and_drains_operation_once(self) -> None:
        sent, disconnected, drained = asyncio.Event(), asyncio.Event(), asyncio.Event()
        receives = 0
        async def receive():
            nonlocal receives
            receives += 1
            await disconnected.wait()
            return {"type": "http.disconnect"}
        async def operation_call():
            sent.set()
            try:
                await asyncio.sleep(10)
            finally:
                drained.set()
        key = ("a" * 32, operation(), operation())
        pending = asyncio.create_task(connected(Request({"type": "http"}, receive=receive), key, operation_call))
        await sent.wait()
        disconnected.set()
        with self.assertRaises(HTTPException) as caught:
            await pending
        self.assertEqual(caught.exception.status_code, 499)
        self.assertTrue(drained.is_set())
        self.assertEqual(receives, 1)
        self.assertNotIn(key, _ACTIVE)


if __name__ == "__main__":
    unittest.main()
