"""One-shot LOCAL browser login through the unchanged normal auth endpoints.

No runtime auth bypass, account/database creation, quota initialization, model,
or arbitrary upstream exists here. Credentials and the short-lived launch URL
stay in a private local directory; neither is printed or logged. The tool must
not be installed as a production service.
"""
from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import hmac
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import stat
import time
from typing import Callable
import webbrowser

import httpx


FRONTEND = "http://trippilot-test.localhost:3104"
BACKEND = "http://127.0.0.1:8802"
ENTRY = "http://trippilot-test.localhost:8804"
ENTRY_HOST = "trippilot-test.localhost:8804"
COOKIE = "trippilot_session"
TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
HEX_ID = re.compile(r"[0-9a-f]{32}")
ROOT = Path(__file__).resolve().parents[2]
RESPONSE_LIMIT = 16 * 1024
ENTRY_SECONDS = 180


class Refused(Exception):
    """Static reason only: never include upstream/credential exception inputs."""


def strict_json(data: bytes) -> object:
    def pairs(items: list) -> dict:
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError()
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))


@dataclass(frozen=True, repr=False)
class Credentials:
    username: str
    password: str = field(repr=False)


def read_credentials(path: Path) -> Credentials:
    """Bounded no-follow read of a current-user-owned 0600 private file."""
    descriptor = None
    try:
        if (not path.is_absolute() or path.resolve().is_relative_to(ROOT)
                or path.is_symlink() or any(parent.is_symlink() for parent in path.parents)):
            raise ValueError()
        parent = path.parent.stat()
        if (parent.st_uid != os.getuid() or not stat.S_ISDIR(parent.st_mode)
                or stat.S_IMODE(parent.st_mode) & 0o077):
            raise ValueError()
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600 or not 1 <= info.st_size <= 4096):
            raise ValueError()
        data = os.read(descriptor, 4097)
        if not 1 <= len(data) <= 4096:
            raise ValueError()
        value = strict_json(data)
        if (not isinstance(value, dict) or set(value) != {"username", "password"}
                or not isinstance(value["username"], str) or re.fullmatch(r"[a-z0-9_.-]{3,48}", value["username"]) is None
                or not isinstance(value["password"], str) or not 12 <= len(value["password"]) <= 128
                or any(ord(c) < 32 or ord(c) == 127 for c in value["password"])):
            raise ValueError()
        return Credentials(value["username"], value["password"])
    except (OSError, ValueError, TypeError, RecursionError, UnicodeError):
        raise Refused("private_credentials_invalid") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _session(value: object, *, authenticated: bool, username: str | None = None) -> dict:
    if (not isinstance(value, dict) or set(value) != {"authenticated", "user", "csrf_token", "expires_at", "session_id"}
            or value["authenticated"] is not authenticated or not isinstance(value["csrf_token"], str)
            or TOKEN.fullmatch(value["csrf_token"]) is None):
        raise Refused("identity_response_invalid")
    if not authenticated:
        if any(value[key] is not None for key in ("user", "expires_at", "session_id")):
            raise Refused("identity_response_invalid")
        return value
    user = value["user"]
    if (not isinstance(user, dict) or set(user) != {"id", "username"} or user["username"] != username
            or not isinstance(user["id"], str) or HEX_ID.fullmatch(user["id"]) is None
            or not isinstance(value["session_id"], str) or HEX_ID.fullmatch(value["session_id"]) is None
            or not isinstance(value["expires_at"], str) or len(value["expires_at"]) > 40):
        raise Refused("identity_response_invalid")
    try:
        expires = datetime.fromisoformat(value["expires_at"].replace("Z", "+00:00"))
        if expires.tzinfo is None or expires <= datetime.now(timezone.utc):
            raise ValueError()
    except (ValueError, TypeError):
        raise Refused("identity_response_invalid") from None
    return value


