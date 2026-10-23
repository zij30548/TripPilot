"""Production identity and durable usage exercised by the Chrome harness."""
from contextlib import closing, redirect_stdout
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from core.business_usage import UsageStore
from memory.storage import engine_for
from tools.usage_acceptance import controlled_app, environment, prepare

ORIGIN = "http://127.0.0.1:3000"


class UsageAcceptanceHarness(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        folder = Path(self.directory)
        self.database, self.usage, self.credentials = folder / "identity.sqlite3", folder / "usage.sqlite3", folder / "private-credentials.json"
        output = io.StringIO()
        with redirect_stdout(output):
            prepare(self.database, self.usage, self.credentials)
        self.values = json.loads(self.credentials.read_text())
        self.stdout = output.getvalue()
        self.enterContext(patch.dict(os.environ, environment(self.database, self.usage)))
        from tests.legacy_identity_app import app
        self.app = app
        self.previous = dict(app.dependency_overrides)
        self.receipt = folder / "receipt.jsonl"
        controlled_app(self.receipt)
        self.addCleanup(self.dispose)
        # Any accidental production TCP transport construction is a test failure.
        self.enterContext(patch("httpx.AsyncHTTPTransport", side_effect=AssertionError("live_transport_forbidden")))
        self.a, self.b = self.enterContext(TestClient(app)), self.enterContext(TestClient(app))

    def dispose(self):
        self.app.dependency_overrides.clear()
        self.app.dependency_overrides.update(self.previous)
        engine_for(str(self.database)).dispose()
        engine_for.cache_clear()

    def login(self, client, name):
        csrf = client.get("/auth/session").json()["csrf_token"]
        response = client.post("/auth/login", headers={"origin": ORIGIN, "x-csrf-token": csrf},
                               json={"username": name, "password": self.values[name]})
        self.assertEqual(response.status_code, 200, response.text)
        return {"origin": ORIGIN, "x-csrf-token": response.json()["csrf_token"]}

    def search(self, client, keyword="受控住宿"):
        return client.get("/places/search", params={"keyword": keyword, "destination_id": "shanghai"})

    def events(self):
        return [json.loads(line) for line in self.receipt.read_text().splitlines()]

    def test_prepare_no_secrets_or_overwrites_and_original_paths_refused(self):
        self.assertEqual(stat.S_IMODE(self.credentials.stat().st_mode), 0o600)
        for value in self.values.values():
            self.assertNotIn(value, self.stdout)
        before = self.database.read_bytes(), self.usage.read_bytes(), self.credentials.read_bytes()
        with self.assertRaises(ValueError):
            prepare(self.database, self.usage, self.credentials)
        self.assertEqual(before, (self.database.read_bytes(), self.usage.read_bytes(), self.credentials.read_bytes()))
        protected = Path(__file__).resolve().parents[1] / "data" / "never-create.sqlite3"
        with self.assertRaises(ValueError):
            environment(protected, self.usage)
        self.assertFalse(protected.exists())

    def test_real_ledger_and_mock_provider_account_and_global_limits(self):
        self.assertEqual(self.search(self.a).status_code, 401)
        self.assertEqual(self.events(), [])
        self.login(self.a, "invited-a")
        self.login(self.b, "invited-b")
        for _ in range(3):
            response = self.search(self.a)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn("非真实高德资料", response.json()[0]["address"])
        denied = self.search(self.a)
        self.assertEqual((denied.status_code, denied.json()["detail"]["scope"]), (429, "account"))
        for _ in range(2):
            self.assertEqual(self.search(self.b).status_code, 200)
        denied = self.search(self.b)
        self.assertEqual((denied.status_code, denied.json()["detail"]["scope"]), (429, "global"))
        self.assertEqual(len(self.events()), 5)
        self.assertEqual(self.a.get("/usage").json()["services"]["amap_poi"]["used"], 3)
        self.assertEqual(self.b.get("/usage").json()["services"]["amap_poi"]["used"], 2)
        self.assertEqual(self.a.get("/saved-trips").status_code, 200)
        with closing(sqlite3.connect(self.usage)) as db:
            self.assertEqual(db.execute("SELECT state, count(*) FROM usage_attempts GROUP BY state").fetchall(), [("received", 5)])

    def test_directional_walking_is_metered_and_restart_keeps_events(self):
        headers = self.login(self.a, "invited-a")
        home = {"place_id": "controlled-home", "longitude": 121.4, "latitude": 31.2}
        visit = {"place_id": "controlled-visit", "longitude": 121.41, "latitude": 31.2}
        for origin, destination in ((home, visit), (visit, home)):
            response = self.a.post("/routes/walking", headers=headers, json={"origin": origin, "destination": destination})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["route"]["duration_seconds"], 600)
            self.assertEqual(response.json()["route"]["segments"][0][0], [origin["longitude"], origin["latitude"]])
        self.assertEqual(self.a.get("/usage").json()["services"]["amap_route"]["used"], 2)
        old = self.receipt.read_bytes()
        controlled_app(self.receipt)
        self.assertTrue(self.receipt.read_bytes().startswith(old))
        self.assertEqual(self.search(self.a).status_code, 200)
        self.assertEqual([row["sequence"] for row in self.events()], [1, 2, 3])
        UsageStore().verify()
        self.assertEqual(set(self.events()[0]), {"sequence", "service", "path", "controlled"})
        receipt = self.receipt.read_text()
        self.assertNotIn("keywords", receipt)
        for password in self.values.values():
            self.assertNotIn(password, receipt)

    def test_missing_usage_storage_rejects_dispatch_but_history_still_reads(self):
        self.login(self.a, "invited-a")
        anchor = Path(str(self.usage) + ".identity")
        anchor.rename(anchor.with_name("held.identity"))
        response = self.search(self.a)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["code"], "business_usage_unavailable")
        self.assertEqual(self.events(), [])
        self.assertEqual(self.a.get("/saved-trips").status_code, 200)


if __name__ == "__main__":
    unittest.main()
