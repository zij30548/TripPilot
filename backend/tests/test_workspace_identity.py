"""Production anonymous-workspace boundary, real isolated MySQL, zero upstreams."""
from concurrent.futures import ThreadPoolExecutor
import asyncio
import hashlib
import os
from pathlib import Path
import secrets
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

from core import auth, workspace_identity as workspace
from core.business_usage import SERVICES, UsageSettings
from core.workspace_contract import WORKSPACE_PASSWORD_MARKER
from memory.identity_models import AccountRow, LoginBucketRow, SessionRow
from memory.mysql_storage import initialize_private_schema
from memory.mysql_usage import initialize_usage_schema
from memory.storage import StorageError
from main import app
from tests.mysql_support import temporary_mysql
from tests.test_saved_trips import payload
from tools.chat_storage import provision_chat_extension
from tools.mysql_migration import Refused, _source_rows_valid

ORIGIN = "http://127.0.0.1:3000"


class WorkspaceNoFallbackTests(unittest.TestCase):
    def test_missing_mysql_never_reads_or_creates_legacy_sqlite(self):
        with patch("core.workspace_identity.mysql_configured", return_value=False), \
                patch("core.auth.identity_engine", side_effect=AssertionError("legacy store must stay untouched")):
            with self.assertRaisesRegex(StorageError, "workspace_requires_mysql"):
                workspace.bootstrap_workspace(secrets.token_urlsafe(32))

    def test_removed_auth_rejected_before_body_or_handler(self):
        async def check():
            received, entered, sent = [], [], []
            async def downstream(scope, receive, send): entered.append(True)
            async def receive():
                received.append(True)
                raise AssertionError("removed endpoint body must not be read")
            async def send(value): sent.append(value)
            for method in ("GET", "POST", "OPTIONS"):
                scope = {"type": "http", "method": method, "path": "/auth/login", "headers": []}
                await workspace.WorkspaceMiddleware(downstream)(scope, receive, send)
            self.assertEqual(received, [])
            self.assertEqual(entered, [])
            self.assertEqual([item["status"] for item in sent if item["type"] == "http.response.start"], [404] * 3)
        asyncio.run(check())

    def test_backup_contract_accepts_only_explicit_internal_marker(self):
        row = {"id": "a" * 32, "username": "workspace_" + "a" * 32,
               "password_hash": WORKSPACE_PASSWORD_MARKER, "disabled": 0, "created_at": 100}
        _source_rows_valid("accounts", row)
        for changes in ({"username": "original-user"}, {"password_hash": "invalid"},
                        {"username": "workspace_" + "b" * 32}, {"password_hash": WORKSPACE_PASSWORD_MARKER + "x"}):
            with self.subTest(changes=changes), self.assertRaises(Refused):
                _source_rows_valid("accounts", row | changes)
        # Existing real Argon2 accounts retain their original validation path.
        _source_rows_valid("accounts", row | {"username": "original-user", "password_hash": auth.PASSWORD_HASHER.hash("controlled-old-account")})


