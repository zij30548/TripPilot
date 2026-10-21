"""Acceptance runtime checks with local storage and controlled transports only.

No test starts the CLI, reads private runtime config, opens a real upstream,
creates a production database, or consumes a model acceptance allowance.
"""
import asyncio
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime, timezone
import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx

from app.config import Settings
from app.integrations.qwen import QwenClient, QwenError
from app.schemas.chat import Intent
from app.services.chat import model_payload
from app.usage_runtime import MeteredAsyncClient
from tests import test_chat_api as chat_fixture
from tests.test_chat_places_tools import envelope as tool_envelope
from tools import chat_acceptance_runtime as runtime
from tools.chat_acceptance import (AcceptanceStopped, BATCH, IDS, MODEL, ChatAcceptanceLedger,
    _new, canonical, digest, freeze)


def selection() -> dict:
    return {"requirements": Intent().model_dump(), "sources": [], "issues": []}


def intent_envelope() -> dict:
    return {"id": "controlled-request-1", "model": "controlled-model", "choices": [
        {"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(selection())}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}


def make_ledger(parent: Path, first_payload: dict) -> tuple[ChatAcceptanceLedger, Path]:
    repo, stage = parent / "repo", parent / "stage"
    repo.mkdir()
    (repo / "frozen.py").write_text("controlled implementation")
    old = parent / "old.jsonl"
    old.write_text('{"event":"old-consumed"}\n')
    payloads = [first_payload] + [{"model": MODEL, "controlled_unique": index} for index in range(1, 12)]
    protocol = {"schema_version": "chat-acceptance-v1", "batch": BATCH, "limit": 12, "model": MODEL,
        "source_commit": "a" * 40, "files": {"frozen.py": digest((repo / "frozen.py").read_bytes())},
        "old_ledgers": {str(old): digest(old.read_bytes())},
        "cases": [{"id": identifier, "channel": "api" if identifier.startswith("api") else "chrome",
            "feature": "intent" if index < 4 or identifier == "chrome01" else "places",
            "payload_hash_kind": "wire_bytes", "payload_sha256": digest(runtime.wire(payloads[index])),
            "canonical_payload_sha256": digest(canonical(payloads[index]))} for index, identifier in enumerate(IDS)]}
    directory = stage / "batch"
    freeze(directory, protocol, stage_root=stage, repository_root=repo)
    return ChatAcceptanceLedger(directory, stage_root=stage, repository_root=repo), directory


class ControlledGate:
    """Only a local test gate; all real runtime transports are still wrapped."""
    def __init__(self, ledger: ChatAcceptanceLedger, directory: Path):
        self.ledger, self.directory, self.active, self.stopped = ledger, directory, {}, []

    def model_case(self, request: httpx.Request) -> str:
        self.ledger.verify()
        matches = [case for case in self.ledger.protocol["cases"] if case["payload_sha256"] == digest(request.content)]
        if len(matches) != 1:
            self.stop("payload_mismatch")
            raise AcceptanceStopped("payload_mismatch")
        return matches[0]["id"]

    def stop(self, code: str) -> None:
        self.stopped.append(code)

    def reserve_amap(self, operation: str, request: httpx.Request) -> int:
        raise AssertionError("This controlled intent test must not search POI")


class RuntimeProjectionTests(unittest.TestCase):
    def test_actual_httpx_wire_bytes_and_business_projection_are_stable(self):
        payload = {"model": MODEL, "messages": [{"role": "user", "content": "受控中文"}]}
        self.assertEqual(runtime.wire(payload), httpx.Request("POST", "https://example.invalid", json=payload).content)
        projected = runtime.projected_native(intent_envelope())
        self.assertIn("controlled-request-1", json.dumps(projected))
        self.assertIn("requirements", json.dumps(projected))
        self.assertNotIn("Authorization", json.dumps(projected))

    def test_projection_strips_top_message_and_nested_thinking_and_private_headers(self):
        raw = tool_envelope()
        raw["reasoning_content"] = "PRIVATE_TOP_THOUGHT"
        raw["headers"] = {"Authorization": "PRIVATE_AUTH"}
        message = raw["choices"][0]["message"]
        message["reasoning_content"] = "PRIVATE_MESSAGE_THOUGHT"
        message["tool_calls"][0]["reasoning_content"] = "PRIVATE_NESTED_THOUGHT"
        message["tool_calls"][0]["function"]["secret"] = "PRIVATE_FUNCTION_THOUGHT"
        encoded = json.dumps(runtime.projected_native(raw), ensure_ascii=False)
        for marker in ("PRIVATE_TOP_THOUGHT", "PRIVATE_AUTH", "PRIVATE_MESSAGE_THOUGHT", "PRIVATE_NESTED_THOUGHT", "PRIVATE_FUNCTION_THOUGHT"):
            self.assertNotIn(marker, encoded)

    def test_invalid_arbitrary_content_refusal_and_arguments_are_not_logged_as_text(self):
        raw = intent_envelope()
        raw["choices"][0]["message"]["content"] = "PRIVATE_FREE_MODEL_TEXT"
        raw["choices"][0]["message"]["refusal"] = "PRIVATE_REFUSAL_TEXT"
        encoded = json.dumps(runtime.projected_native(raw))
        self.assertNotIn("PRIVATE_FREE_MODEL_TEXT", encoded)
        self.assertNotIn("PRIVATE_REFUSAL_TEXT", encoded)
        raw = tool_envelope()
        raw["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = '{"private":"PRIVATE_BAD_ARGUMENT"}'
        self.assertNotIn("PRIVATE_BAD_ARGUMENT", json.dumps(runtime.projected_native(raw)))

    def test_derive_payloads_never_sends_case_labels_expected_or_grading(self):
        evaluation = json.loads(runtime.MANIFEST.read_bytes())
        bindings = {"cases": {}}
        for case in evaluation["cases"]:
            identifier = str(uuid4())
            conversation = {"schema_version": "chat-session-v1", "id": identifier, "title": "受控测试会话", "revision": 0,
                "requirements": Intent().model_dump(), "confirmed_revision": None, "sources": [], "issues": [], "messages": [],
                "last_turn": None, "created_at": "2026-10-21T00:00:00+00:00", "updated_at": "2026-10-21T00:00:00+00:00"}
            places = {"schema_version": "chat-places-v1", "conversation_id": identifier, "intent_revision": 0,
                "revision": 0, "city_id": None, "accommodation": None, "required_places": [], "batch": None,
                "last_operation": None, "clarification": None, "proposed_city_id": None, "request_text": None, "stale": False}
            bindings["cases"][case["id"]] = {"conversation": conversation, "places": places}
        # Chrome is a predeclared one-conversation chain. Later contexts freeze
        # only expected counters, not a fabricated future POI/result body.
        bindings["cases"]["chrome"] = deepcopy(bindings["cases"]["chrome01"])
        before = deepcopy(bindings)
        cases, requests = runtime.frozen_payloads(bindings, evaluation, now=datetime(2026, 10, 21, tzinfo=timezone.utc))
        self.assertEqual(tuple(case["id"] for case in cases), IDS)
        self.assertEqual(bindings, before)
        for identifier, request in requests.items():
            payload = request["payload"]
            self.assertEqual(digest(runtime.wire(payload)), next(c["payload_sha256"] for c in cases if c["id"] == identifier))
            text = json.dumps(payload, ensure_ascii=False)
            for forbidden in ("confirmation_delta", "required_issues", "keyword_identity", "expected_workspace_revision", '"forbidden"'):
                self.assertNotIn(forbidden, text)
            self.assertNotIn(identifier, text)


class RuntimeTransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = self.enterContext(TemporaryDirectory())
        self.parent = Path(self.temp)
        self.payload = {"model": MODEL, "stream": False, "enable_thinking": False,
                        "max_tokens": 1024, "messages": [{"role": "user", "content": "controlled"}]}
        self.ledger, directory = make_ledger(self.parent, self.payload)
        self.gate = ControlledGate(self.ledger, directory)
        self.settings = Settings(_env_file=None, qwen_api_key="controlled-not-real-key",
            qwen_base_url="https://controlled.cn-beijing.maas.aliyuncs.com/compatible-mode/v1")
        # Restore both class methods even if an assertion fails. The wrapper
        # still runs around real QwenClient bounded parsing; only HTTP is fake.
        async def bypass_product_meter(self, request, **kwargs):
            return await httpx.AsyncClient.send(self, request, **kwargs)
        self.enterContext(patch.object(MeteredAsyncClient, "send", bypass_product_meter))
        self.enterContext(patch.object(QwenClient, "_request", QwenClient._request))
        runtime.install_live_gate(self.gate)

    async def test_attempt_is_durable_before_transport_and_exact_wire_is_sent_once(self):
        observed = []
        def upstream(request):
            observed.append(request.content)
            status = self.ledger.verify()
            self.assertEqual(status["consumed"], 1)
            self.assertTrue(status["paused"])
            return httpx.Response(200, json=intent_envelope())
        async with MeteredAsyncClient(transport=httpx.MockTransport(upstream)) as client:
            raw = await QwenClient(self.settings, client)._request(self.payload)
            self.assertEqual(raw["id"], "controlled-request-1")
            with self.assertRaises(AcceptanceStopped):
                await QwenClient(self.settings, client)._request(self.payload)
        self.assertEqual(observed, [runtime.wire(self.payload)])
        self.assertEqual(self.ledger.verify()["consumed"], 1)
        self.assertFalse(self.ledger.verify()["paused"])

    async def test_unknown_payload_or_prior_uncertainty_never_reaches_transport(self):
        observed = []
        async with MeteredAsyncClient(transport=httpx.MockTransport(lambda request: observed.append(request))) as client:
            with self.assertRaises(AcceptanceStopped):
                await QwenClient(self.settings, client)._request(self.payload | {"max_tokens": 8})
            self.ledger.reserve("api01", runtime.wire(self.payload))
            with self.assertRaises(AcceptanceStopped):
                await QwenClient(self.settings, client)._request(self.payload)
        self.assertEqual(observed, [])
        self.assertTrue(self.ledger.verify()["paused"])

    async def test_sent_connection_failure_is_counted_paused_and_not_retried(self):
        observed = []
        def upstream(request):
            observed.append(request)
            raise httpx.ConnectError("controlled private socket detail")
        async with MeteredAsyncClient(transport=httpx.MockTransport(upstream)) as client:
            with self.assertRaises(QwenError):
                await QwenClient(self.settings, client)._request(self.payload)
        self.assertEqual(len(observed), 1)
        self.assertEqual(self.ledger.verify()["consumed"], 1)
        self.assertTrue(self.ledger.verify()["paused"])
        self.assertNotIn("private socket", (self.gate.directory / "calls.jsonl").read_text())

    async def test_native_evidence_write_failure_pauses_instead_of_completing_success(self):
        async with MeteredAsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=intent_envelope()))) as client:
            with patch("tools.chat_acceptance_runtime._new", side_effect=OSError("controlled evidence disk failure")):
                with self.assertRaises((AcceptanceStopped, OSError)):
                    await QwenClient(self.settings, client)._request(self.payload)
        self.assertTrue(self.ledger.verify()["paused"])
        self.assertTrue(self.gate.stopped)


class RuntimeAMapJournalTests(unittest.TestCase):
    def setUp(self):
        self.make_gate()

    def make_gate(self):
        parent = Path(self.enterContext(TemporaryDirectory()))
        self.gate = runtime.LiveGate.__new__(runtime.LiveGate)
        self.gate.directory = parent / "batch"
        self.gate.runtime = parent / ".runtime"
        self.gate.directory.mkdir(mode=0o700)
        self.gate.runtime.mkdir(mode=0o700)
        self.gate.active, self.gate.requests = {}, {}
        self.gate.ledger = SimpleNamespace(protocol_hash="a" * 64,
            verify=lambda: {"consumed": 0, "remaining": 12, "paused": False})
        self.gate.identity = {"batch": BATCH, "directory": str(self.gate.directory), "protocol_sha256": "a" * 64,
                              "amap_logical_limit": 6, "amap_physical_limit": 12, "artifacts": {}}
        _new(self.gate.runtime / "identity.json", canonical(self.gate.identity))
        _new(self.gate.runtime / "gate.lock", b"")
        self.init = canonical({"event": "initialized", "protocol_sha256": "a" * 64}) + b"\n"
        _new(self.gate.runtime / "amap.jsonl", self.init)
        self.request = httpx.Request("GET", "https://restapi.amap.com/v5/place/text", params={
            "key": "CONTROLLED_SECRET_NOT_A_KEY", "keywords": "苏州博物馆", "region": "320500",
            "city_limit": "true", "page_size": "20", "page_num": "1"})

    def test_key_is_not_persisted_and_truncation_or_missing_marker_cannot_reset(self):
        self.assertEqual(self.gate.reserve_amap("api05", self.request), 1)
        journal = self.gate.runtime / "amap.jsonl"
        full = journal.read_bytes()
        self.assertNotIn(b"CONTROLLED_SECRET", full)
        journal.write_bytes(self.init)
        with self.assertRaises(AcceptanceStopped):
            self.gate.amap_rows()
        journal.write_bytes(full[:-1])
        with self.assertRaises(AcceptanceStopped):
            self.gate.amap_rows()
        journal.write_bytes(full)
        (self.gate.runtime / "amap-01.json").unlink()
        with self.assertRaises((AcceptanceStopped, OSError)):
            self.gate.reserve_amap("api06", self.request)

    def test_unfinished_amap_dispatch_after_restart_blocks_new_send(self):
        self.assertEqual(self.gate.reserve_amap("api05", self.request), 1)
        with self.assertRaises(AcceptanceStopped):
            self.gate.reserve_amap("api06", self.request)

    def test_wrong_method_host_parameters_never_reserve(self):
        for request in (httpx.Request("GET", "https://example.invalid/v5/place/text"),
                httpx.Request("POST", "https://restapi.amap.com/v5/place/text"),
                httpx.Request("GET", "https://restapi.amap.com/v3/direction/walking"),
                httpx.Request("GET", "https://restapi.amap.com/v5/place/text?key=fake")):
            # New isolated gate each time because the deliberate rejection writes
            # a permanent stop marker; no reset is performed by the runtime.
            self.make_gate()
            with self.subTest(url=str(request.url)), self.assertRaises(AcceptanceStopped):
                self.gate.reserve_amap("api05", request)
            self.assertEqual((self.gate.runtime / "amap.jsonl").read_bytes(), self.init)


class RuntimeAMapStreamingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        RuntimeAMapJournalTests.make_gate(self)
        async def bypass_product_meter(self, request, **kwargs):
            return await httpx.AsyncClient.send(self, request, **kwargs)
        self.enterContext(patch.object(MeteredAsyncClient, "send", bypass_product_meter))
        self.enterContext(patch.object(QwenClient, "_request", QwenClient._request))
        runtime.install_live_gate(self.gate)
        token = runtime._CASE.set("api05")
        self.addCleanup(runtime._CASE.reset, token)

    async def test_headers_are_not_completed_evidence_until_full_body_read(self):
        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'{"status":"1","pois":[]}'
        async with MeteredAsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Body()))) as client:
            async with client.stream("GET", self.request.url) as response:
                self.assertFalse((self.gate.directory / "amap-01-transport.json").exists())
                self.assertEqual(await response.aread(), b'{"status":"1","pois":[]}')
        result = json.loads((self.gate.directory / "amap-01-transport.json").read_text())
        self.assertEqual(result["http_status"], 200)
        self.assertNotIn("error", result)
        self.assertEqual(len(self.gate.amap_rows()), 2)

    async def test_read_timeout_after_headers_pauses_and_cannot_start_later_query(self):
        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'{"status":'
                raise httpx.ReadTimeout("controlled private read detail")
        async with MeteredAsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Body()))) as client:
            with self.assertRaises(httpx.ReadTimeout):
                async with client.stream("GET", self.request.url) as response:
                    await response.aread()
        self.assertTrue((self.gate.runtime / "stop.json").exists())
        with self.assertRaises(AcceptanceStopped):
            self.gate.reserve_amap("api06", self.request)
        artifact = (self.gate.directory / "amap-01-transport.json").read_text()
        self.assertNotIn("private read", artifact)

    async def test_early_stream_close_is_uncertain_not_success(self):
        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"first"
                yield b"last"
        async with MeteredAsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Body()))) as client:
            async with client.stream("GET", self.request.url):
                pass
        self.assertTrue((self.gate.runtime / "stop.json").exists())
        with self.assertRaises(AcceptanceStopped):
            self.gate.verify()

    async def test_existing_one_connect_retry_consumes_two_physical_attempts_only(self):
        from app.integrations.chat_places import ChatPlacesClient
        calls = []
        def upstream(request):
            calls.append(request)
            if len(calls) == 1:
                raise httpx.ConnectError("controlled connection failure")
            return httpx.Response(200, json={"status": "1", "pois": []})
        async with MeteredAsyncClient(transport=httpx.MockTransport(upstream)) as client:
            self.assertEqual(await ChatPlacesClient(client, "controlled-not-real-key").search("苏州博物馆", "320500"), [])
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(self.gate.amap_rows()), 3)
        first = json.loads((self.gate.directory / "amap-01-transport.json").read_text())
        self.assertEqual(first["error"], "connect_failure")


