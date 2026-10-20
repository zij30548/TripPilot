"""Local-only bootstrap: real private files/HTTP, controlled auth transport."""
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import httpx

from tools import local_access as access


def anonymous() -> dict:
    return {"authenticated": False, "user": None, "csrf_token": "p" * 43,
            "expires_at": None, "session_id": None}


def authenticated() -> dict:
    return {"authenticated": True, "user": {"id": "a" * 32, "username": "local-test"},
            "csrf_token": "s" * 43, "session_id": "b" * 32,
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat()}


COOKIE = "trippilot_session=" + "t" * 43 + "; Path=/; Max-Age=28800; HttpOnly; SameSite=lax"


class LocalCredentialsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="trippilot-local-access-"))).resolve()
        self.folder.chmod(0o700)
        self.file = self.folder / "credentials.json"
        self.file.write_text(json.dumps({"username": "local-test", "password": "private-random-password"}))
        self.file.chmod(0o600)

    def test_valid_file_and_no_secret_repr(self) -> None:
        credentials = access.read_credentials(self.file)
        self.assertEqual(credentials.username, "local-test")
        self.assertEqual(credentials.password, "private-random-password")
        self.assertNotIn(credentials.password, repr(credentials))

    def test_private_file_parent_owner_and_links_are_required(self) -> None:
        self.file.chmod(0o640)
        with self.assertRaisesRegex(access.Refused, "^private_credentials_invalid$"):
            access.read_credentials(self.file)
        self.file.chmod(0o600)
        self.folder.chmod(0o750)
        with self.assertRaises(access.Refused):
            access.read_credentials(self.file)
        self.folder.chmod(0o700)
        link = self.folder / "link.json"
        link.symlink_to(self.file)
        with self.assertRaises(access.Refused):
            access.read_credentials(link)
        hard = self.folder / "hard.json"
        os.link(self.file, hard)
        with self.assertRaises(access.Refused):
            access.read_credentials(self.file)
        hard.unlink()
        with patch("tools.local_access.os.getuid", return_value=os.getuid() + 1):
            with self.assertRaises(access.Refused):
                access.read_credentials(self.file)

    def test_nonregular_relative_repository_and_oversize_are_rejected(self) -> None:
        with self.assertRaises(access.Refused):
            access.read_credentials(Path("credentials.json"))
        with self.assertRaises(access.Refused):
            access.read_credentials(access.ROOT / "backend" / ".env")
        self.file.write_bytes(b"x" * 4097)
        with self.assertRaises(access.Refused):
            access.read_credentials(self.file)
        fifo = self.folder / "fifo"
        os.mkfifo(fifo, 0o600)
        with self.assertRaises(access.Refused):
            access.read_credentials(fifo)

    def test_strict_shape_duplicate_keys_and_invalid_values(self) -> None:
        values = [
            b'{"username":"local-test","username":"other","password":"long-enough-pass"}',
            b'{"username":"local-test","password":"long-enough-pass","owner_id":"any"}',
            b'{"username":"local-test","password":null}',
            b'{"username":"UPPER","password":"long-enough-pass"}',
            b'{"username":"local-test","password":"short"}',
            b'{"username":"local-test","password":NaN}',
        ]
        for value in values:
            with self.subTest(value=value[:35]):
                self.file.write_bytes(value)
                with self.assertRaises(access.Refused):
                    access.read_credentials(self.file)