def _request(client: httpx.Client, method: str, path: str, **kwargs) -> tuple[dict, httpx.Headers]:
    # A fixed constant is always prepended; callers cannot supply a target URL.
    with client.stream(method, BACKEND + path, **kwargs) as response:
        if response.status_code != 200 or response.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
            raise Refused("normal_login_failed_or_uncertain")
        body = bytearray()
        for block in response.iter_bytes():
            if len(body) + len(block) > RESPONSE_LIMIT:
                raise Refused("identity_response_invalid")
            body.extend(block)
        try:
            value = strict_json(bytes(body))
        except (ValueError, TypeError, RecursionError, UnicodeError):
            raise Refused("identity_response_invalid") from None
        return value, response.headers


def _forward_cookie(headers: httpx.Headers) -> str:
    found = []
    try:
        for raw in headers.get_list("set-cookie"):
            if len(raw) > 2048:
                raise ValueError()
            parsed = SimpleCookie()
            parsed.load(raw)
            if COOKIE in parsed:
                found.append(parsed[COOKIE])
        if len(found) != 1:
            raise ValueError()
        cookie = found[0]
        if (TOKEN.fullmatch(cookie.value) is None or cookie["domain"] or cookie["path"] != "/"
                or cookie["samesite"].lower() != "lax" or not cookie["httponly"] or cookie["secure"]
                or not re.fullmatch(r"[1-9][0-9]{0,5}", cookie["max-age"])
                or int(cookie["max-age"]) > 604800):
            raise ValueError()
        # Reconstruct the known local cookie, never relay arbitrary headers or
        # a domain-bearing/production cookie to a different browser origin.
        return f"{COOKIE}={cookie.value}; Path=/; Max-Age={cookie['max-age']}; HttpOnly; SameSite=Lax"
    except (ValueError, TypeError, CookieError):
        raise Refused("local_session_cookie_invalid") from None


def normal_login(credentials: Credentials, *, transport: httpx.BaseTransport | None = None) -> str:
    """Actual password/CSRF/session flow. Failure is consumed, never retried."""
    try:
        with httpx.Client(transport=transport, timeout=10, follow_redirects=False, trust_env=False) as client:
            preauth, _ = _request(client, "GET", "/auth/session")
            preauth = _session(preauth, authenticated=False)
            result, headers = _request(client, "POST", "/auth/login",
                headers={"Origin": FRONTEND, "X-CSRF-Token": preauth["csrf_token"]},
                json={"username": credentials.username, "password": credentials.password})
            checked = _session(result, authenticated=True, username=credentials.username)
            cookie = _forward_cookie(headers)
            verified, _ = _request(client, "GET", "/auth/session")
            verified = _session(verified, authenticated=True, username=credentials.username)
            if checked != verified:
                raise Refused("identity_changed_during_login")
            return cookie
    except Refused:
        raise
    except (httpx.HTTPError, OSError, ValueError, TypeError, RecursionError):
        raise Refused("normal_login_failed_or_uncertain") from None


SCRIPT = """const fragment=location.hash.slice(1);history.replaceState(null,'','/');
const message=document.getElementById('status');
if(!/^[A-Za-z0-9_-]{43}$/.test(fragment)){message.textContent='入口无效，请重新运行本地工具。';}
else{fetch('/enter',{method:'POST',credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:fragment})})
.then(async response=>{if(!response.ok)throw new Error();const result=await response.json();if(result.ok!==true)throw new Error();location.replace('http://trippilot-test.localhost:3104');})
.catch(()=>{message.textContent='本地登录未完成或结果不确定。不会自动重试，请检查本地服务。';});}"""
HTML = ("<!doctype html><html lang='zh-CN'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>TripPilot 本地测试入口</title><h1>TripPilot 本地测试</h1>"
        "<p id='status'>正在通过正常登录建立本地测试会话…</p><p>没有关闭鉴权；不修改线上配置，不调用模型。</p>"
        f"<script>{SCRIPT}</script></html>").encode()
SCRIPT_HASH = base64.b64encode(hashlib.sha256(SCRIPT.encode()).digest()).decode()