class WorkspaceMySQLTests(unittest.TestCase):
    def setUp(self):
        self.engine, cleanup = temporary_mysql()
        self.addCleanup(cleanup)
        initialize_private_schema(self.engine)
        provision_chat_extension(self.engine)
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="trippilot-workspace-")))
        anchor = self.folder / "usage.identity"
        identity = uuid4().hex
        anchor.write_text(identity + "\n", encoding="ascii")
        anchor.chmod(0o600)
        initialize_usage_schema(self.engine, identity)
        self.enterContext(patch.dict(os.environ, {
            "TRIPPILOT_MYSQL_URL": self.engine.url.render_as_string(hide_password=False),
            "BUSINESS_USAGE_IDENTITY_PATH": str(anchor),
        }))
        self.settings = auth.AuthSettings(_env_file=None, auth_cookie_secure=False, auth_allowed_origins=(ORIGIN,))
        for module in ("core.auth", "core.workspace_identity", "api.workspace"):
            self.enterContext(patch(module + ".get_auth_settings", return_value=self.settings))
        self.enterContext(patch("core.workspace_identity.current_mysql_engine", return_value=self.engine))
        # The opt-in test administrator has no application password. Keep the
        # real isolated InnoDB connection while bypassing only URL construction;
        # the production schema validator and all handlers still run unchanged.
        self.enterContext(patch("memory.mysql_storage.engine_from_url", return_value=self.engine))
        from memory.mysql_storage import _validated_engine
        self.addCleanup(_validated_engine.cache_clear)
        usage_settings = UsageSettings(_env_file=None, business_usage_identity_path=str(anchor),
            business_daily_limits={name: {"account": 1, "global": 2} for name in SERVICES})
        self.enterContext(patch("core.business_usage.get_usage_settings", return_value=usage_settings))
        self.a = self.enterContext(TestClient(app))
        self.b = self.enterContext(TestClient(app))
        self.no_external = patch("httpx.AsyncClient.send", side_effect=AssertionError("no external business requests"))
        self.no_external.start()
        self.addCleanup(self.no_external.stop)

    def preflight(self, client=None):
        target = client or self.a
        response = target.get("/workspace/session")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def prepare(self, client=None):
        target = client or self.a
        view = self.preflight(target)
        if view["authenticated"]:
            return view
        response = target.post("/workspace/session", json={}, headers={"origin": ORIGIN, "x-csrf-token": view["csrf_token"]})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    @staticmethod
    def headers(view):
        return {"origin": ORIGIN, "x-csrf-token": view["csrf_token"]}

    def rows(self, model):
        with Session(self.engine) as db:
            return list(db.exec(select(model)).all())

    def test_read_bootstrap_is_not_account_creation_and_seed_is_private(self):
        first = self.preflight()
        second = self.preflight()
        self.assertFalse(first["authenticated"])
        self.assertEqual(first, second)
        self.assertEqual(self.rows(AccountRow), [])
        seed = self.a.cookies.get(workspace.cookie_name("seed"))
        self.assertIsNotNone(seed)
        self.assertNotEqual(seed, first["csrf_token"])
        self.assertNotIn(seed, str(first))
        response = self.a.get("/workspace/session")
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertIn("SameSite=lax", response.headers["set-cookie"])
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("Cookie", response.headers["vary"])

    def test_workspace_bootstrap_no_credentials_same_owner_and_chat_work(self):
        view = self.prepare()
        self.assertEqual(set(view), {"authenticated", "user", "csrf_token", "expires_at", "session_id"})
        self.assertEqual(view["user"]["username"], "当前工作区")
        self.assertTrue(view["authenticated"])
        self.assertEqual(self.preflight(), view)
        self.assertEqual(len(self.rows(AccountRow)), 1)
        self.assertEqual(len(self.rows(SessionRow)), 1)
        account = self.rows(AccountRow)[0]
        self.assertFalse(account.disabled)
        self.assertEqual(account.password_hash, WORKSPACE_PASSWORD_MARKER)
        self.assertNotEqual(self.rows(SessionRow)[0].token_hash, self.a.cookies.get(workspace.cookie_name("session")))
        response = self.a.post("/chat/conversations", json={"operation_id": str(uuid4())}, headers=self.headers(view))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.a.get("/chat/conversations").json()["items"][0]["id"], response.json()["id"])

    def test_two_workspaces_archive_chat_and_idempotency_are_isolated(self):
        a, b = self.prepare(), self.prepare(self.b)
        self.assertNotEqual(a["user"]["id"], b["user"]["id"])
        body = payload()
        first = self.a.post("/saved-trips", json=body, headers=self.headers(a))
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(self.a.post("/saved-trips", json=body, headers=self.headers(a)).json(), first.json())
        self.assertEqual(self.b.get("/saved-trips").json()["items"], [])
        self.assertEqual(self.b.get("/saved-trips/" + first.json()["id"]).status_code, 404)
        second = self.b.post("/saved-trips", json=body, headers=self.headers(b))
        self.assertEqual(second.status_code, 200, second.text)
        self.assertNotEqual(second.json()["id"], first.json()["id"])
        self.assertEqual(self.b.post("/saved-trips", json=body | {"owner_id": a["user"]["id"]}, headers=self.headers(b)).status_code, 422)
        chat = self.a.post("/chat/conversations", json={"operation_id": str(uuid4())}, headers=self.headers(a)).json()
        self.assertEqual(self.b.get("/chat/conversations/" + chat["id"]).status_code, 404)

    def test_origin_csrf_payload_and_unknown_owner_refused(self):
        view = self.preflight()
        headers = self.headers(view)
        for wrong in ({}, {"origin": ORIGIN}, {"origin": "https://evil.invalid", "x-csrf-token": view["csrf_token"]},
                      headers | {"sec-fetch-site": "cross-site"}):
            self.assertEqual(self.a.post("/workspace/session", json={}, headers=wrong).status_code, 403)
        for raw in (b'{"owner_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}', b'{"x":1,"x":2}', b'[]', b'null', b'{"x":NaN}'):
            self.assertEqual(self.a.post("/workspace/session", content=raw, headers=headers | {"content-type": "application/json"}).status_code, 422)
        self.assertEqual(self.a.post("/workspace/session", content=b"x" * 129, headers=headers | {"content-type": "application/json"}).status_code, 413)
        self.assertEqual(self.a.get("/workspace/session?owner_id=x").status_code, 422)
        before = dict(self.a.cookies)
        for extra in ({"sec-fetch-site": "cross-site"}, {"origin": "https://evil.invalid"}):
            rejected = self.a.get("/workspace/session", headers=extra)
            self.assertEqual(rejected.status_code, 403)
            self.assertNotIn("set-cookie", rejected.headers)
        self.assertEqual(dict(self.a.cookies), before)
        self.assertEqual(self.rows(AccountRow), [])

    def test_production_cookie_is_secure_host_only_and_private(self):
        self.settings.auth_cookie_secure = True
        with TestClient(app, base_url="https://testserver") as client:
            view = self.preflight(client)
            response = client.post("/workspace/session", json={}, headers=self.headers(view))
            self.assertEqual(response.status_code, 200, response.text)
            for value in response.headers.get_list("set-cookie"):
                self.assertTrue(value.startswith("__Host-trippilot_workspace_"))
                for expected in ("Secure", "HttpOnly", "SameSite=lax", "Path=/"):
                    self.assertIn(expected, value)
                self.assertNotIn("Domain=", value)

    def test_actual_controlled_dispatch_uses_workspace_owner_and_shared_hard_quota(self):
        from api.places import get_http_client
        from core.config import Settings, get_settings
        from core.usage_runtime import MeteredAsyncClient
        self.no_external.stop()
        calls = []
        async def upstream(request):
            calls.append(request)
            return httpx.Response(200, json={"status": "1", "pois": []})
        async def dependency():
            async with MeteredAsyncClient(transport=httpx.MockTransport(upstream)) as client:
                yield client
        a, b = self.prepare(), self.prepare(self.b)
        with patch.dict(app.dependency_overrides, {get_http_client: dependency,
                get_settings: lambda: Settings(_env_file=None, amap_web_key="controlled-not-a-key")}):
            path = "/places/search?keyword=受控&destination_id=shanghai"
            self.assertEqual(self.a.get(path).status_code, 200)
            self.assertEqual(self.a.get(path).status_code, 429)
            self.assertEqual(self.b.get(path).status_code, 200)
            self.assertEqual(self.b.get(path).status_code, 429)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.a.get("/usage").json()["services"]["amap_poi"]["used"], 1)
        with self.engine.connect() as db:
            from sqlalchemy import text
            rows = db.execute(text("SELECT owner_id,state FROM usage_attempts ORDER BY owner_id")).all()
        self.assertEqual(rows, sorted([(a["user"]["id"], "received"), (b["user"]["id"], "received")]))
        self.assertEqual(self.a.get("/saved-trips").status_code, 200)

    def test_unsafe_business_requests_keep_csrf_and_missing_workspace_stops_processing(self):
        with patch("api.photo.normalize_photo", side_effect=AssertionError("no decoder")), \
                patch("api.video.executables", side_effect=AssertionError("no process")):
            for path in ("/media/landmark", "/media/video-frames", "/trips/schedule-preview"):
                self.assertEqual(self.a.post(path, content=b"not-a-request").status_code, 401)
        view = self.prepare()
        self.assertEqual(self.a.post("/chat/conversations", json={"operation_id": str(uuid4())}).status_code, 403)
        self.assertEqual(self.a.post("/chat/conversations", json={"operation_id": str(uuid4())},
            headers=self.headers(view) | {"x-csrf-token": "a" * 43}).status_code, 403)

    def test_old_auth_routes_and_old_account_cookie_do_not_enter_current_product(self):
        old = AccountRow(id="a" * 32, username="retained-account", password_hash=auth.PASSWORD_HASHER.hash("controlled-retained-password"), created_at=100)
        token = secrets.token_urlsafe(32)
        with Session(self.engine, expire_on_commit=False) as db:
            db.add(old); db.flush()
            db.add(SessionRow(id="b" * 32, user_id=old.id, token_hash=hashlib.sha256(token.encode()).hexdigest(),
                csrf_token="c" * 43, created_at=100, expires_at=auth.now_seconds() + 600))
            db.commit()
        self.a.cookies.set("trippilot_session", token)
        self.assertEqual(self.a.get("/saved-trips").status_code, 401)
        for path in ("/auth/session", "/auth/login", "/auth/register", "/auth/logout"):
            self.assertEqual(self.a.post(path, content=b"ignored").status_code, 404)
            self.assertEqual(self.a.get(path).status_code, 404)
        self.assertFalse(any(path.startswith("/auth/") for path in app.openapi()["paths"]))
        current = self.prepare()
        self.assertNotEqual(current["user"]["id"], old.id)
        self.a.cookies.set(workspace.cookie_name("session"), token)
        self.assertEqual(self.a.get("/saved-trips").status_code, 401)
        with Session(self.engine) as db:
            self.assertEqual(db.get(AccountRow, old.id).username, "retained-account")

    def test_concurrent_and_response_loss_bootstrap_reuses_seed_owner_and_session(self):
        seed = secrets.token_urlsafe(32)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: workspace.bootstrap_workspace(seed), range(4)))
        self.assertEqual(len({result[0]["user"]["id"] for result in results}), 1)
        self.assertEqual(len({result[0]["session_id"] for result in results}), 1)
        self.assertEqual(len({result[0]["csrf_token"] for result in results}), 1)
        self.assertEqual(len({result[1] for result in results}), 1)
        self.assertEqual((len(self.rows(AccountRow)), len(self.rows(SessionRow))), (1, 1))
        before = results[0]
        self.engine.dispose()
        after = workspace.bootstrap_workspace(seed)
        self.assertEqual(after[0]["user"], before[0]["user"])
        self.assertEqual(after[0]["session_id"], before[0]["session_id"])
        self.assertEqual(after[1], before[1])

    def test_expired_session_renews_same_owner_and_quota_never_refunds(self):
        view = self.prepare()
        owner = view["user"]["id"]
        from core.business_usage import get_usage_store
        store = get_usage_store()
        identifier = store.reserve(owner, "qwen_text")
        store.mark(identifier, "dispatching")
        with Session(self.engine) as db:
            row = db.get(SessionRow, view["session_id"])
            row.expires_at = auth.now_seconds() - 1
            db.add(row); db.commit()
        renewed = self.prepare()
        self.assertEqual(renewed["user"], view["user"])
        self.assertEqual(renewed["session_id"], view["session_id"])
        self.assertEqual(self.a.get("/usage").json()["services"]["qwen_text"]["used"], 1)
        with self.assertRaisesRegex(Exception, "business_quota_exhausted"):
            store.reserve(owner, "qwen_text")

    def test_global_bootstrap_rate_limit_persists_and_existing_workspace_reads_work(self):
        with patch.object(workspace, "BOOTSTRAP_GLOBAL_LIMIT", 2):
            self.prepare(); self.prepare(self.b)
            self.engine.dispose()
            with TestClient(app) as third:
                view = self.preflight(third)
                response = third.post("/workspace/session", json={}, headers=self.headers(view))
                self.assertEqual(response.status_code, 429)
                self.assertEqual(response.json()["detail"]["code"], "workspace_rate_limited")
                self.assertGreater(int(response.headers["retry-after"]), 0)
            self.assertEqual(len(self.rows(AccountRow)), 2)
            self.assertTrue(self.preflight()["authenticated"])
            self.assertEqual(self.a.get("/saved-trips").status_code, 200)

    def test_commit_failure_rolls_back_and_uncertain_success_recovers_same_owner(self):
        seed = secrets.token_urlsafe(32)
        with patch.object(Session, "commit", side_effect=OperationalError("private", {}, Exception("sensitive"))):
            with self.assertRaises(OperationalError):
                workspace.bootstrap_workspace(seed)
        self.assertEqual(self.rows(AccountRow), [])
        original = Session.commit
        def lost_ack(db):
            original(db)
            raise OperationalError("private", {}, Exception("sensitive"))
        with patch.object(Session, "commit", lost_ack), self.assertRaises(OperationalError):
            workspace.bootstrap_workspace(seed)
        self.assertEqual(len(self.rows(AccountRow)), 1)
        result = workspace.bootstrap_workspace(seed)
        self.assertEqual(result[0]["user"]["id"], self.rows(AccountRow)[0].id)
        self.assertEqual(len(self.rows(AccountRow)), 1)

    def test_disabled_or_non_workspace_owner_cannot_be_resumed(self):
        view = self.prepare()
        with Session(self.engine) as db:
            row = db.get(AccountRow, view["user"]["id"])
            row.disabled = True
            db.add(row); db.commit()
        self.assertFalse(self.preflight()["authenticated"])
        csrf = self.preflight()["csrf_token"]
        result = self.a.post("/workspace/session", json={}, headers={"origin": ORIGIN, "x-csrf-token": csrf})
        self.assertEqual(result.status_code, 403)
        self.assertEqual(len(self.rows(AccountRow)), 1)

    def test_storage_failure_does_not_leak_details_or_create_fallback(self):
        self.preflight()
        with patch("core.workspace_identity.current_mysql_engine", side_effect=StorageError("private absolute path")):
            result = self.a.post("/workspace/session", json={}, headers=self.headers(self.preflight()))
        self.assertEqual(result.status_code, 503)
        self.assertNotIn("private absolute path", result.text)
        self.assertEqual(self.rows(AccountRow), [])