class LocalNormalLoginTests(unittest.TestCase):
    def setUp(self) -> None:
        self.credentials = access.Credentials("local-test", "private-random-password")
        self.calls: list[httpx.Request] = []
        self.identity = authenticated()

    def upstream(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        self.assertEqual(str(request.url).split("/auth")[0], access.BACKEND)
        if len(self.calls) == 1:
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.url.path, "/auth/session")
            return httpx.Response(200, json=anonymous(), headers={
                "Set-Cookie": "trippilot_preauth=" + "p" * 43 + "; Path=/; HttpOnly; SameSite=lax"})
        if len(self.calls) == 2:
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.path, "/auth/login")
            self.assertEqual(request.headers["Origin"], access.FRONTEND)
            self.assertEqual(request.headers["X-CSRF-Token"], "p" * 43)
            self.assertIn("trippilot_preauth=", request.headers["Cookie"])
            self.assertEqual(json.loads(request.content), {"username": self.credentials.username, "password": self.credentials.password})
            return httpx.Response(200, json=self.identity, headers={"Set-Cookie": COOKIE})
        self.assertEqual(request.url.path, "/auth/session")
        self.assertIn("trippilot_session=", request.headers["Cookie"])
        return httpx.Response(200, json=self.identity)

    def test_normal_csrf_password_flow_then_actual_session_confirmation(self) -> None:
        result = access.normal_login(self.credentials, transport=httpx.MockTransport(self.upstream))
        self.assertEqual(len(self.calls), 3)
        self.assertIn("HttpOnly; SameSite=Lax", result)
        self.assertNotIn("Domain", result)
        self.assertNotIn(self.credentials.password, result)

    def test_redirect_or_failure_never_follows_or_retries(self) -> None:
        for status in (301, 302, 401, 429, 503):
            calls = []
            def reject(request):
                calls.append(str(request.url))
                return httpx.Response(status, headers={"Location": "https://attacker.invalid/private"})
            with self.subTest(status=status), self.assertRaises(access.Refused):
                access.normal_login(self.credentials, transport=httpx.MockTransport(reject))
            self.assertEqual(calls, [access.BACKEND + "/auth/session"])

    def test_oversize_bad_json_and_identity_structures_fail_closed(self) -> None:
        values = [b"x" * (access.RESPONSE_LIMIT + 1), b"not-json",
                  b'{"authenticated":false,"authenticated":false}', json.dumps(anonymous() | {"extra": True}).encode(),
                  json.dumps(anonymous() | {"csrf_token": "bad"}).encode()]
        for value in values:
            calls = []
            def malformed(request):
                calls.append(request)
                return httpx.Response(200, content=value, headers={"Content-Type": "application/json"})
            with self.subTest(value=value[:30]), self.assertRaises(access.Refused):
                access.normal_login(self.credentials, transport=httpx.MockTransport(malformed))
            self.assertEqual(len(calls), 1)

    def test_cookie_security_filter_and_duplicate_cookie_rejection(self) -> None:
        for bad in [COOKIE + "; Domain=localhost", COOKIE + "; Secure", COOKIE.replace("HttpOnly; ", ""),
                    COOKIE.replace("lax", "none"), COOKIE.replace("Path=/", "Path=/auth"),
                    COOKIE.replace("28800", "999999"), COOKIE.replace("t" * 43, "bad")]:
            with self.subTest(cookie=bad[-30:]), self.assertRaises(access.Refused):
                access._forward_cookie(httpx.Headers({"Set-Cookie": bad}))
        with self.assertRaises(access.Refused):
            access._forward_cookie(httpx.Headers([("Set-Cookie", COOKIE), ("Set-Cookie", COOKIE)]))

    def test_wrong_account_changed_session_and_expired_session_are_rejected(self) -> None:
        self.identity["user"]["username"] = "other-account"
        with self.assertRaises(access.Refused):
            access.normal_login(self.credentials, transport=httpx.MockTransport(self.upstream))
        self.assertEqual(len(self.calls), 2)
        self.identity = authenticated()
        self.identity["expires_at"] = "2000-01-01T00:00:00Z"
        self.calls = []
        with self.assertRaises(access.Refused):
            access.normal_login(self.credentials, transport=httpx.MockTransport(self.upstream))
        self.assertEqual(len(self.calls), 2)
        self.identity = authenticated()
        self.calls = []
        def changed(request):
            result = self.upstream(request)
            if len(self.calls) == 3:
                return httpx.Response(200, json=self.identity | {"session_id": "c" * 32})
            return result
        with self.assertRaisesRegex(access.Refused, "identity_changed"):
            access.normal_login(self.credentials, transport=httpx.MockTransport(changed))

    def test_network_errors_are_static_and_no_secrets_are_logged(self) -> None:
        stream = io.StringIO()
        def fail(request):
            raise httpx.ReadTimeout(self.credentials.password + " " + "t" * 43, request=request)
        with redirect_stdout(stream), redirect_stderr(stream):
            with self.assertRaises(access.Refused) as error:
                access.normal_login(self.credentials, transport=httpx.MockTransport(fail))
        self.assertEqual(str(error.exception), "normal_login_failed_or_uncertain")
        self.assertEqual(stream.getvalue(), "")


