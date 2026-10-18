"""Real SQLite sessions and password verification; no external business APIs."""
import asyncio
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import auth
from app.api import auth as auth_api
from app.identity_models import AccountRow, LoginBucketRow, SessionRow
from app.storage import engine_for
from app.storage import StorageError
from tools import account_admin

ORIGIN = "http://127.0.0.1:3000"
PASSWORD = "controlled-test-passphrase"


def test_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(auth.AuthenticationMiddleware)
    app.include_router(auth_api.router)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.api_route("/protected", methods=["GET", "POST"])
    async def protected(request: Request):
        return {"user_id": auth.current_user(request).id, "size": len(await request.body())}

    return app


class IdentitySecurity(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "identity.sqlite3"
        self.settings = auth.AuthSettings(_env_file=None, auth_cookie_secure=False, auth_allowed_origins=(ORIGIN,))
        self.patches = [patch("app.auth.database_path", return_value=self.path),
                        patch("app.auth.get_auth_settings", return_value=self.settings),
                        patch("app.api.auth.get_auth_settings", return_value=self.settings)]
        for item in self.patches: item.start()
        self.account = auth.create_account("invite-a", PASSWORD)
        self.app = test_app()
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        engine_for(str(self.path)).dispose()
        engine_for.cache_clear()
        for item in reversed(self.patches): item.stop()
        self.directory.cleanup()

    def bootstrap(self, client=None):
        target = client or self.client
        response = target.get("/auth/session")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        return {"origin": ORIGIN, "x-csrf-token": response.json()["csrf_token"]}

    def login(self, client=None, username="invite-a", password=PASSWORD):
        target = client or self.client
        return target.post("/auth/login", json={"username": username, "password": password}, headers=self.bootstrap(target))

    def test_cookie_contract_opaque_digest_and_real_password_hash(self):
        response = self.login()
        self.assertEqual(response.status_code, 200)
        view = response.json()
        self.assertEqual(set(view), {"authenticated", "user", "csrf_token", "expires_at", "session_id"})
        self.assertEqual(view["user"]["id"], self.account)
        self.assertTrue(view["authenticated"])
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertIn("SameSite=lax", response.headers["set-cookie"])
        with Session(engine_for(str(self.path))) as db:
            row = db.exec(select(SessionRow)).one()
            account = db.get(AccountRow, self.account)
            self.assertNotEqual(row.token_hash, self.client.cookies.get("trippilot_session"))
            self.assertNotIn(PASSWORD, account.password_hash)
            self.assertTrue(account.password_hash.startswith("$argon2id$"))
        self.assertEqual(self.client.get("/protected").json()["user_id"], self.account)

    def test_production_cookie_is_host_only_secure_and_httponly(self):
        self.settings.auth_cookie_secure = True
        self.client = TestClient(self.app, base_url="https://testserver")
        response = self.login()
        self.assertEqual(response.status_code, 200)
        cookie = response.headers["set-cookie"]
        self.assertIn("__Host-trippilot_session=", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Path=/", cookie)
        self.assertNotIn("Domain=", cookie)

    def test_login_origin_and_csrf_required_before_parsing(self):
        headers = self.bootstrap()
        for wrong in ({}, {"origin": "http://evil.invalid", "x-csrf-token": headers["x-csrf-token"]},
                      {"origin": ORIGIN}, {**headers, "sec-fetch-site": "cross-site"}):
            self.assertEqual(self.client.post("/auth/login", content=b"not-json", headers=wrong).status_code, 403)
        self.assertEqual(self.client.post("/auth/login", content=b"{" + b"x" * 4096, headers={**headers, "content-type": "application/json"}).status_code, 413)
        body = '{"username":"invite-a","username":"invite-b","password":"x"}'
        response = self.client.post("/auth/login", content=body, headers={**headers, "content-type": "application/json"})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("password", response.text)

    def test_anonymous_tabs_share_stable_bootstrap_and_logout_rotates(self):
        # The same cookie jar models browser tabs, not two independent sessions.
        first_tab = self.client.get("/auth/session").json()
        second_tab = self.client.get("/auth/session").json()
        self.assertEqual(first_tab["csrf_token"], second_tab["csrf_token"])
        logged_in = self.client.post("/auth/login", json={"username": "invite-a", "password": PASSWORD},
                                     headers={"origin": ORIGIN, "x-csrf-token": first_tab["csrf_token"]})
        self.assertEqual(logged_in.status_code, 200)
        result = logged_in.json()
        logged_out = self.client.post("/auth/logout", headers={"origin": ORIGIN, "x-csrf-token": result["csrf_token"]})
        self.assertEqual(logged_out.status_code, 200)
        replacement = logged_out.json()["csrf_token"]
        self.assertNotEqual(replacement, first_tab["csrf_token"])
        self.assertEqual(self.client.get("/auth/session").json()["csrf_token"], replacement)
        self.assertEqual(self.client.get("/auth/session").json()["csrf_token"], replacement)
        self.assertEqual(self.client.post("/auth/login", json={"username": "invite-a", "password": PASSWORD},
                                          headers={"origin": ORIGIN, "x-csrf-token": first_tab["csrf_token"]}).status_code, 403)
        self.assertEqual(self.client.post("/auth/login", json={"username": "invite-a", "password": PASSWORD},
                                          headers={"origin": ORIGIN, "x-csrf-token": replacement}).status_code, 200)

    def test_protected_unsafe_origin_and_per_session_csrf(self):
        result = self.login().json()
        self.assertEqual(self.client.post("/protected").status_code, 403)
        self.assertEqual(self.client.post("/protected", headers={"origin": ORIGIN, "x-csrf-token": "a" * 43}).status_code, 403)
        headers = {"origin": ORIGIN, "x-csrf-token": result["csrf_token"]}
        self.assertEqual(self.client.post("/protected", content=b"abc", headers=headers).json()["size"], 3)
        self.assertEqual(self.client.get("/protected").status_code, 200)

    def test_logout_replay_rejected_and_other_browser_remains(self):
        first = self.login().json()
        old_token = self.client.cookies.get("trippilot_session")
        with TestClient(self.app) as other:
            self.assertEqual(self.login(other).status_code, 200)
            result = self.client.post("/auth/logout", headers={"origin": ORIGIN, "x-csrf-token": first["csrf_token"]})
            self.assertEqual(result.status_code, 200)
            self.assertFalse(result.json()["authenticated"])
            self.client.cookies.set("trippilot_session", old_token)
            self.assertEqual(self.client.get("/protected").status_code, 401)
            self.assertEqual(other.get("/protected").status_code, 200)

    def test_expiry_revoke_disabled_and_account_switch_rotation(self):
        self.login()
        with patch("app.auth.now_seconds", return_value=auth.now_seconds() + 30_000):
            self.assertEqual(self.client.get("/protected").status_code, 401)
        self.assertEqual(auth.revoke_account("invite-a"), 1)
        self.assertEqual(self.client.get("/protected").status_code, 401)
        first = self.login().json()
        old_cookie = self.client.cookies.get("trippilot_session")
        second_id = auth.create_account("invite-b", PASSWORD)
        switched = self.client.post("/auth/login", json={"username": "invite-b", "password": PASSWORD},
                                    headers={"origin": ORIGIN, "x-csrf-token": first["csrf_token"]})
        self.assertEqual(switched.status_code, 200)
        self.assertEqual(switched.json()["user"]["id"], second_id)
        self.assertNotEqual(switched.json()["session_id"], first["session_id"])
        self.assertEqual(self.client.get("/protected", headers={"cookie": f"trippilot_session={old_cookie}"}).status_code, 401)
        auth.revoke_account("invite-b", disable=True)
        self.assertEqual(self.client.get("/protected").status_code, 401)

    def test_generic_credentials_errors_persisted_rate_limit_and_busy(self):
        headers = self.bootstrap()
        errors = []
        for username in ("invite-a", "not-present"):
            errors.append(self.client.post("/auth/login", json={"username": username, "password": "incorrect"}, headers=headers).json())
        self.assertEqual(errors[0], errors[1])
        for _ in range(4):
            self.assertEqual(self.client.post("/auth/login", json={"username": "invite-a", "password": "incorrect"}, headers=headers).status_code, 401)
        engine_for(str(self.path)).dispose()
        engine_for.cache_clear()
        self.assertEqual(self.client.post("/auth/login", json={"username": "invite-a", "password": PASSWORD}, headers=headers).status_code, 429)
        with patch("app.auth._HASH_CAPACITY.acquire", return_value=False):
            self.assertEqual(self.client.post("/auth/login", json={"username": "invite-c", "password": PASSWORD}, headers=headers).status_code, 429)
        with Session(engine_for(str(self.path))) as db:
            self.assertGreater(len(db.exec(select(LoginBucketRow)).all()), 1)

    def test_unauthorized_does_not_read_body_or_enter_handler(self):
        received = 0
        sent = []
        entered = []
        async def downstream(scope, receive, send): entered.append(True)
        async def receive():
            nonlocal received
            received += 1
            raise AssertionError("Unauthorized media body must not be consumed")
        async def send(message): sent.append(message)
        scope = {"type": "http", "method": "POST", "path": "/media/landmark", "headers": []}
        asyncio.run(auth.AuthenticationMiddleware(downstream)(scope, receive, send))
        self.assertEqual(received, 0)
        self.assertEqual(entered, [])
        self.assertEqual(sent[0]["status"], 401)

    def test_bad_cookie_duplicates_and_unknown_endpoints_denied(self):
        self.login()
        token = self.client.cookies.get("trippilot_session")
        duplicate = f"trippilot_session={token}; trippilot_session={token}"
        self.assertEqual(self.client.get("/protected", headers={"cookie": duplicate}).status_code, 401)
        self.assertEqual(TestClient(self.app).get("/docs").status_code, 401)
        self.assertEqual(TestClient(self.app).get("/health").status_code, 200)

    def test_storage_errors_are_safe_and_private_responses_not_cacheable(self):
        self.login()
        response = self.client.get("/protected")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("Cookie", response.headers["vary"])
        with patch("app.auth.engine_for", side_effect=StorageError("storage_migration_required")):
            for path in ("/protected", "/auth/session"):
                failure = self.client.get(path)
                self.assertEqual(failure.status_code, 503)
                self.assertEqual(failure.json()["detail"]["code"], "identity_unavailable")
                self.assertNotIn(str(self.path), failure.text)
                self.assertNotIn("migration", failure.text)

    def test_global_rate_limit_no_new_buckets_after_exhaustion_and_window_reset(self):
        for index in range(30):
            auth.reserve_login_attempt(f"subject-{index}")
        with self.assertRaisesRegex(Exception, "429"):
            auth.reserve_login_attempt("new-subject")
        with Session(engine_for(str(self.path))) as db:
            self.assertEqual(len(db.exec(select(LoginBucketRow)).all()), 31)
        with patch("app.auth.now_seconds", return_value=auth.now_seconds() + 901):
            auth.reserve_login_attempt("subject-0")

    def test_admin_password_never_argument_output_or_error(self):
        output = io.StringIO()
        with patch("sys.argv", ["account_admin", "create", "invite-c"]), patch("sys.stdin.isatty", return_value=True), patch("getpass.getpass", side_effect=[PASSWORD, PASSWORD]), contextlib.redirect_stdout(output):
            self.assertEqual(account_admin.main(), 0)
        self.assertNotIn(PASSWORD, output.getvalue())
        with patch("sys.argv", ["account_admin", "disable", "invite-c"]), contextlib.redirect_stdout(output):
            self.assertEqual(account_admin.main(), 0)
        self.assertEqual(self.login(username="invite-c").status_code, 401)
        errors = io.StringIO()
        with patch("sys.argv", ["account_admin", "create", "invite-d"]), patch("sys.stdin.isatty", return_value=True), patch("getpass.getpass", side_effect=[PASSWORD, "different"]), contextlib.redirect_stderr(errors):
            self.assertEqual(account_admin.main(), 2)
        self.assertNotIn(PASSWORD, errors.getvalue())
        with patch("sys.argv", ["account_admin", "create", "invite-d"]), patch("sys.stdin.isatty", return_value=False), patch("getpass.getpass", side_effect=AssertionError("No unsafe fallback")), contextlib.redirect_stderr(errors):
            self.assertEqual(account_admin.main(), 2)

    def test_new_login_does_not_keep_unbounded_active_sessions(self):
        with patch("app.auth.reserve_login_attempt"):
            for _ in range(10):
                auth.login("invite-a", PASSWORD, None)
        with Session(engine_for(str(self.path))) as db:
            self.assertEqual(len(db.exec(select(SessionRow)).all()), 8)

    def test_configuration_and_admin_input_limits(self):
        self.assertTrue(auth.AuthSettings(_env_file=None).auth_cookie_secure)
        for value in (["https://example.invalid/path"], [ORIGIN, ORIGIN], []):
            with self.assertRaises(ValueError): auth.AuthSettings(_env_file=None, auth_allowed_origins=value)
        for name, password in (("ab", PASSWORD), ("Upper", PASSWORD), ("invite-z", "short")):
            with self.assertRaises(ValueError): auth.create_account(name, password)


if __name__ == "__main__":
    unittest.main()