class RuntimeHTTPBoundaryTests(unittest.TestCase):
    login = chat_fixture.ChatAPIBoundaryTests.login
    create = chat_fixture.ChatAPIBoundaryTests.create
    turn = chat_fixture.ChatAPIBoundaryTests.turn
    dispose = chat_fixture.ChatAPIBoundaryTests.dispose
    usage_rows = chat_fixture.ChatAPIBoundaryTests.usage_rows
    upstream = chat_fixture.ChatAPIBoundaryTests.upstream

    def setUp(self):
        chat_fixture.ChatAPIBoundaryTests.setUp(self)

    def test_actual_auth_csrf_route_and_metering_with_controlled_model(self):
        headers = self.login(self.a)
        view = self.create(headers)
        text = "受控不修改原需求"
        seed = deepcopy(view)
        seed["messages"].append({"role": "user", "text": text})
        payload = model_payload(seed, text, model=MODEL, now=datetime.now(timezone.utc))
        directory = Path(self.enterContext(TemporaryDirectory()))
        ledger, evidence = make_ledger(directory, payload)
        gate = ControlledGate(ledger, evidence)
        self.enterContext(patch.object(MeteredAsyncClient, "send", MeteredAsyncClient.send))
        self.enterContext(patch.object(QwenClient, "_request", QwenClient._request))
        runtime.install_live_gate(gate)
        # No session, wrong CSRF and cross-owner are blocked before the send gate.
        path = f"/chat/conversations/{view['id']}/turns"
        body = {"operation_id": str(uuid4()), "expected_revision": 0, "text": text}
        self.assertEqual(self.c.post(path, json=body).status_code, 401)
        self.assertEqual(self.a.post(path, json=body).status_code, 403)
        other_headers = self.login(self.b, "b")
        self.assertEqual(self.b.post(path, json=body, headers=other_headers).status_code, 404)
        self.assertEqual(ledger.verify()["consumed"], 0)
        self.assertEqual(self.calls, [])
        self.results.append(selection())
        response = self.turn(view, text, headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["last_turn"]["state"], "succeeded")
        self.assertEqual(ledger.verify()["consumed"], 1)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.usage_rows(), [(self.first_id, "qwen_text", "received")])
        self.assertTrue((evidence / "api01-native.json").exists())
        self.assertEqual(response.json()["requirements"], view["requirements"])

    def test_runner_reuses_real_asgi_auth_response_contract_without_relogin(self):
        """Regression for the live-runner's former invented state-field check.

        Identity, cookies, CSRF and two chat routes use the actual ASGI app and
        isolated fixture store. Only the downstream model is MockTransport.
        """
        headers = self.login(self.a)
        views = [self.create(headers), self.create(headers)]
        session_view = self.a.get("/auth/session").json()
        self.assertIs(session_view["authenticated"], True)
        self.assertNotIn("state", session_view)
        self.assertNotIn("status", session_view)
        parent = Path(self.enterContext(TemporaryDirectory()))
        private, evidence = parent / "private", parent / "evidence"
        private.mkdir(mode=0o700); evidence.mkdir(mode=0o700)
        _new(private / "account-a.json", canonical({"username": "chat-a", "password": "controlled-chat-a-password"}))
        self.results.extend([selection(), selection()])
        stopped = []
        def status():
            return {"consumed": len(self.calls), "remaining": 12 - len(self.calls), "paused": False}
        gate = SimpleNamespace(requests={identifier: {
            "path": f"/chat/conversations/{view['id']}/turns",
            "body": {"operation_id": str(uuid4()), "expected_revision": 0, "text": "受控保持意向"}}
            for identifier, view in zip(IDS[:2], views, strict=True)},
            ledger=SimpleNamespace(verify=status), verify=status, stop=stopped.append)
        original_client = httpx.AsyncClient
        auth_calls = []
        async def observe(request):
            if request.url.path.startswith("/auth/"):
                auth_calls.append((request.method, request.url.path))
        def client(**kwargs):
            from main import app
            return original_client(transport=httpx.ASGITransport(app=app),
                event_hooks={"request": [observe]}, **kwargs)
        with patch.object(runtime, "LiveGate", return_value=gate), patch.object(runtime, "ORIGIN", chat_fixture.ORIGIN), \
                patch.object(runtime.httpx, "AsyncClient", side_effect=client), redirect_stdout(StringIO()):
            asyncio.run(runtime.api_run(evidence, private, "api01"))
            asyncio.run(runtime.api_run(evidence, private, "api02"))
            self.assertEqual(stopped, [])
            self.assertEqual(auth_calls.count(("POST", "/auth/login")), 1)
            self.assertEqual(auth_calls.count(("GET", "/auth/session")), 2)
            # Real logout revokes that cached session. The next planned action
            # must stop before its business request, not silently log in again.
            cached = json.loads((private / "api-session.json").read_text())
            async def revoke_cached_session():
                from main import app
                async with original_client(transport=httpx.ASGITransport(app=app), base_url=runtime.API) as logout_client:
                    logout_client.cookies.update(cached["cookies"])
                    response = await logout_client.post("/auth/logout", headers={"Origin": chat_fixture.ORIGIN,
                        "X-CSRF-Token": cached["csrf_token"]})
                    self.assertEqual(response.status_code, 200)
                    self.assertIs(response.json()["authenticated"], False)
            asyncio.run(revoke_cached_session())
            with self.assertRaisesRegex(AcceptanceStopped, "^test_session_expired_or_changed$"):
                asyncio.run(runtime.api_run(evidence, private, "api03"))
        self.assertEqual(stopped, ["test_session_expired_or_changed"])
        self.assertEqual(auth_calls.count(("POST", "/auth/login")), 1)
        self.assertEqual(auth_calls.count(("GET", "/auth/session")), 3)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.usage_rows(), [(self.first_id, "qwen_text", "received")] * 2)
        self.assertEqual((private / "api-session.json").stat().st_mode & 0o777, 0o600)
        for identifier in IDS[:2]:
            body = json.loads((evidence / f"{identifier}-application.json").read_text())
            self.assertEqual(body["http_status"], 200)
            self.assertEqual(body["response"]["last_turn"]["state"], "succeeded")
        self.assertFalse((evidence / "api03-application.json").exists())


class RuntimeRunnerSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_api_runner_reuses_private_session_instead_of_repeated_login(self):
        root = Path(self.enterContext(TemporaryDirectory()))
        private, evidence = root / "private", root / "evidence"
        private.mkdir(mode=0o700); evidence.mkdir(mode=0o700)
        _new(private / "account-a.json", canonical({"username": "controlled-user", "password": "controlled-not-real-password"}))
        from app.auth import anonymous_view, user_view
        from app.identity_models import AccountRow, SessionRow
        account = AccountRow(id="a" * 32, username="controlled-user", password_hash="never-returned", created_at=1)
        session = SessionRow(id="b" * 32, token_hash="c" * 64, user_id=account.id,
            csrf_token="d" * 43, created_at=1, expires_at=2_000_000_000)
        authenticated = user_view(account, session)
        anonymous = anonymous_view(session.csrf_token)
        self.assertEqual(set(authenticated), {"authenticated", "user", "csrf_token", "expires_at", "session_id"})
        progress, logins, business = 0, 0, []
        def status():
            return {"consumed": progress, "remaining": 12 - progress, "paused": False}
        gate = SimpleNamespace(requests={identifier: {"path": f"/controlled/{identifier}", "body": {"operation_id": identifier}}
            for identifier in IDS[:8]}, ledger=SimpleNamespace(verify=status), verify=status,
            stop=lambda _: self.fail("Controlled success must not pause"))
        def upstream(request):
            nonlocal progress, logins
            if request.url.path == "/auth/session":
                return httpx.Response(200, json=authenticated if request.headers.get("cookie") else anonymous)
            if request.url.path == "/auth/login":
                logins += 1
                self.assertEqual(request.headers["Origin"], runtime.ORIGIN)
                self.assertEqual(request.headers["X-CSRF-Token"], session.csrf_token)
                return httpx.Response(200, json=authenticated,
                    headers={"set-cookie": "controlled_session=controlled-not-real-cookie; Path=/; HttpOnly"})
            self.assertIn("controlled_session=", request.headers.get("cookie", ""))
            self.assertEqual(request.headers["X-CSRF-Token"], session.csrf_token)
            business.append(request.url.path)
            progress += 1
            return httpx.Response(200, json={"last_turn": {"state": "succeeded"}})
        original_client = httpx.AsyncClient
        def client(**kwargs):
            return original_client(transport=httpx.MockTransport(upstream), **kwargs)
        with patch.object(runtime, "LiveGate", return_value=gate), patch.object(runtime.httpx, "AsyncClient", side_effect=client), redirect_stdout(StringIO()):
            await runtime.api_run(evidence, private, "api01")
            await runtime.api_run(evidence, private, "api02")
        self.assertEqual(logins, 1)
        self.assertEqual(business, ["/controlled/api01", "/controlled/api02"])
        self.assertEqual((private / "api-session.json").stat().st_mode & 0o777, 0o600)
        for artifact in evidence.iterdir():
            text = artifact.read_text()
            self.assertNotIn("controlled-not-real-password", text)
            self.assertNotIn("controlled-not-real-cookie", text)


if __name__ == "__main__":
    unittest.main()