class LocalEntryTests(unittest.TestCase):
    def test_expiry_consumption_and_uncertain_result_cannot_retry(self) -> None:
        now = [100.0]
        calls = []
        entry = access.LocalEntry(access.Credentials("local-test", "private-random-password"),
            clock=lambda: now[0], authenticate=lambda value: calls.append(value) or COOKIE)
        with self.assertRaises(access.Refused):
            entry.enter("bad")
        self.assertFalse(entry.consumed)
        self.assertEqual(entry.enter(entry.token), COOKIE)
        with self.assertRaises(access.Refused):
            entry.enter(entry.token)
        self.assertEqual(len(calls), 1)
        expired = access.LocalEntry(entry.credentials, clock=lambda: now[0], authenticate=entry.authenticate)
        now[0] += access.ENTRY_SECONDS
        with self.assertRaises(access.Refused):
            expired.enter(expired.token)
        def fail(_):
            raise access.Refused("normal_login_failed_or_uncertain")
        failed = access.LocalEntry(entry.credentials, authenticate=fail)
        with self.assertRaises(access.Refused):
            failed.enter(failed.token)
        self.assertTrue(failed.consumed)
        with self.assertRaisesRegex(access.Refused, "consumed"):
            failed.enter(failed.token)

    def test_real_loopback_http_host_origin_and_browser_cookie_contract(self) -> None:
        calls = []
        entry = access.LocalEntry(access.Credentials("local-test", "private-random-password"),
            authenticate=lambda value: calls.append(value) or COOKIE)
        server = access.LocalServer(("127.0.0.1", 0), access.handler(entry))
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), thread.join(2)))
        base = "http://127.0.0.1:" + str(server.server_address[1])
        headers = {"Host": access.ENTRY_HOST, "Origin": access.ENTRY, "Sec-Fetch-Site": "same-origin",
                   "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"}
        with httpx.Client(trust_env=False) as client:
            self.assertEqual(client.get(base + "/").status_code, 404)
            page = client.get(base + "/", headers={"Host": access.ENTRY_HOST})
            self.assertEqual(page.status_code, 200)
            self.assertEqual(page.headers["Referrer-Policy"], "no-referrer")
            self.assertNotIn(entry.token, page.text)
            self.assertNotIn(entry.credentials.password, page.text)
            self.assertEqual(client.get(base + "/?token=leak", headers={"Host": access.ENTRY_HOST}).status_code, 404)
            for key, value in (("Host", "production.invalid"), ("Origin", access.FRONTEND),
                               ("Sec-Fetch-Site", "cross-site"), ("Sec-Fetch-Mode", "navigate"),
                               ("Sec-Fetch-Dest", "document")):
                response = client.post(base + "/enter", headers=headers | {key: value}, json={"token": entry.token})
                self.assertEqual(response.status_code, 403)
                self.assertFalse(entry.consumed)
            self.assertEqual(client.post(base + "/enter", headers=headers, json={"token": entry.token, "url": "https://production.invalid"}).status_code, 409)
            good = client.post(base + "/enter", headers=headers, json={"token": entry.token})
            self.assertEqual(good.status_code, 200)
            self.assertEqual(good.json(), {"ok": True})
            self.assertIn("HttpOnly", good.headers["Set-Cookie"])
            self.assertEqual(client.post(base + "/enter", headers=headers, json={"token": entry.token}).status_code, 409)
        self.assertEqual(len(calls), 1)

    def test_cli_cannot_select_backend_or_production_urls(self) -> None:
        for option in ("--backend", "--frontend", "--host", "--port"):
            with patch("sys.argv", ["local_access", "--credentials", "/private/no-file", option, "https://production.invalid"]), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    access.main()
            self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