class LocalEntry:
    def __init__(self, credentials: Credentials, *, clock: Callable[[], float] = time.monotonic,
                 authenticate: Callable[[Credentials], str] = normal_login):
        self.credentials, self.clock, self.authenticate = credentials, clock, authenticate
        self.token = secrets.token_urlsafe(32)
        self.expires_at = clock() + ENTRY_SECONDS
        self.consumed = False

    def enter(self, token: object) -> str:
        if (not isinstance(token, str) or TOKEN.fullmatch(token) is None
                or not hmac.compare_digest(token, self.token) or self.clock() >= self.expires_at or self.consumed):
            raise Refused("local_entry_expired_or_consumed")
        # The HTTPServer is single-threaded. Consume before the first network
        # operation: timeouts/unknown login outcomes cannot resend passwords.
        self.consumed = True
        return self.authenticate(self.credentials)


def handler(entry: LocalEntry) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *_args) -> None:
            pass

        def send_result(self, status: int, body: bytes, *, cookie: str | None = None, html: bool = False) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8" if html else "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", f"default-src 'none'; script-src 'sha256-{SCRIPT_HASH}'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.send_header("Connection", "close")
            if cookie is not None:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def host_ok(self) -> bool:
            return self.headers.get_all("Host") == [ENTRY_HOST]

        def do_GET(self) -> None:
            if self.path != "/" or not self.host_ok():
                self.send_result(404, b'{"error":"local_entry_not_found"}')
                return
            self.send_result(200, HTML, html=True)

        def do_POST(self) -> None:
            if (self.path != "/enter" or not self.host_ok()
                    or self.headers.get_all("Origin") != [ENTRY]
                    or self.headers.get_all("Sec-Fetch-Site") != ["same-origin"]
                    or self.headers.get("Sec-Fetch-Mode") not in {"cors", "same-origin"}
                    or self.headers.get("Sec-Fetch-Dest") != "empty"
                    or self.headers.get_all("Content-Type") != ["application/json"]
                    or self.headers.get("Transfer-Encoding") is not None):
                self.send_result(403, b'{"error":"local_entry_rejected"}')
                return
            try:
                lengths = self.headers.get_all("Content-Length")
                if not lengths or len(lengths) != 1 or not re.fullmatch(r"[1-9][0-9]{0,2}", lengths[0]):
                    raise ValueError()
                length = int(lengths[0])
                if length > 256:
                    raise ValueError()
                data = self.rfile.read(length)
                if len(data) != length:
                    raise ValueError()
                value = strict_json(data)
                if not isinstance(value, dict) or set(value) != {"token"}:
                    raise ValueError()
                cookie = entry.enter(value["token"])
            except (ValueError, TypeError, RecursionError, UnicodeError, OSError, Refused):
                self.send_result(409, b'{"error":"local_entry_failed_no_retry"}')
                return
            self.send_result(200, b'{"ok":true}', cookie=cookie)
    return Handler


class LocalServer(HTTPServer):
    def handle_error(self, _request, _client_address) -> None:
        # Default HTTPServer prints exception details; credentials never belong
        # in server logs, including unexpected transport/cookie errors.
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--no-open", action="store_true", help="Save private launch URL without opening browser")
    args = parser.parse_args()
    launch = None
    try:
        entry = LocalEntry(read_credentials(args.credentials))
        with LocalServer(("127.0.0.1", 8804), handler(entry)) as server:
            # A private file supports manual opening without putting the token
            # into stdout, shell argv, query strings or HTTP access logs.
            launch = args.credentials.parent / ("local-entry-" + secrets.token_hex(8) + ".txt")
            descriptor = os.open(launch, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "w") as output:
                output.write(ENTRY + "/#" + entry.token + "\n")
            print(f"Private local launch URL saved: {launch}")
            print("Local-only normal login; no model or business API. Press Ctrl+C to close.")
            if not args.no_open:
                webbrowser.open(ENTRY + "/#" + entry.token)
            server.timeout = 0.5
            while not entry.consumed and entry.clock() < entry.expires_at:
                server.handle_request()
            print("Local entry consumed or expired; no automatic retry.")
        return 0
    except KeyboardInterrupt:
        print("Local entry closed.")
        return 0
    except (Refused, OSError):
        print("Local entry unavailable. Inspect private credentials and local services; no automatic retry.")
        return 1
    finally:
        if launch is not None:
            launch.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
