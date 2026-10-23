"""Real production identity + durable quota + controlled HTTP transport.

No live provider, user database, or historical model allowance is used.
"""
import ast
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from core import auth
from core.business_usage import SERVICES, UsageSettings, UsageStore, UsageError
from core.config import Settings, get_settings
from memory.storage import engine_for
from tests.legacy_identity_app import app
from tests.test_saved_trips import payload

ORIGIN = "http://127.0.0.1:3000"


class ProductionUsageBoundary(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(self.directory) / "identity.sqlite3"
        self.usage_path = Path(self.directory) / "usage.sqlite3"
        self.enterContext(patch.dict(os.environ, {"TRIPPILOT_DATABASE_PATH": str(self.path)}))
        settings = auth.AuthSettings(_env_file=None, auth_cookie_secure=False)
        self.enterContext(patch("core.auth.get_auth_settings", return_value=settings))
        self.enterContext(patch("api.auth.get_auth_settings", return_value=settings))
        self.limits = {service: {"account": 20, "global": 100} for service in SERVICES}
        self.limits["amap_poi"] = {"account": 2, "global": 3}
        self.settings = self.quota_settings()
        self.enterContext(patch("core.business_usage.get_usage_settings", side_effect=lambda: self.settings))
        UsageStore(self.settings).initialize()
        self.first_id = auth.create_account("quota-a", "controlled-quota-a-password")
        self.second_id = auth.create_account("quota-b", "controlled-quota-b-password")
        self.a, self.b = self.enterContext(TestClient(app)), self.enterContext(TestClient(app))
        self.sent = 0
        self.failure = None
        self.enterContext(patch("httpx._client.AsyncHTTPTransport", side_effect=lambda **_: httpx.MockTransport(self.upstream)))
        self.enterContext(patch.dict(app.dependency_overrides, {
            get_settings: lambda: Settings(_env_file=None, amap_web_key="controlled-not-a-real-key"),
        }))
        self.addCleanup(self.dispose)

    def dispose(self):
        engine_for(str(self.path)).dispose()
        engine_for.cache_clear()

    def quota_settings(self):
        return UsageSettings(_env_file=None, business_usage_path=str(self.usage_path),
                             business_rate_account_limit=100, business_rate_global_limit=200,
                             business_daily_limits=self.limits)

    def upstream(self, request):
        self.sent += 1
        if self.failure is not None:
            raise self.failure("controlled network failure", request=request)
        return httpx.Response(200, json={"status": "1", "pois": []})

    def login(self, client, suffix):
        csrf = client.get("/auth/session").json()["csrf_token"]
        response = client.post("/auth/login", headers={"origin": ORIGIN, "x-csrf-token": csrf},
                               json={"username": "quota-" + suffix, "password": "controlled-quota-" + suffix + "-password"})
        self.assertEqual(response.status_code, 200)
        return {"origin": ORIGIN, "x-csrf-token": response.json()["csrf_token"]}

    def search(self, client):
        return client.get("/places/search?keyword=受控&destination_id=shanghai")

    def test_direct_account_and_global_caps_restart_and_readonly_history(self):
        self.assertEqual(self.search(self.a).status_code, 401)
        ah = self.login(self.a, "a")
        self.login(self.b, "b")
        for _ in range(2):
            self.assertEqual(self.search(self.a).status_code, 200)
        limited = self.search(self.a)
        self.assertEqual(limited.status_code, 429, limited.text)
        self.assertEqual(limited.json()["detail"]["scope"], "account")
        self.assertEqual(limited.headers["x-business-usage-code"], "business_quota_exhausted")
        self.assertGreater(int(limited.headers["retry-after"]), 0)
        self.assertEqual(self.search(self.b).status_code, 200)
        limited = self.search(self.b)
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.json()["detail"]["scope"], "global")
        self.assertEqual(self.sent, 3)
        # New store object/reopened disk retains counts. Only own counts shown.
        UsageStore(self.settings).verify()
        own = self.a.get("/usage")
        self.assertEqual(own.status_code, 200, own.text)
        self.assertEqual(own.json()["services"]["amap_poi"]["used"], 2)
        self.assertNotIn(self.second_id, own.text)
        self.assertNotIn("global", own.text)
        self.assertEqual(self.a.get("/usage?owner_id=" + self.second_id).status_code, 422)
        self.assertEqual(self.a.get("/saved-trips").status_code, 200)
        operation = payload()
        saved = self.a.post("/saved-trips", json=operation, headers=ah)
        self.assertEqual(saved.status_code, 200, saved.text)
        repeated = self.a.post("/saved-trips", json=operation, headers=ah)
        self.assertEqual(saved.json(), repeated.json())
        self.assertEqual(self.b.get("/saved-trips/" + saved.json()["id"]).status_code, 404)
        self.assertEqual(self.sent, 3)

    def test_connection_retry_is_a_new_attempt_and_cannot_cross_quota(self):
        self.login(self.a, "a")
        self.failure = httpx.ConnectError
        # Existing adapter's bounded retry consumes its own durable unit.
        response = self.search(self.a)
        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(self.sent, 2)
        response = self.search(self.a)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(self.sent, 2)
        with closing(sqlite3.connect(self.usage_path)) as db:
            self.assertEqual(db.execute("SELECT state, count(*) FROM usage_attempts GROUP BY state").fetchall(), [("failed", 2)])

    def test_counter_failure_stops_dispatch_without_disabling_history_or_answer_gate(self):
        headers = self.login(self.a, "a")
        with patch("core.business_usage.UsageStore.reserve", side_effect=UsageError()):
            failed = self.search(self.a)
            self.assertEqual(failed.status_code, 503)
            self.assertEqual(failed.json()["detail"]["code"], "business_usage_unavailable")
            self.assertEqual(self.sent, 0)
            self.assertEqual(self.a.get("/saved-trips").status_code, 200)
            disabled = self.a.post("/knowledge/answer", json={}, headers=headers)
            self.assertEqual(disabled.status_code, 503)
            self.assertEqual(disabled.json()["detail"]["code"], "knowledge_answer_disabled")

    def test_invalid_config_missing_database_and_lost_anchor_do_not_replenish(self):
        self.login(self.a, "a")
        self.assertEqual(self.search(self.a).status_code, 200)
        anchor = self.usage_path.with_name(self.usage_path.name + ".identity")
        anchor.rename(anchor.with_suffix(".held"))
        response = self.search(self.a)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.sent, 1)
        self.assertEqual(self.a.get("/saved-trips").status_code, 200)
        self.assertNotIn(str(self.path), response.text)
        self.assertNotIn(str(self.usage_path), response.text)


class ProductionTransportInventory(unittest.TestCase):
    def test_runtime_http_client_factories_all_install_metering(self):
        root = Path(__file__).resolve().parents[1]
        factories = []
        sources = [source for package in ("api", "agents", "core", "tooling", "memory", "monitor", "evaluation")
                   for source in (root / package).rglob("*.py")]
        self.assertTrue(sources)
        for source in sources:
            tree = ast.parse(source.read_text())
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name) and node.func.value.id == "httpx"
                        and node.func.attr == "AsyncClient"):
                    self.fail(f"unmetered runtime HTTP client: {source}")
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "MeteredAsyncClient":
                    factories.append(str(source.relative_to(root)))
                    self.assertFalse(any(keyword.arg == "transport" for keyword in node.keywords), source)
        self.assertEqual(sorted(factories), ["api/amap_proxy.py", "api/explanation.py", "api/places.py"])


if __name__ == "__main__":
    unittest.main()
