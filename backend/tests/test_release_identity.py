"""Full production ASGI stack: identity, ownership and early execution boundary."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from core import auth
from memory.identity_models import SessionRow
from memory.saved_trips import SavedTripRow, canonical
from core.schemas.saved_trip import SaveTrip
from memory.storage import engine_for
from main import app
from tests.test_saved_trips import payload

ORIGIN = "http://127.0.0.1:3000"


class ProductionIdentityBoundary(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "private.sqlite3"
        self.enterContext(patch.dict(os.environ, {"TRIPPILOT_DATABASE_PATH": str(self.path)}))
        settings = auth.AuthSettings(_env_file=None, auth_cookie_secure=False)
        self.enterContext(patch("core.auth.get_auth_settings", return_value=settings))
        self.enterContext(patch("api.auth.get_auth_settings", return_value=settings))
        self.first_id = auth.create_account("account-a", "controlled-account-a-password")
        self.second_id = auth.create_account("account-b", "controlled-account-b-password")
        self.a, self.b = TestClient(app), TestClient(app)
        self.addCleanup(self.finish)

    def finish(self):
        self.a.close(); self.b.close()
        engine_for(str(self.path)).dispose(); engine_for.cache_clear()
        self.directory.cleanup()

    def login(self, client, letter):
        csrf = client.get("/auth/session").json()["csrf_token"]
        result = client.post("/auth/login", headers={"origin": ORIGIN, "x-csrf-token": csrf},
                             json={"username": f"account-{letter}", "password": f"controlled-account-{letter}-password"})
        self.assertEqual(result.status_code, 200, result.text)
        return {"origin": ORIGIN, "x-csrf-token": result.json()["csrf_token"]}

    def test_every_business_route_denied_before_dependencies_or_media_body(self):
        protected = []
        with patch("httpx.AsyncClient.send", side_effect=AssertionError("no upstream")), \
                patch("api.photo.normalize_photo", side_effect=AssertionError("no photo decode")), \
                patch("api.video.executables", side_effect=AssertionError("no video process")):
            # Installed FastAPI includes routers lazily; OpenAPI enumerates the
            # real mounted paths rather than relying on route class internals.
            for route_path, methods in app.openapi()["paths"].items():
                for verb in methods:
                    method = verb.upper()
                    if (method, route_path) in auth.PUBLIC_ENDPOINTS or method == "OPTIONS":
                        continue
                    path = route_path.replace("{record_id}", "0" * 32)
                    result = self.a.request(method, path, content=b"not even valid input")
                    self.assertEqual(result.status_code, 401, (method, path, result.text))
                    self.assertEqual(result.json()["detail"]["code"], "authentication_required")
                    protected.append((method, path))
        self.assertGreaterEqual(len(protected), 18)
        self.assertEqual(self.a.get("/health").status_code, 200)

    def test_two_accounts_scope_list_detail_idempotency_and_forged_owner(self):
        ah, bh = self.login(self.a, "a"), self.login(self.b, "b")
        operation = payload()
        with patch("httpx.AsyncClient.send", side_effect=AssertionError("archives never call upstream")):
            first = self.a.post("/saved-trips", json=operation, headers=ah)
            self.assertEqual(first.status_code, 200, first.text)
            aid = first.json()["id"]
            self.assertEqual(self.a.post("/saved-trips", json=operation, headers=ah).json()["id"], aid)
            self.assertEqual(self.b.get("/saved-trips/" + aid).status_code, 404)
            self.assertEqual(self.b.get("/saved-trips").json()["items"], [])
            second = self.b.post("/saved-trips", json=operation, headers=bh)
            self.assertEqual(second.status_code, 200, second.text)
            bid = second.json()["id"]
            self.assertNotEqual(aid, bid)
            self.assertEqual(self.a.get("/saved-trips/" + bid).status_code, 404)
            result = self.b.post("/saved-trips", json={**operation, "owner_id": self.first_id}, headers=bh)
            self.assertEqual(result.status_code, 422)
            result = self.a.get("/saved-trips?owner_id=" + self.second_id)
            self.assertEqual([row["id"] for row in result.json()["items"]], [aid])
            self.assertEqual(self.b.post("/saved-trips", json={**operation, "name": "changed"}, headers=bh).status_code, 409)
            # Restart/reopen preserves original timestamps and isolated contents.
            engine_for(str(self.path)).dispose(); engine_for.cache_clear()
            self.assertEqual(self.a.get("/saved-trips/" + aid).json(), first.json())

    def test_unowned_legacy_row_is_not_assigned_to_any_account(self):
        operation = SaveTrip.model_validate(payload())
        sr = operation.snapshot.schedule
        legacy_id = uuid4().hex
        with Session(engine_for(str(self.path))) as db:
            db.add(SavedTripRow(id=legacy_id, owner_id=None, operation_id=operation.operation_id,
                               name=operation.name, saved_at="2026-10-10T00:00:00+00:00",
                               schema_version=operation.snapshot.schema_version,
                               start_date=str(sr.request.start_date), end_date=str(sr.request.end_date),
                               transport_mode=sr.request.transport_mode, status=sr.status,
                               snapshot_json=canonical(operation.snapshot)))
            db.commit()
        for client, letter in ((self.a, "a"), (self.b, "b")):
            self.login(client, letter)
            self.assertEqual(client.get("/saved-trips").json()["items"], [])
            self.assertEqual(client.get("/saved-trips/" + legacy_id).status_code, 404)

    def test_expired_and_revoked_sessions_cannot_process_media_or_save(self):
        headers = self.login(self.a, "a")
        with Session(engine_for(str(self.path))) as db:
            row = db.exec(select(SessionRow)).one()
            row.expires_at = 1; db.add(row); db.commit()
        with patch("api.video.executables", side_effect=AssertionError("no decoder")):
            self.assertEqual(self.a.post("/media/video-frames?request_id=x", content=b"x", headers=headers).status_code, 401)
            self.assertEqual(self.a.post("/saved-trips", json=payload(), headers=headers).status_code, 401)
        self.login(self.a, "a")
        auth.revoke_account("account-a")
        self.assertEqual(self.a.get("/saved-trips").status_code, 401)

    def test_csrf_failure_does_not_enter_archive_or_upstream_and_disabled_answer_stays_disabled(self):
        headers = self.login(self.a, "a")
        with patch("httpx.AsyncClient.send", side_effect=AssertionError("no upstream")):
            self.assertEqual(self.a.post("/saved-trips", json=payload()).status_code, 403)
            self.assertEqual(self.a.post("/trips/schedule-preview", json={}, headers={**headers, "origin": "https://evil.invalid"}).status_code, 403)
            result = self.a.post("/knowledge/answer", json={}, headers=headers)
            self.assertEqual(result.status_code, 503)
            self.assertEqual(result.json()["detail"]["code"], "knowledge_answer_disabled")


if __name__ == "__main__":
    unittest.main()
