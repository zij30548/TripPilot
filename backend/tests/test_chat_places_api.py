"""Real auth, temporary persistence and durable metering, controlled upstreams.

No request leaves MockTransport; this proves wiring, not model interpretation or
real national POI availability.
"""
import asyncio
from concurrent.futures import CancelledError as FutureCancelledError, ThreadPoolExecutor
import json
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

from app.api.chat_places import get_chat_places_store
from app.business_usage import UsageError
from app.chat_places_storage import ChatPlacesStore
from main import app
from tests import test_chat_api as chat_fixture
from tests.test_chat_places_tools import envelope
from tools.chat_places_storage import provision_chat_places_extension


def poi(identity="controlled-poi", *, citycode="021", cityname="上海市", adcode="310106"):
    return {"id": identity, "name": "受控测试地点", "address": "受控地址，不是真实查询", "location": "121.45,31.23",
            "type": "风景名胜", "citycode": citycode, "cityname": cityname, "adcode": adcode}


class ChatPlacesAPITests(unittest.TestCase):
    login = chat_fixture.ChatAPIBoundaryTests.login
    create = chat_fixture.ChatAPIBoundaryTests.create
    dispose = chat_fixture.ChatAPIBoundaryTests.dispose
    usage_rows = chat_fixture.ChatAPIBoundaryTests.usage_rows

    def setUp(self):
        chat_fixture.ChatAPIBoundaryTests.setUp(self)
        from pydantic import SecretStr
        self.settings.amap_web_key = SecretStr("controlled-not-a-real-key")
        provision_chat_places_extension(self.engine)
        self.places = ChatPlacesStore(self.engine)
        self.enterContext(patch.dict(app.dependency_overrides, {get_chat_places_store: lambda: self.places}))
        self.headers = self.login(self.a)
        self.conversation = self.create(self.headers)
        self.view = self.a.get(self.path_for(), headers=self.headers).json()

    async def upstream(self, request):
        self.calls.append(request)
        result = self.results.pop(0)
        if self.pause_next:
            self.pause_next = False
            self.entered.set()
            while not self.release.is_set():
                await asyncio.sleep(.005)
        if isinstance(result, BaseException):
            raise result
        return httpx.Response(200, json=result)

    def path_for(self):
        return f"/chat/conversations/{self.conversation['id']}/places"

    def command(self, action, *, view=None, op=None, client=None, headers=None, **fields):
        current = self.view if view is None else view
        result = (client or self.a).post(self.path_for(), headers=headers or self.headers,
            json={"action": action, "operation_id": op or str(uuid4()),
                  "expected_intent_revision": self.conversation["revision"],
                  "expected_revision": current["revision"], **fields})
        if result.status_code == 200 and client is None:
            self.view = result.json()
        return result

    def set_city(self):
        response = self.command("set_city", city_id="310000")
        self.assertEqual(response.status_code, 200, response.text)

    def test_local_catalog_get_and_city_confirmation_do_not_dispatch(self):
        response = self.a.get("/chat/cities?keyword=苏州")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["items"][0]["id"], "320500")
        self.assertEqual(len(self.calls), 0)
        self.assertIsNone(self.view["city_id"])
        self.set_city()
        self.assertEqual(self.view["city_id"], "310000")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.usage_rows(), [])

    def test_explicit_search_preview_confirmation_and_replay_only_send_once(self):
        self.set_city()
        self.results.append({"status": "1", "pois": [poi()]})
        before, op = self.view, str(uuid4())
        result = self.command("search", keyword="测试地点", role="required", op=op)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.view["required_places"], [])
        batch = self.view["batch"]
        self.assertEqual(batch["places"][0]["id"], "controlled-poi")
        replay = self.command("search", view=before, keyword="测试地点", role="required", op=op)
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.usage_rows()[0][1], "amap_poi")
        result = self.command("confirm", batch_id=batch["id"], place_id="controlled-poi", role="required")
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.view["required_places"][0], batch["places"][0])
        self.assertEqual(len(self.calls), 1)
        forged = self.command("confirm", batch_id=batch["id"], place_id="forged", role="required")
        self.assertEqual(forged.status_code, 409)

    def test_native_tool_search_is_single_model_then_one_logical_poi_query(self):
        self.set_city()
        self.results.extend([envelope(), {"status": "1", "pois": [poi()]}])
        result = self.command("ask", text="帮我搜索上海博物馆，想去")
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.view["last_operation"]["state"], "succeeded")
        self.assertEqual(self.view["required_places"], [])
        self.assertEqual([call.method for call in self.calls], ["POST", "GET"])
        payload = json.loads(self.calls[0].content)
        self.assertIn("tools", payload)
        self.assertNotIn("response_format", payload)
        self.assertEqual(self.calls[1].url.params["region"], "310000")
        self.assertEqual([row[1] for row in self.usage_rows()], ["qwen_text", "amap_poi"])

    def test_cross_city_tool_returns_proposal_without_search_or_city_change(self):
        self.set_city()
        self.results.append(envelope(arguments={"city_id": "110000", "keyword": "颐和园", "role": "required"}))
        result = self.command("ask", text="改去北京，看看颐和园")
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.view["clarification"], "city_confirmation_required")
        self.assertEqual(self.view["proposed_city_id"], "110000")
        self.assertEqual(self.view["city_id"], "310000")
        self.assertIsNone(self.view["batch"])
        self.assertEqual(len(self.calls), 1)

    def test_multi_city_and_unsupported_clarifications_preserve_text_without_executing(self):
        self.set_city()
        for reason, text in (("multi_city", "上海两天杭州三天"), ("unsupported", "帮我付款并自动保存")):
            self.results.append(envelope("request_clarification", {"reason": reason}))
            result = self.command("ask", text=text)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(self.view["request_text"], text)
            self.assertEqual(self.view["clarification"], reason)
            self.assertIsNone(self.view["batch"])
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(all(call.method == "POST" for call in self.calls))

    def test_bad_native_output_and_bad_region_fail_without_confirmable_candidates(self):
        self.set_city()
        self.results.append(envelope("confirm_place", {"place_id": "forged"}))
        result = self.command("ask", text="确认地点")
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.view["last_operation"]["error_code"], "model_structure")
        self.assertIsNone(self.view["batch"])
        self.results.append({"status": "1", "pois": [poi(citycode="010", cityname="北京市", adcode="110108")]})
        result = self.command("search", keyword="测试地点", role="required")
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.view["last_operation"]["error_code"], "chat_place_region_error")
        self.assertEqual(self.view["required_places"], [])

    def test_owner_csrf_and_extra_owner_field_reject_before_dispatch(self):
        self.set_city()
        self.assertEqual(self.c.get(self.path_for()).status_code, 401)
        other_headers = self.login(self.b, "b")
        self.assertEqual(self.b.get(self.path_for()).status_code, 404)
        self.assertEqual(self.command("search", client=self.b, headers=other_headers,
                                     keyword="任意", role="required").status_code, 404)
        invalid = self.a.post(self.path_for(), headers=self.headers,
            json={"action": "search", "operation_id": str(uuid4()), "expected_revision": self.view["revision"],
                  "expected_intent_revision": 0, "keyword": "任意", "role": "required", "owner_id": "forged"})
        self.assertEqual(invalid.status_code, 422)
        bad = self.a.post(self.path_for(), json={"action": "search"})
        self.assertEqual(bad.status_code, 403)
        self.assertEqual(self.calls, [])

    def test_cancel_running_native_request_prevents_late_candidates_and_operation_reuse(self):
        self.set_city()
        before, op = self.view, str(uuid4())
        self.results.append(envelope())
        self.pause_next = True
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(lambda: self.command("ask", view=before, op=op, text="搜索博物馆"))
            self.assertTrue(self.entered.wait(3))
            # begin_operation's committed revision is exactly +1; not a second
            # model request. Another authenticated client cancels the same op.
            cancelled = self.a.post(self.path_for(), headers=self.headers,
                json={"action": "cancel", "operation_id": op, "expected_intent_revision": 0,
                      "expected_revision": before["revision"] + 1})
            self.release.set()
            self.assertEqual(cancelled.status_code, 200, cancelled.text)
            try:
                response = future.result(timeout=3)
            except FutureCancelledError:
                # Closing an ASGI request can terminate it without an HTTP
                # response; the durable receipt remains the authority.
                pass
            else:
                self.assertIn(response.status_code, (409, 499))
        latest = self.places.get(self.first_id, self.conversation["id"])
        self.assertEqual(latest["last_operation"]["state"], "cancelled")
        self.assertIsNone(latest["batch"])
        self.assertEqual(len(self.calls), 1)
        self.assertNotEqual(self.usage_rows()[0][2], "reserved")
        retry = self.command("ask", view=before, op=op, text="搜索博物馆")
        self.assertIn(retry.status_code, (200, 409))
        self.assertEqual(len(self.calls), 1)

    def test_cancel_before_active_registration_blocks_model_and_poi(self):
        self.set_city()
        real_begin = self.places.begin_operation
        def cancel_after_begin(*arguments):
            reservation = real_begin(*arguments)
            self.places.cancel(arguments[0], arguments[1], arguments[2], reservation["revision"])
            return reservation
        with patch.object(self.places, "begin_operation", side_effect=cancel_after_begin):
            result = self.command("ask", text="搜索博物馆")
        self.assertEqual(result.status_code, 409)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.usage_rows(), [])
        self.assertEqual(self.places.get(self.first_id, self.conversation["id"])["last_operation"]["state"], "cancelled")

    def test_quota_failure_before_send_persists_failure_not_running(self):
        self.set_city()
        with patch("app.usage_runtime.get_usage_store", return_value=self.usage_store), patch.object(
                self.usage_store, "reserve", side_effect=UsageError("business_quota_exhausted", service="qwen_text", scope="account")):
            result = self.command("ask", text="搜索博物馆")
        self.assertEqual(result.status_code, 429, result.text)
        latest = self.places.get(self.first_id, self.conversation["id"])
        self.assertEqual(latest["last_operation"]["state"], "failed")
        self.assertEqual(latest["last_operation"]["error_code"], "business_quota_exhausted")
        self.assertEqual(self.calls, [])

    def test_operation_deadline_cancels_transport_without_retry_or_candidate(self):
        self.set_city()
        self.results.append(envelope())
        self.pause_next = True
        with patch("app.api.chat_places.MAX_OPERATION_SECONDS", 1):
            result = self.command("ask", text="搜索博物馆")
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.view["last_operation"]["error_code"], "operation_timeout")
        self.assertEqual(self.view["last_operation"]["state"], "failed")
        self.assertIsNone(self.view["batch"])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.usage_rows()[0][2], "cancelled")


if __name__ == "__main__":
    unittest.main()
