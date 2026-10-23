"""Real local counters and a closed MockTransport; never live model evidence."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import gzip
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

if importlib.util.find_spec("jsonschema") is None:
    raise unittest.SkipTest("Controlled candidate transport requires the explicit echomind extra")

from core.llm_utils import RuntimeFailure
from core.usage_runtime import MeteredAsyncClient
from tests.usage_fixture import UsageFixture
from tools.echomind_travel_trial_ledger import Ledger
from tools.echomind_travel_trial_transport import (
    MAX_OUTPUT, MODEL, PARAMETERS, URL, TransportStopped, controlled_client, digest,
)

QUESTION = "只读取当前需求，不执行修改。"
ROUTE = "/chat/conversations/12345678-1234-4234-8234-123456789abc/assistant-turns"
BINDING = "b" * 64
TOOLS = [{"type": "function", "function": {"name": "inspect_trip", "description": "受控只读",
    "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}}}]


def payload() -> dict:
    return dict(PARAMETERS, messages=[{"role": "system", "content": "受控冻结规则"},
        {"role": "user", "content": QUESTION}], tools=deepcopy(TOOLS))


def native(number: int = 1, *, final: bool = False) -> dict:
    return {"model": MODEL, "id": f"controlled_response_{number}", "choices": [{"index": 0,
        "finish_reason": "stop" if final else "tool_calls", "message": {"role": "assistant",
        "content": "受控非业务自由文本" if final else None, **({} if final else {"tool_calls": [{
            "index": 0, "id": f"controlled_call_{number}", "type": "function", "function": {
                "name": "inspect_trip", "arguments": "{}"}}]})}}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 10}}


def feedback(previous: dict, number: int = 1) -> dict:
    result = deepcopy(previous)
    result["messages"].extend([{"role": "assistant", "content": None, "tool_calls": [{
        "id": f"controlled_call_{number}", "type": "function", "function": {"name": "inspect_trip", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": f"controlled_call_{number}", "content": '{"state":"controlled_read"}'}])
    return result


class Chunks(httpx.AsyncByteStream):
    def __init__(self, *chunks: bytes):
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self):
        pass


class TransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.path = self.directory / "controlled.jsonl"
        self.ledger = Ledger.create(self.path, binding={"plan_protocol_sha256": "a" * 64,
            "execution_sha256": "c" * 64, "prior_ledger_hashes": {"controlled_prior": "d" * 64}},
            mode="controlled", limit=21)
        self.usage = UsageFixture()
        self.addCleanup(self.usage.close)
        self.sent: list[bytes] = []

    def handler(self, request):
        self.sent.append(request.content)
        return httpx.Response(200, json=native(len(self.sent), final=len(self.sent) == 2))

    def client(self, **changes):
        return controlled_client(self.ledger, "m01", BINDING, QUESTION, payload(), self.handler, **changes)

    def used(self):
        return self.usage.store.own_usage("a" * 32)["services"]["qwen_text"]["used"]

    async def test_exact_client_two_native_rounds_share_real_product_and_trial_counts(self):
        with self.usage.bind(ROUTE):
            async with self.client() as client:
                self.assertIs(type(client), MeteredAsyncClient)
                body = payload()
                first = await client.post(URL, json=body)
                self.assertEqual(first.json()["choices"][0]["finish_reason"], "tool_calls")
                await client.post(URL, json=feedback(body))
        state = self.ledger.summary()
        self.assertEqual((state["controlled_attempts"], state["real_calls"]), (2, 0))
        self.assertFalse(state["paused"])
        self.assertEqual(self.used(), 2)
        # All plaintext prompt/body data remains in memory, not the ledger.
        for path in self.directory.rglob("*"):
            if path.is_file():
                raw = path.read_bytes()
                self.assertNotIn(QUESTION.encode(), raw)
                self.assertNotIn(b"controlled-not-real", raw)

    async def test_first_actual_wire_hash_is_not_replaced_by_canonical_hash(self):
        with self.usage.bind(ROUTE):
            async with self.client() as client:
                await client.post(URL, json=payload())
        raw = b"".join(path.read_bytes() for path in self.directory.rglob("*") if path.is_file())
        self.assertIn(digest(self.sent[0]).encode(), raw)

    async def test_third_planned_round_allowed_fourth_denied_and_no_retry(self):
        def multiple(request):
            self.sent.append(request.content)
            return httpx.Response(200, json=native(len(self.sent)))
        with self.usage.bind(ROUTE):
            with self.assertRaises(Exception):
                async with controlled_client(self.ledger, "m01", BINDING, QUESTION, payload(), multiple) as client:
                    body = payload()
                    for number in range(1, 5):
                        await client.post(URL, json=body)
                        body = feedback(body, number)
        self.assertEqual(len(self.sent), 3)
        self.assertEqual(self.ledger.summary()["controlled_attempts"], 3)
        # The hook is after production reservation; the denied fourth consumes
        # a conservative product attempt, but no fourth controlled dispatch.
        self.assertEqual(self.used(), 4)

    async def test_frozen_question_parameters_unknown_tool_and_sequence_refused(self):
        for change in ("question", "tokens", "unknown", "feedback"):
            with self.subTest(change=change):
                root = self.directory / (change + ".jsonl")
                ledger = Ledger.create(root, binding={"plan_protocol_sha256": "a" * 64,
                    "execution_sha256": "c" * 64, "prior_ledger_hashes": {"prior": "d" * 64}}, mode="controlled", limit=21)
                with self.usage.bind(ROUTE):
                    with self.assertRaises(TransportStopped):
                        async with controlled_client(ledger, "m01", BINDING, QUESTION, payload(), self.handler) as client:
                            body = payload()
                            if change == "question":
                                body["messages"][-1]["content"] = "另一问题"
                            elif change == "tokens":
                                body["max_tokens"] = 901
                            elif change == "unknown":
                                body["tools"][0]["function"]["name"] = "arbitrary_url"
                            else:
                                await client.post(URL, json=body)
                                body = feedback(body)
                                body["messages"][-1]["tool_call_id"] = "wrong_call"
                            await client.post(URL, json=body)
                self.assertEqual(ledger.summary()["controlled_attempts"], int(change == "feedback"))
                self.assertTrue(ledger.summary()["paused"])
                with self.assertRaises(Exception):
                    Ledger(root).start_operation("m02", BINDING)
        self.assertEqual(len(self.sent), 1)

    async def test_current_binding_changed_before_send_refused_without_upstream(self):
        with self.usage.bind(ROUTE):
            with self.assertRaises(TransportStopped):
                async with self.client(current_binding=lambda: "e" * 64) as client:
                    await client.post(URL, json=payload())
        self.assertEqual(self.sent, [])
        self.assertEqual(self.ledger.summary()["controlled_attempts"], 0)
        self.assertTrue(self.ledger.summary()["paused"])
        with self.assertRaises(Exception):
            Ledger(self.path).start_operation("m02", BINDING)

    async def test_binding_changes_during_durable_claim_blocks_dispatch_and_stays_occupied(self):
        current = [BINDING]
        original = self.ledger.claim
        def claim(*args, **kwargs):
            result = original(*args, **kwargs)
            current[0] = "e" * 64
            return result
        with self.usage.bind(ROUTE), patch.object(self.ledger, "claim", claim):
            with self.assertRaises(TransportStopped):
                async with self.client(current_binding=lambda: current[0]) as client:
                    await client.post(URL, json=payload())
        self.assertEqual(self.sent, [])
        self.assertEqual(self.ledger.summary()["controlled_attempts"], 1)
        self.assertTrue(self.ledger.summary()["paused"])

    async def test_cancel_before_send_pauses_without_manufacturing_dispatch(self):
        with self.usage.bind(ROUTE):
            with self.assertRaises(asyncio.CancelledError):
                async with self.client():
                    raise asyncio.CancelledError
        self.assertEqual(self.sent, [])
        self.assertEqual(self.used(), 0)
        self.assertEqual(self.ledger.summary()["controlled_attempts"], 0)
        self.assertTrue(self.ledger.summary()["paused"])

    async def test_caught_request_hook_error_still_durably_pauses_zero_send_operation(self):
        with self.usage.bind(ROUTE):
            async with self.client() as client:
                with self.assertRaises(TransportStopped):
                    await client.post(URL, json=payload() | {"max_tokens": 901})
        self.assertEqual(self.sent, [])
        self.assertEqual(self.ledger.summary()["controlled_attempts"], 0)
        self.assertTrue(self.ledger.summary()["paused"])
        with self.assertRaises(Exception):
            Ledger(self.path).start_operation("m02", BINDING)

    async def test_received_business_failure_is_not_repaired_or_execution_pause(self):
        with self.usage.bind(ROUTE):
            with self.assertRaises(RuntimeFailure):
                async with self.client() as client:
                    await client.post(URL, json=payload())
                    raise RuntimeFailure("tool_arguments")
        self.assertFalse(self.ledger.summary()["paused"])
        self.assertEqual(self.ledger.summary()["controlled_attempts"], 1)
        # This is a new independent fixed operation, never retrying m01.
        Ledger(self.path).start_operation("m02", BINDING)

    async def test_claim_failure_never_calls_controlled_transport(self):
        with self.usage.bind(ROUTE):
            with self.assertRaises(OSError):
                async with self.client() as client:
                    with patch.object(self.ledger, "claim", side_effect=OSError("CONTROLLED_PRIVATE_DETAIL")):
                        await client.post(URL, json=payload())
        self.assertEqual(self.sent, [])
        self.assertEqual(self.used(), 1)

    async def test_timeout_before_response_occupies_and_pauses_no_reopen_send(self):
        def timeout(request):
            self.sent.append(request.content)
            raise httpx.ReadTimeout("controlled", request=request)
        with self.usage.bind(ROUTE):
            with self.assertRaises(httpx.ReadTimeout):
                async with controlled_client(self.ledger, "m01", BINDING, QUESTION, payload(), timeout) as client:
                    await client.post(URL, json=payload())
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.ledger.summary()["controlled_attempts"], 1)
        self.assertTrue(self.ledger.summary()["paused"])
        with self.assertRaises(Exception):
            Ledger(self.path).start_operation("m02", BINDING)

    async def test_authentication_http_response_pauses_but_invalid_business_json_does_not(self):
        with self.usage.bind(ROUTE):
            async with controlled_client(self.ledger, "m01", BINDING, QUESTION, payload(),
                    lambda _: httpx.Response(401, json={"error": {"message": "PRIVATE_DETAIL"}})) as client:
                response = await client.post(URL, json=payload())
                self.assertEqual(response.status_code, 401)
        self.assertTrue(self.ledger.summary()["paused"])
        ledger = Ledger.create(self.directory / "invalid-business.jsonl", binding={"plan_protocol_sha256": "a" * 64,
            "execution_sha256": "c" * 64, "prior_ledger_hashes": {"prior": "d" * 64}})
        with self.usage.bind(ROUTE):
            async with controlled_client(ledger, "m01", BINDING, QUESTION, payload(),
                    lambda _: httpx.Response(200, content=b"INVALID_JSON")) as client:
                await client.post(URL, json=payload())
        self.assertFalse(ledger.summary()["paused"])
        self.assertEqual(ledger.summary()["controlled_attempts"], 1)

    async def test_sent_cancel_counted_no_early_capacity_or_duplicate_finish(self):
        entered = asyncio.Event()
        async def wait(request):
            self.sent.append(request.content)
            entered.set()
            await asyncio.Future()
        async def run():
            with self.usage.bind(ROUTE):
                async with controlled_client(self.ledger, "m01", BINDING, QUESTION, payload(), wait) as client:
                    await client.post(URL, json=payload())
        task = asyncio.create_task(run())
        await entered.wait()
        task.cancel()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.ledger.summary()["controlled_attempts"], 1)
        self.assertTrue(self.ledger.summary()["paused"])
        self.assertEqual(self.used(), 1)

    async def test_response_limit_and_unread_stream_pause_admission(self):
        for name, chunks in (("overflow", Chunks(b"x" * (MAX_OUTPUT + 1))), ("unread", Chunks(b"{}"))):
            with self.subTest(name=name):
                ledger = Ledger.create(self.directory / (name + ".jsonl"), binding={"plan_protocol_sha256": "a" * 64,
                    "execution_sha256": "c" * 64, "prior_ledger_hashes": {"prior": "d" * 64}}, mode="controlled", limit=21)
                with self.usage.bind(ROUTE):
                    if name == "overflow":
                        with self.assertRaises(TransportStopped):
                            async with controlled_client(ledger, "m01", BINDING, QUESTION, payload(),
                                    lambda _: httpx.Response(200, stream=chunks)) as client:
                                await client.post(URL, json=payload())
                    else:
                        async with controlled_client(ledger, "m01", BINDING, QUESTION, payload(),
                                lambda _: httpx.Response(200, stream=chunks)) as client:
                            async with client.stream("POST", URL, json=payload()):
                                pass
                self.assertEqual(ledger.summary()["controlled_attempts"], 1)
                self.assertTrue(ledger.summary()["paused"])

    async def test_gzip_body_preserved_and_feedback_bound_to_decoded_response(self):
        def compressed(request):
            self.sent.append(request.content)
            raw = json.dumps(native(len(self.sent))).encode()
            return httpx.Response(200, stream=Chunks(gzip.compress(raw)), headers={"Content-Encoding": "gzip"})
        with self.usage.bind(ROUTE):
            async with controlled_client(self.ledger, "m01", BINDING, QUESTION, payload(), compressed) as client:
                first = await client.post(URL, json=payload())
                self.assertEqual(first.json()["id"], "controlled_response_1")
                await client.post(URL, json=feedback(payload()))
        self.assertEqual(self.ledger.summary()["controlled_attempts"], 2)

    async def test_concurrent_send_cannot_pass_pending_claim(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def wait(request):
            self.sent.append(request.content)
            entered.set()
            await release.wait()
            return httpx.Response(200, json=native())
        with self.usage.bind(ROUTE):
            async with controlled_client(self.ledger, "m01", BINDING, QUESTION, payload(), wait) as client:
                first = asyncio.create_task(client.post(URL, json=payload()))
                await entered.wait()
                with self.assertRaises(TransportStopped):
                    await client.post(URL, json=payload())
                release.set()
                await first
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.ledger.summary()["controlled_attempts"], 1)
        self.assertTrue(self.ledger.summary()["paused"])
        self.assertFalse(self.ledger.summary()["pending"])

    async def test_no_live_mode_url_or_transport_argument_and_real_secret_denied(self):
        for name, value in (("mode", "live"), ("url", "https://other.example"),
                            ("transport", httpx.MockTransport(self.handler))):
            with self.assertRaises(TypeError):
                controlled_client(self.ledger, "m01", BINDING, QUESTION, payload(), self.handler, **{name: value})
        with self.usage.bind(ROUTE):
            with self.assertRaises(TransportStopped):
                async with self.client() as client:
                    await client.post(URL, json=payload(), headers={"Authorization": "Bearer PRIVATE_REAL_VALUE"})
        self.assertEqual(self.sent, [])

    async def test_schema_references_refused_at_construction_without_jsonschema_remote_retrieval(self):
        # jsonschema's default remote retriever imports urllib.request.urlopen.
        # Replace that callback, never actually resolve any test URL.
        with patch("urllib.request.urlopen", side_effect=AssertionError("retrieval forbidden")) as retrieve:
            for keyword, reference in (("$ref", "https://controlled.invalid/schema"),
                                       ("$dynamicRef", "https://controlled.invalid/schema"),
                                       ("$recursiveRef", "https://controlled.invalid/schema"),
                                       ("$ref", "#/$defs/local")):
                with self.subTest(keyword=keyword, reference=reference):
                    ledger = Ledger.create(self.directory / ("reference-" + str(len(list(self.directory.iterdir()))) + ".jsonl"),
                        binding={"plan_protocol_sha256": "a" * 64, "execution_sha256": "c" * 64,
                                 "prior_ledger_hashes": {"prior": "d" * 64}})
                    body = payload()
                    body["tools"][0]["function"]["parameters"]["properties"]["value"] = {keyword: reference}
                    with self.usage.bind(ROUTE), self.assertRaises(TransportStopped):
                        async with controlled_client(ledger, "m01", BINDING, QUESTION, body, self.handler):
                            self.fail("reference-bearing schema must not construct a client")
                    self.assertEqual(ledger.summary()["controlled_attempts"], 0)
                    self.assertTrue(ledger.summary()["paused"])
        retrieve.assert_not_called()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.used(), 0)


if __name__ == "__main__":
    unittest.main()
