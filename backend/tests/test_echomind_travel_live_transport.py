"""Offline live-admission checks: fake constructor, real private journal/quota.

No Settings discovery and no actual upstream connections. These are not live
semantic grades or an authorization to run the separate acceptance executor.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from pydantic import SecretStr

if importlib.util.find_spec("jsonschema") is None:
    raise unittest.SkipTest("Live admission needs the explicit echomind extra")

from core.config import Settings
from core.usage_runtime import MeteredAsyncClient
from tests.usage_fixture import UsageFixture
from tools.echomind_travel_live_transport import FILENAME, LiveLedger, live_client
from tools.echomind_travel_trial_ledger import Ledger, LedgerError
from tools.echomind_travel_trial_transport import PARAMETERS, TransportStopped, digest

QUESTION = "查看第二天安排，不修改当前行程。"
ROUTE = "/chat/conversations/12345678-1234-4234-8234-123456789abc/assistant-turns"
BINDING = "b" * 64
BINDINGS = {"grant_sha256": "e" * 64, "plan_protocol_sha256": "a" * 64,
            "execution_sha256": "c" * 64, "prior_ledger_hashes": {"prior_original": "d" * 64}}
URL = "https://offline-workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions"


def payload() -> dict:
    return dict(PARAMETERS, messages=[{"role": "system", "content": "固定规则"},
        {"role": "user", "content": QUESTION}], tools=[{"type": "function", "function": {
            "name": "inspect_trip", "description": "只读", "parameters": {"type": "object",
                "properties": {}, "required": [], "additionalProperties": False}}}])


def response(number: int = 1) -> dict:
    return {"model": PARAMETERS["model"], "choices": [{"index": 0, "finish_reason": "tool_calls",
        "message": {"role": "assistant", "content": None, "tool_calls": [{"index": 0,
            "id": f"offline_call_{number}", "type": "function", "function": {
                "name": "inspect_trip", "arguments": "{}"}}]}}],
        "usage": {"prompt_tokens": 13, "completion_tokens": 7}}


def feedback(previous: dict, number: int = 1) -> dict:
    result = deepcopy(previous)
    call = response(number)["choices"][0]["message"]["tool_calls"][0]
    result["messages"].extend([{"role": "assistant", "content": None,
        "tool_calls": [{key: value for key, value in call.items() if key != "index"}]},
        {"role": "tool", "tool_call_id": call["id"], "content": '{"state":"offline_read"}'}])
    return result


class LiveJournalTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.path = self.directory / FILENAME
        self.ledger = LiveLedger.create(self.path, binding=BINDINGS)

    def test_live_header_not_accepted_by_controlled_reader_and_cannot_reinitialize(self):
        self.assertEqual(self.ledger.verify(binding=BINDINGS)["mode"], "live")
        with self.assertRaises(LedgerError):
            Ledger(self.path).summary()
        before = self.path.read_bytes()
        with self.assertRaises(LedgerError):
            LiveLedger.create(self.path, binding=BINDINGS)
        self.assertEqual(self.path.read_bytes(), before)
        with self.assertRaises(LedgerError):
            LiveLedger.create(self.directory / "another.jsonl", binding=BINDINGS)

    def test_real_header_bindings_are_mandatory_and_changed_grant_is_rejected(self):
        with self.assertRaises(LedgerError):
            self.ledger.verify(binding=dict(BINDINGS, grant_sha256="f" * 64))
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_pending_survives_restart_and_thread_race_has_one_winner(self):
        self.ledger.start_operation("m01", BINDING)
        def claim(number):
            try:
                LiveLedger(self.path).claim("m01", digest(str(number).encode()), binding_sha256=BINDING)
                return True
            except LedgerError:
                return False
        with ThreadPoolExecutor(max_workers=12) as pool:
            self.assertEqual(sum(pool.map(claim, range(12))), 1)
        reopened = LiveLedger(self.path)
        self.assertTrue(reopened.summary()["pending"])
        with self.assertRaises(LedgerError):
            reopened.claim("m01", "f" * 64, binding_sha256=BINDING)

    def test_all_seven_share_twenty_one_limit_no_extra_operations_or_resends(self):
        for operation in range(1, 8):
            op = f"m{operation:02}"
            self.ledger.start_operation(op, BINDING)
            for number in range(3):
                attempt = self.ledger.claim(op, digest(f"{op}-{number}".encode()), binding_sha256=BINDING)
                self.ledger.finish(attempt, "received")
            with self.assertRaises(LedgerError):
                self.ledger.claim(op, digest(b"fourth"), binding_sha256=BINDING)
            self.ledger.seal_operation(op)
        self.assertEqual(self.ledger.summary()["reserved_attempts"], 21)
        with self.assertRaises(LedgerError):
            self.ledger.start_operation("m01", BINDING)
        with self.assertRaises(LedgerError):
            self.ledger.start_operation("m08", BINDING)

    def test_dependency_skip_is_durable_zero_claim_and_not_a_retry(self):
        self.ledger.skip_operation("m01")
        state = LiveLedger(self.path).summary()
        self.assertEqual(state["reserved_attempts"], 0)
        self.assertEqual(state["operations"]["m01"]["skipped"], "dependency_failed")
        with self.assertRaises(LedgerError):
            self.ledger.start_operation("m01", BINDING)
        with self.assertRaises(LedgerError):
            self.ledger.skip_operation("m02", reason="any_failure")
        self.ledger.start_operation("m02", BINDING)

    def test_pause_never_reopens_after_pending_truthful_finish(self):
        self.ledger.start_operation("m01", BINDING)
        attempt = self.ledger.claim("m01", "f" * 64, binding_sha256=BINDING)
        self.ledger.pause()
        self.ledger.finish(attempt, "received")
        self.assertTrue(LiveLedger(self.path).summary()["paused"])
        with self.assertRaises(LedgerError):
            self.ledger.skip_operation("m02")


class LiveTransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.path = self.directory / FILENAME
        self.ledger = LiveLedger.create(self.path, binding=BINDINGS)
        self.usage = UsageFixture()
        self.addCleanup(self.usage.close)
        self.sent, self.captured, self.observed = [], [], []
        self.settings = Settings.model_construct(qwen_model=PARAMETERS["model"],
            qwen_base_url=URL.removesuffix("/chat/completions"),
            qwen_api_key=SecretStr("offline-only-never-a-real-secret"))
        self.current = BINDING
        self.transport_options = None
        self.enterContext(patch("tools.echomind_travel_live_transport.httpx.AsyncHTTPTransport",
                                side_effect=self.transport))

    def transport(self, **kwargs):
        self.transport_options = kwargs
        return httpx.MockTransport(self.handler)

    def handler(self, request):
        # This is a test-only Mock peer, not the live constructor implementation.
        self.assertGreater(self.ledger.summary()["reserved_attempts"], len(self.sent))
        self.sent.append(request.content)
        return httpx.Response(200, json=response(len(self.sent)))

    def client(self, **changes):
        options = {"current_binding": lambda: self.current, "verify_execution": lambda: None,
                   "persist_initial": lambda raw, binding: self.captured.append((digest(raw), binding)),
                   "observe_response": lambda attempt, evidence: self.observed.append((attempt, evidence))}
        options.update(changes)
        return live_client(self.ledger, "m01", BINDING, QUESTION, None, self.settings, **options)

    async def test_exact_client_actual_workspace_endpoint_and_persist_before_native_send(self):
        with self.usage.bind(ROUTE):
            async with self.client() as client:
                self.assertIs(type(client), MeteredAsyncClient)
                await client.post(URL, json=payload())
                await client.post(URL, json=feedback(payload()))
        self.assertEqual(self.transport_options, {"retries": 0, "trust_env": False})
        self.assertEqual(self.ledger.summary()["reserved_attempts"], 2)
        self.assertEqual(self.captured, [(digest(self.sent[0]), BINDING)])
        self.assertEqual([item[0] for item in self.observed], ["m01-a01", "m01-a02"])
        self.assertEqual(self.observed[0][1]["usage"], {"input_tokens": 13, "output_tokens": 7})
        raw = self.path.read_bytes()
        self.assertNotIn(QUESTION.encode(), raw)
        self.assertNotIn(b"offline-only-never-a-real-secret", raw)
        self.assertIn(b'"native_tool_calls":1', raw)

    async def test_frozen_question_rejects_before_claim_and_sticky_pauses(self):
        invalid = payload()
        invalid["messages"][-1]["content"] = "different"
        with self.usage.bind(ROUTE), self.assertRaises(TransportStopped):
            async with self.client() as client:
                await client.post(URL, json=invalid)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.ledger.summary()["reserved_attempts"], 0)
        self.assertTrue(self.ledger.summary()["paused"])

    async def test_version_failure_before_send_stops_without_claim(self):
        def changed():
            raise RuntimeError("fixed_test_error")
        with self.assertRaises(RuntimeError):
            async with self.client(verify_execution=changed):
                pass
        self.assertEqual(self.sent, [])
        self.assertTrue(self.ledger.summary()["paused"])

    async def test_receipt_persist_failure_stops_before_claim(self):
        def failure(raw, binding):
            raise OSError("fixed_test_error")
        with self.usage.bind(ROUTE), self.assertRaises(OSError):
            async with self.client(persist_initial=failure) as client:
                await client.post(URL, json=payload())
        self.assertEqual(self.sent, [])
        self.assertEqual(self.ledger.summary()["reserved_attempts"], 0)
        self.assertTrue(self.ledger.summary()["paused"])

    async def test_response_observation_drops_unknown_fields_reasoning_and_headers(self):
        body = response()
        body["private_extra"] = "PRIVATE_NOT_ALLOWED"
        body["choices"][0]["message"]["reasoning_content"] = "PRIVATE_REASONING"
        body["choices"][0]["message"]["private_extra"] = "PRIVATE_NOT_ALLOWED"
        with patch.object(self, "handler", return_value=httpx.Response(200, json=body,
                headers={"x-private-token": "PRIVATE_HEADER"})), self.usage.bind(ROUTE):
            async with self.client() as client:
                await client.post(URL, json=payload())
        observed = json.dumps(self.observed)
        self.assertNotIn("PRIVATE", observed)
        self.assertNotIn(QUESTION, observed)
        self.assertEqual(self.observed[0][1]["native_tool_calls"][0]["function"]["name"], "inspect_trip")

    async def test_response_receipt_failure_is_execution_failure_and_pauses(self):
        def failure(attempt, evidence):
            raise OSError("offline_receipt_unavailable")
        with self.usage.bind(ROUTE), self.assertRaises(OSError):
            async with self.client(observe_response=failure) as client:
                await client.post(URL, json=payload())
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.ledger.summary()["reserved_attempts"], 1)
        self.assertTrue(self.ledger.summary()["paused"])

    async def test_version_change_after_claim_consumes_uncertain_and_no_send(self):
        calls = 0
        def check():
            nonlocal calls
            calls += 1
            if calls == 3:
                raise RuntimeError("fixed_test_error")
        with self.usage.bind(ROUTE), self.assertRaises(RuntimeError):
            async with self.client(verify_execution=check) as client:
                await client.post(URL, json=payload())
        self.assertEqual(self.sent, [])
        self.assertEqual(self.ledger.summary()["counts"]["unknown"], 1)
        self.assertTrue(self.ledger.summary()["paused"])

    async def test_native_feedback_cannot_forge_tool_id(self):
        with self.usage.bind(ROUTE), self.assertRaises(TransportStopped):
            async with self.client() as client:
                await client.post(URL, json=payload())
                invalid = feedback(payload())
                invalid["messages"][-1]["tool_call_id"] = "forged"
                await client.post(URL, json=invalid)
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.ledger.summary()["paused"])

    async def test_http_failure_is_counted_and_pauses_no_refund(self):
        def rejected(request):
            self.sent.append(request.content)
            return httpx.Response(401, json={"error": {"code": "offline_error"}})
        with patch.object(self, "handler", side_effect=rejected), self.usage.bind(ROUTE):
            async with self.client() as client:
                self.assertEqual((await client.post(URL, json=payload())).status_code, 401)
        self.assertEqual(self.ledger.summary()["counts"]["failed"], 1)
        self.assertTrue(self.ledger.summary()["paused"])

    async def test_schema_failure_received_not_semantic_pass_and_can_continue_next_fixed_op(self):
        with patch.object(self, "handler", return_value=httpx.Response(200, json={"bad": "schema"})), self.usage.bind(ROUTE):
            async with self.client() as client:
                await client.post(URL, json=payload())
        self.assertEqual(self.ledger.summary()["counts"]["received"], 1)
        self.assertFalse(self.ledger.summary()["paused"])
        self.ledger.start_operation("m02", BINDING)

    async def test_timeout_and_sent_cancellation_remain_occupied(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                if cancelled:
                    self.path = self.directory / "next" / FILENAME
                    self.path.parent.mkdir(mode=0o700)
                    self.ledger = LiveLedger.create(self.path, binding=BINDINGS)
                async def failure(request):
                    raise asyncio.CancelledError if cancelled else httpx.ReadTimeout("offline timeout")
                with patch.object(self, "handler", side_effect=failure), self.usage.bind(ROUTE):
                    with self.assertRaises(asyncio.CancelledError if cancelled else httpx.ReadTimeout):
                        async with self.client() as client:
                            await client.post(URL, json=payload())
                self.assertEqual(self.ledger.summary()["reserved_attempts"], 1)
                self.assertTrue(self.ledger.summary()["paused"])

    async def test_foreign_url_refused_before_actual_transport(self):
        with self.usage.bind(ROUTE), self.assertRaises(Exception):
            async with self.client() as client:
                await client.post("https://example.com/compatible-mode/v1/chat/completions", json=payload())
        self.assertEqual(self.sent, [])
        self.assertTrue(self.ledger.summary()["paused"])

    async def test_changed_fixed_model_and_bad_settings_refuse_without_transport(self):
        self.settings.qwen_model = "qwen3.7-plus-2026-05-26"
        with self.assertRaises(TransportStopped):
            async with self.client():
                pass
        self.assertIsNone(self.transport_options)
        self.assertTrue(self.ledger.summary()["paused"])


if __name__ == "__main__":
    unittest.main()
