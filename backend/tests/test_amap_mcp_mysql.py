"""Production ASGI/workspace + real temporary MySQL + SDK, controlled MCP wire.

No model, real AMap service, user database or acceptance ledger is used. The
only transport replacement is an httpx MockTransport below the real MCP SDK.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import CancelledError as FutureCancelledError, ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import text

if any(importlib.util.find_spec(name) is None for name in ("mcp", "jsonschema")):
    raise unittest.SkipTest("requires explicitly installed amap-mcp optional dependencies")

from core.business_usage import DailyLimit
from core import usage_runtime
from core.config import Settings, get_settings
from memory.chat_places_storage import ChatPlacesStore
from tests.mysql_support import temporary_mysql
from tests.test_amap_mcp import KEY, Wire
from tooling.providers.amap_mcp import ChatPlacesMCPClient as RealChatPlacesMCPClient
from tools.echomind_travel_controlled import controlled_app, initialize

ORIGIN = "http://127.0.0.1:3000"


class AmapMCPMySQLTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine, cleanup = temporary_mysql()
        self.addCleanup(cleanup)
        folder = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="amap-mcp-mysql-"))).resolve()
        anchor = folder / "usage.identity"
        anchor.write_text(uuid4().hex + "\n", encoding="ascii")
        anchor.chmod(0o600)
        initialize(self.engine, anchor)
        app, self.unplanned_upstream = self.enterContext(controlled_app(self.engine, anchor, origin=ORIGIN))
        settings = Settings(_env_file=None, chat_places_transport="amap_mcp", amap_mcp_key=KEY,
                            amap_web_key="", qwen_api_key="", qwen_base_url="")
        self.enterContext(patch.dict(app.dependency_overrides, {get_settings: lambda: settings}))
        self.wire = Wire()
        self.constructor = self.enterContext(patch("tooling.providers.amap_mcp.ChatPlacesMCPClient",
            side_effect=lambda key, **kwargs: RealChatPlacesMCPClient(key, transport=httpx.MockTransport(self.wire.handle), **kwargs)))
        self.a = self.enterContext(TestClient(app))
        self.b = self.enterContext(TestClient(app))
        identity = self.bootstrap(self.a)
        self.owner = identity["user"]["id"]
        self.headers = {"origin": ORIGIN, "x-csrf-token": identity["csrf_token"]}
        created = self.a.post("/chat/conversations", headers=self.headers, json={"operation_id": str(uuid4())})
        self.assertEqual(created.status_code, 200, created.text)
        self.conversation = created.json()
        self.path = f"/chat/conversations/{self.conversation['id']}/places"
        read = self.a.get(self.path)
        self.assertEqual(read.status_code, 200, read.text)
        self.view = read.json()
        selected = self.command("set_city", city_id="230100")
        self.assertEqual(selected.status_code, 200, selected.text)
        self.assertEqual(self.wire.methods, [])

    def bootstrap(self, browser: TestClient) -> dict:
        first = browser.get("/workspace/session")
        self.assertEqual(first.status_code, 200, first.text)
        created = browser.post("/workspace/session", headers={"origin": ORIGIN,
            "x-csrf-token": first.json()["csrf_token"]}, json={})
        self.assertEqual(created.status_code, 200, created.text)
        return created.json()

    def payload(self, action: str, *, view: dict | None = None, operation: str | None = None, **values) -> dict:
        return {"action": action, "operation_id": operation or str(uuid4()),
                "expected_intent_revision": self.conversation["revision"],
                "expected_revision": (view or self.view)["revision"], **values}

    def command(self, action: str, *, view: dict | None = None, operation: str | None = None, **values) -> httpx.Response:
        result = self.a.post(self.path, headers=self.headers,
            json=self.payload(action, view=view, operation=operation, **values))
        if result.status_code == 200:
            self.view = result.json()
        return result

    def attempts(self) -> list[tuple]:
        with self.engine.connect() as db:
            return db.execute(text("SELECT owner_id,service,state FROM usage_attempts ORDER BY created_at,id")).all()

    def test_real_sdk_preview_confirmation_replay_and_anonymous_owner_csrf_isolation(self) -> None:
        before, operation = self.view, str(uuid4())
        response = self.command("search", operation=operation, keyword="受控地点", role="required")
        self.assertEqual(response.status_code, 200, response.text)
        batch = self.view["batch"]
        self.assertEqual(batch["places"][0]["id"], "B001")
        self.assertEqual(batch["places"][0]["region"], {"citycode": "0451", "cityname": "哈尔滨市", "adcode": "230102"})
        self.assertEqual(self.view["required_places"], [])
        self.assertEqual(self.wire.methods, ["initialize", "notifications/initialized", "tools/list", "tools/call"])
        self.assertEqual(self.attempts(), [(self.owner, "amap_poi", "received")])
        self.assertEqual(self.constructor.call_count, 1)
        self.assertEqual(self.constructor.call_args.args, (KEY,))
        self.assertTrue(callable(self.constructor.call_args.kwargs["before_send"]))
        replay = self.command("search", view=before, operation=operation, keyword="受控地点", role="required")
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertEqual(replay.json()["batch"], batch)
        self.assertEqual(self.constructor.call_count, 1)
        self.assertEqual(len(self.wire.methods), 4)

        identity_b = self.bootstrap(self.b)
        headers_b = {"origin": ORIGIN, "x-csrf-token": identity_b["csrf_token"]}
        self.assertNotEqual(identity_b["user"]["id"], self.owner)
        self.assertEqual(self.b.get(self.path).status_code, 404)
        stolen = self.b.post(self.path, headers=headers_b,
            json=self.payload("confirm", batch_id=batch["id"], place_id="B001", role="required"))
        self.assertEqual(stolen.status_code, 404, stolen.text)
        invalid_csrf = self.a.post(self.path,
            json=self.payload("confirm", batch_id=batch["id"], place_id="B001", role="required"))
        self.assertEqual(invalid_csrf.status_code, 403, invalid_csrf.text)
        confirmed = self.command("confirm", batch_id=batch["id"], place_id="B001", role="required")
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertEqual(self.view["required_places"], batch["places"])
        # Re-opening the disk-backed store preserves only explicitly confirmed
        # data; not a browser or remote-provider authenticity certification.
        self.engine.dispose()
        restored = self.a.get(self.path)
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()["required_places"], batch["places"])
        self.assertEqual(len(self.wire.methods), 4)
        self.assertEqual(self.attempts(), [(self.owner, "amap_poi", "received")])
        self.assertEqual(self.unplanned_upstream.events, [])

    def test_sent_cancellation_cannot_publish_late_batch_or_authorize_operation_resend(self) -> None:
        before, operation = self.view, str(uuid4())
        self.wire.wait = asyncio.Event()
        body = self.payload("search", view=before, operation=operation, keyword="受控地点", role="required")
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self.a.post, self.path, headers=self.headers, json=body)
            deadline = time.monotonic() + 5
            while not self.wire.entered.is_set() and time.monotonic() < deadline:
                time.sleep(.005)
            self.assertTrue(self.wire.entered.is_set(), "controlled MCP send did not start")
            cancelled = self.a.post(self.path, headers=self.headers, json=self.payload("cancel",
                view={"revision": before["revision"] + 1}, operation=operation))
            self.assertEqual(cancelled.status_code, 200, cancelled.text)
            try:
                result = future.result(timeout=5)
            except FutureCancelledError:
                result = None
        if result is not None:
            self.assertIn(result.status_code, {409, 499}, result.text)
        current = self.a.get(self.path)
        self.assertEqual(current.status_code, 200, current.text)
        self.assertEqual(current.json()["last_operation"]["state"], "cancelled")
        self.assertIsNone(current.json()["batch"])
        self.assertEqual(current.json()["required_places"], [])
        self.assertEqual(self.wire.methods.count("tools/call"), 1)
        self.assertEqual(self.attempts(), [(self.owner, "amap_poi", "cancelled")])
        replay = self.a.post(self.path, headers=self.headers, json=body)
        self.assertIn(replay.status_code, {200, 409}, replay.text)
        self.assertEqual(self.wire.methods.count("tools/call"), 1)
        self.assertEqual(self.unplanned_upstream.events, [])

    def test_durable_quota_refusal_is_http_429_before_actual_tool_send_not_empty_candidates(self) -> None:
        usage_runtime.get_usage_store().settings.business_daily_limits["amap_poi"] = DailyLimit(account=0, **{"global": 10})
        response = self.command("search", keyword="受控地点", role="required")
        self.assertEqual(response.status_code, 429, response.text)
        self.assertEqual(response.json()["detail"]["code"], "business_service_disabled")
        self.assertEqual(response.headers["x-business-usage-service"], "amap_poi")
        self.assertEqual(self.wire.methods, ["initialize", "notifications/initialized", "tools/list"])
        self.assertEqual(self.attempts(), [])
        current = self.a.get(self.path).json()
        self.assertEqual(current["last_operation"]["state"], "failed")
        self.assertEqual(current["last_operation"]["error_code"], "business_service_disabled")
        self.assertIsNone(current["batch"])
        self.assertEqual(self.a.get("/saved-trips").status_code, 200)
        self.assertEqual(self.unplanned_upstream.events, [])

    def test_db_cancel_during_discovery_prevents_business_send_without_active_task_cancellation(self) -> None:
        before, operation = self.view, str(uuid4())
        discovered, release = threading.Event(), threading.Event()
        original_handle = self.wire.handle

        async def delayed_catalog(request: httpx.Request) -> httpx.Response:
            response = await original_handle(request)
            if json.loads(request.content)["method"] == "tools/list":
                discovered.set()
                while not release.is_set():
                    await asyncio.sleep(.005)
            return response

        self.wire.handle = delayed_catalog
        body = self.payload("search", view=before, operation=operation, keyword="受控地点", role="required")
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self.a.post, self.path, headers=self.headers, json=body)
            try:
                self.assertTrue(discovered.wait(5), "controlled tools/list was not reached")
                # Deliberately bypass only the HTTP cancel route's _ACTIVE
                # task.cancel. The persisted operation/version changes while
                # the old SDK task is allowed to continue to the send guard.
                cancelled = ChatPlacesStore(self.engine).cancel(self.owner, self.conversation["id"],
                    operation, before["revision"] + 1)
                self.assertEqual(cancelled["last_operation"]["state"], "cancelled")
            finally:
                release.set()
            response = future.result(timeout=5)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.wire.methods, ["initialize", "notifications/initialized", "tools/list"])
        self.assertEqual(self.attempts(), [])
        current = self.a.get(self.path)
        self.assertEqual(current.status_code, 200, current.text)
        self.assertEqual(current.json()["last_operation"]["state"], "cancelled")
        self.assertIsNone(current.json()["batch"])
        self.assertEqual(current.json()["required_places"], [])
        self.assertEqual(self.unplanned_upstream.events, [])


if __name__ == "__main__":
    unittest.main()
