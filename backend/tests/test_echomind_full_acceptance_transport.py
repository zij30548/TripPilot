"""Real private trial/product counters, exclusively controlled upstream bytes."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from fastapi import Depends, FastAPI, HTTPException

from core.business_usage import UsageError
from core.config import Settings
from core.llm_utils import RuntimeFailure, strict_json
from core.usage_runtime import MeteredAsyncClient
from echomind_core.embedding import _payload as embedding_payload
from echomind_core.provider import ProviderError, _response
from echomind_core.travel_domain import TravelRole
from echomind_core.travel_package import TravelDomainPackage
from tests.usage_fixture import UsageFixture
from tools.echomind_full_acceptance_ledger import FILENAME, FullAcceptanceLedger
from tools.echomind_full_acceptance_transport import (
    MODEL, FullTransportStopped, ResponseRejected, canonical, digest,
    full_acceptance_client, full_embedding_stage, validate_payload,
)

BINDING = "b" * 64
ROUTE = "/chat/conversations/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/assistant-turns"
URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
EMBED = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
SECRET = "controlled-not-real-never-persist"
QUESTION = "受控冻结问题，不是用户私人行程"


def binding():
    return {"grant_sha256": "a" * 64, "protocol_sha256": "c" * 64,
            "code_freeze_sha256": "d" * 64, "prior_ledger_hashes": {"old_paused": "e" * 64}}


def payload(stage="classifier"):
    package = TravelDomainPackage().capture()
    return {"model": MODEL, "max_tokens": {"classifier": 256, "rewrite": 512,
        "rerank": 2048, "composer": 1000}[stage], "temperature": {"classifier": .1,
        "rewrite": .3, "rerank": .0, "composer": .1}[stage], "stream": False,
        "enable_thinking": False, "messages": [
            {"role": "system", "content": package.stage_prompt(stage)},
            {"role": "user", "content": QUESTION}]}


def agent_payload(role=TravelRole.PLANNING):
    package = TravelDomainPackage().capture()
    return {"model": MODEL, "max_tokens": 1024, "temperature": .1, "stream": False,
        "enable_thinking": False, "messages": [{"role": "system", "content": package.prompt_for(role, QUESTION)},
            {"role": "user", "content": QUESTION}], "tool_choice": "auto", "parallel_tool_calls": False,
        "tools": [{"type": "function", "function": {"name": "inspect_trip", "description": "受控只读",
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}}}]}


def text_response(content=None, **changes):
    result = {"id": "controlled_response", "model": MODEL, "choices": [{"index": 0,
        "finish_reason": "stop", "message": {"role": "assistant", "content": content or
            '{"intent":"schedule","confidence":0.9,"reasoning":"受控分类"}'}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 15, "total_tokens": 27}}
    result.update(changes)
    return result


def native_response():
    result = text_response()
    result["choices"][0] = {"finish_reason": "tool_calls", "message": {"role": "assistant",
        "content": None, "tool_calls": [{"id": "controlled_call", "type": "function",
            "function": {"name": "inspect_trip", "arguments": "{}"}}]}}
    return result


def vectors_response():
    return {"model": "text-embedding-v4", "object": "list", "data": [{"index": 0,
        "object": "embedding", "embedding": [1.] + [0.] * 1023}], "usage": {"total_tokens": 4}}


class Chunks(httpx.AsyncByteStream):
    def __init__(self, parts, *, entered=None):
        self.parts, self.closed, self.entered = parts, False, entered

    async def __aiter__(self):
        for part in self.parts:
            if self.entered:
                self.entered.set()
                await asyncio.Future()
            yield part

    async def aclose(self):
        self.closed = True


class FullTransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.directory.chmod(0o700)
        self.ledger = FullAcceptanceLedger.create(self.directory / FILENAME, binding=binding())
        self.usage = UsageFixture()
        self.addCleanup(self.usage.close)
        self.settings = Settings(_env_file=None, qwen_api_key=SECRET,
                                 qwen_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", qwen_model=MODEL)
        self.sent = []

    def ready(self, target="f01"):
        for op in ("prep01", "prep02", "prep03", "index01"):
            if op == target:
                break
            self.ledger.start_operation(op, BINDING)
            attempt = self.ledger.claim(op, "controlled.preparation", digest(op.encode()), binding_sha256=BINDING)
            self.ledger.finish(attempt, "received")
            self.ledger.seal_operation(op)
        self.ledger.start_operation(target, BINDING)

    def handler(self, request):
        self.sent.append(request.content)
        return httpx.Response(200, json=text_response())

    def client(self, handler=None, **changes):
        return full_acceptance_client(ledger=self.ledger, operation_id=changes.pop("operation_id", "f01"),
            binding_sha256=BINDING, settings=self.settings, verify=lambda: None,
            transport=httpx.MockTransport(handler or self.handler), **changes)

    async def post(self, client, body, url=URL):
        return await client.post(url, json=body, headers={"Authorization": "Bearer " + SECRET})

    def used(self):
        return self.usage.store.own_usage("a" * 32)["services"]["qwen_text"]["used"]

    async def test_exact_metered_client_native_classifier_and_composer_are_distinct(self):
        self.ready()
        def handler(request):
            self.sent.append(request.content)
            body = json.loads(request.content)
            return httpx.Response(200, json=native_response() if "tools" in body else
                text_response("DO_NOT_PERSIST_COMPOSER_BODY") if body["max_tokens"] == 1000 else text_response())
        with self.usage.bind(ROUTE):
            async with self.client(handler) as (client, observed):
                self.assertIs(type(client), MeteredAsyncClient)
                await self.post(client, payload())
                await self.post(client, agent_payload())
                await self.post(client, payload("composer"))
        self.assertEqual(self.used(), 3)
        self.assertEqual([row["stage"] for row in observed.records], ["classifier", "agent:travel_planning", "composer"])
        self.assertEqual(observed.records[0]["response"]["classifier"]["intent"], "schedule")
        self.assertEqual(observed.records[1]["response"]["tool_calls"][0]["name"], "inspect_trip")
        self.assertNotIn("DO_NOT_PERSIST_COMPOSER_BODY", json.dumps(observed.records))
        self.assertEqual(observed.records[0]["payload_sha256"], digest(self.sent[0]))
        raw = (self.directory / FILENAME).read_bytes()
        for forbidden in (QUESTION.encode(), SECRET.encode(), b"DO_NOT_PERSIST_COMPOSER_BODY", b"Authorization"):
            self.assertNotIn(forbidden, raw)
        self.assertFalse(self.ledger.summary()["paused"])

    def test_frozen_generation_profiles_and_embedding_bounds(self):
        for stage in ("classifier", "rewrite", "rerank", "composer"):
            self.assertEqual(validate_payload(payload(stage), embedding=False)[0], stage)
        for role in TravelRole:
            self.assertEqual(validate_payload(agent_payload(role), embedding=False)[0], "agent:" + role.value)
        self.assertEqual(validate_payload(embedding_payload(["受控"], "text-embedding-v4"), embedding=True)[0], "embedding")
        for key, value in (("model", "qwen3.7-plus-2026-05-26"), ("max_tokens", 257),
                           ("temperature", .2), ("stream", True), ("enable_thinking", True)):
            bad = payload(); bad[key] = value
            with self.subTest(key=key), self.assertRaises(FullTransportStopped):
                validate_payload(bad, embedding=False)
        bad = payload(); bad["messages"][0]["content"] = "arbitrary stage"
        with self.assertRaises(FullTransportStopped):
            validate_payload(bad, embedding=False)
        bad = agent_payload(); bad["tools"][0]["function"]["name"] = "download_arbitrary_url"
        with self.assertRaises(FullTransportStopped):
            validate_payload(bad, embedding=False)
        for change in ({"input": ["x"] * 11}, {"dimensions": 512}, {"encoding_format": "base64"}):
            bad = embedding_payload(["x"], "text-embedding-v4") | change
            with self.assertRaises(FullTransportStopped):
                validate_payload(bad, embedding=True)

    async def test_same_embedding_text_distinct_producers_and_retry_guard(self):
        self.ready()
        body = embedding_payload(["受控相同文本"], "text-embedding-v4")
        with self.usage.bind(ROUTE):
            async with self.client(lambda _: httpx.Response(200, json=vectors_response())) as (client, observed):
                for stage in ("memory.embedding", "intent.embedding"):
                    with full_embedding_stage(stage):
                        await self.post(client, body, EMBED)
                with full_embedding_stage("intent.embedding"), self.assertRaises(Exception):
                    await self.post(client, body, EMBED)
        self.assertEqual([row["stage"] for row in observed.records], ["memory.embedding", "intent.embedding"])
        self.assertEqual(len(self.ledger.summary()["attempts"]), 6)  # 4 controlled prefix + 2 actual.
        self.assertTrue(self.ledger.summary()["paused"])
        self.assertEqual(self.used(), 3)  # Denied trial claim conservatively occupies product quota only.
        self.assertNotIn('"vectors"', json.dumps(observed.records))
        self.assertNotIn('"embedding": [', json.dumps(observed.records))

    async def test_missing_embedding_annotation_and_overlarge_input_never_send(self):
        self.ready("prep01")
        with self.usage.bind(ROUTE), self.assertRaises(FullTransportStopped):
            async with self.client(operation_id="prep01") as (client, _):
                await self.post(client, embedding_payload(["x"], "text-embedding-v4"), EMBED)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.ledger.summary()["reserved_attempts"], 0)
        bad = payload(); bad["messages"][1]["content"] = "大" * 20_000
        with self.assertRaises(FullTransportStopped):
            validate_payload(bad, embedding=False)

    async def test_parallel_pending_siblings_finish_out_of_order(self):
        self.ready()
        arrived, release = asyncio.Event(), asyncio.Event()
        async def handler(request):
            self.sent.append(request.content)
            if len(self.sent) == 2:
                arrived.set()
            await release.wait()
            return httpx.Response(200, json=native_response())
        with self.usage.bind(ROUTE):
            async with self.client(handler) as (client, observed):
                tasks = [asyncio.create_task(self.post(client, agent_payload(role)))
                         for role in (TravelRole.PLANNING, TravelRole.BUDGET)]
                await asyncio.wait_for(arrived.wait(), 2)
                self.assertEqual(self.ledger.summary()["counts"]["pending"], 2)
                release.set()
                await asyncio.gather(*tasks)
        self.assertEqual(len(observed.records), 2)
        self.assertEqual(self.used(), 2)
        self.assertFalse(self.ledger.summary()["paused"])

    async def test_freeze_change_during_claim_blocks_physical_send_and_remains_occupied(self):
        self.ready()
        changed = [False]
        original = self.ledger.claim
        def claim(*args, **kwargs):
            result = original(*args, **kwargs)
            changed[0] = True
            return result
        def verify():
            if changed[0]:
                raise FullTransportStopped("full_freeze_changed")
        with self.usage.bind(ROUTE), patch.object(self.ledger, "claim", claim), self.assertRaises(FullTransportStopped):
            async with full_acceptance_client(ledger=self.ledger, operation_id="f01", binding_sha256=BINDING,
                    settings=self.settings, verify=verify, transport=httpx.MockTransport(self.handler)) as (client, _):
                await self.post(client, payload())
        self.assertEqual(self.sent, [])
        self.assertEqual(self.ledger.summary()["counts"]["unknown"], 1)
        self.assertTrue(self.ledger.summary()["paused"])

    async def test_network_error_and_sent_cancel_pause_without_refund(self):
        self.ready()
        async def handler(request):
            self.sent.append(request.content)
            raise httpx.ConnectError("not logged controlled private exception", request=request)
        with self.usage.bind(ROUTE), self.assertRaises(httpx.ConnectError):
            async with self.client(handler) as (client, _):
                await self.post(client, payload())
        self.assertEqual(self.used(), 1)
        self.assertEqual(self.ledger.summary()["counts"]["failed"], 1)
        self.assertTrue(self.ledger.summary()["paused"])
        self.assertNotIn(b"private exception", (self.directory / FILENAME).read_bytes())

    async def test_repeated_cancellation_drains_response_and_leaves_one_attempt(self):
        self.ready()
        entered = asyncio.Event()
        stream = Chunks([b"unfinished"], entered=entered)
        with self.usage.bind(ROUTE):
            async with self.client(lambda _: httpx.Response(200, stream=stream)) as (client, _):
                operation = asyncio.create_task(self.post(client, payload()))
                await entered.wait()
                operation.cancel(); await asyncio.sleep(0); operation.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await operation
        self.assertTrue(stream.closed)
        self.assertEqual(self.ledger.summary()["counts"]["cancelled"], 1)
        self.assertEqual(self.used(), 1)

    async def test_bad_structure_is_received_and_can_finish_failed_without_global_pause(self):
        self.ready()
        with self.usage.bind(ROUTE):
            async with self.client(lambda _: httpx.Response(200, content=b"not JSON")) as (client, observed):
                response = await self.post(client, payload())
        self.assertEqual(response.content, b"not JSON")  # Never repaired into success.
        self.assertEqual(observed.records[0]["response"]["structure"], "invalid")
        self.ledger.seal_operation("f01", business_outcome="failed")
        self.assertFalse(self.ledger.summary()["paused"])

    async def test_oversize_and_compressed_bomb_are_received_structure_failures(self):
        self.ready()
        raw = b"x" * 32_769
        for content, headers in ((raw, {}), (gzip.compress(raw), {"content-encoding": "gzip"})):
            with self.subTest(compressed=bool(headers)), self.usage.bind(ROUTE):
                async with self.client(lambda _: httpx.Response(200, headers=headers, stream=Chunks([content]))) as (client, observed):
                    changed = payload("rewrite" if headers else "classifier")
                    with self.assertRaises(ResponseRejected):
                        await self.post(client, changed)
                    self.assertEqual(observed.records[0]["metadata"]["error_code"], "response_limit")
        self.assertFalse(self.ledger.summary()["paused"])

    async def test_error_status_survives_oversize_or_bad_encoding_and_still_pauses(self):
        cases = [(429, gzip.compress(b"x" * 32_769), {"content-encoding": "gzip"}, "rate_limit"),
            (401, b"x" * 32_769, {}, "authentication"),
            (302, b"x", {"content-encoding": "unsupported"}, "upstream")]
        for number, (status, content, headers, category) in enumerate(cases):
            folder = self.directory / f"status-first-{number}"
            folder.mkdir(mode=0o700)
            self.ledger = FullAcceptanceLedger.create(folder / FILENAME, binding=binding())
            self.ready()
            def handler(_):
                return httpx.Response(status, headers=headers, stream=Chunks([content]))
            with self.subTest(status=status), self.usage.bind(ROUTE), self.assertRaises(ResponseRejected):
                async with self.client(handler) as (client, observed):
                    await self.post(client, payload())
            row = observed.records[0]
            self.assertEqual(row["state"], "failed")
            self.assertEqual(row["metadata"]["status_code"], status)
            self.assertEqual(row["metadata"]["error_code"], category)
            self.assertTrue(self.ledger.summary()["paused"])
            self.assertEqual(self.ledger.summary()["counts"]["failed"], 1)

    async def test_authentication_and_redirect_never_follow_or_continue(self):
        self.ready()
        with self.usage.bind(ROUTE), self.assertRaises(FullTransportStopped):
            async with self.client(lambda _: httpx.Response(401, json={"error": {"code": "InvalidApiKey"}})) as (client, _):
                await self.post(client, payload())
        self.assertEqual(self.ledger.summary()["counts"]["failed"], 1)
        self.assertTrue(self.ledger.summary()["paused"])

    async def test_product_counter_failure_blocks_trial_claim_and_physical_send(self):
        self.ready()
        with self.usage.bind(ROUTE), patch.object(self.usage.store, "reserve", side_effect=UsageError()), \
                self.assertRaises(UsageError):
            async with self.client() as (client, _):
                await self.post(client, payload())
        self.assertEqual(self.sent, [])
        self.assertEqual(self.ledger.summary()["reserved_attempts"], 4)  # Controlled preparation prefix only.
        self.assertTrue(self.ledger.summary()["paused"])

    async def test_total_transport_deadline_pauses_and_does_not_refund(self):
        self.ready()
        async def delayed(_):
            await asyncio.Future()
        with self.usage.bind(ROUTE), patch("tools.echomind_full_acceptance_transport.DEADLINE_SECONDS", .01), \
                self.assertRaises(TimeoutError):
            async with self.client(delayed) as (client, _):
                await self.post(client, payload())
        self.assertEqual(self.used(), 1)
        self.assertEqual(self.ledger.summary()["counts"]["failed"], 1)
        self.assertTrue(self.ledger.summary()["paused"])

    async def test_configured_official_workspace_origin_is_exact_no_query_or_redirect(self):
        self.ready()
        self.settings.qwen_base_url = "https://test-workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/"
        workspace = "https://test-workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions"
        with self.usage.bind(ROUTE):
            async with self.client() as (client, _):
                await self.post(client, payload(), workspace)
                with self.assertRaises(FullTransportStopped):
                    await self.post(client, payload("composer"), URL)
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.ledger.summary()["paused"])

    async def test_preclaim_frozen_change_and_bad_configuration_pause_without_new_send(self):
        self.ready()
        self.settings.qwen_base_url = "https://outside.invalid/compatible-mode/v1"
        with self.usage.bind(ROUTE), self.assertRaises(Exception):
            async with self.client():
                self.fail("invalid endpoint entered client")
        self.assertEqual(self.sent, [])
        self.assertEqual(self.used(), 0)
        self.assertTrue(self.ledger.summary()["paused"])

    async def test_redirect_is_one_failed_physical_attempt_never_followed(self):
        self.ready()
        def redirect(request):
            self.sent.append(request.content)
            return httpx.Response(302, headers={"location": "https://outside.invalid/private"})
        with self.usage.bind(ROUTE), self.assertRaises(FullTransportStopped):
            async with self.client(redirect) as (client, _):
                await self.post(client, payload())
        self.assertEqual((len(self.sent), self.used()), (1, 1))
        self.assertEqual(self.ledger.summary()["counts"]["failed"], 1)
        self.assertTrue(self.ledger.summary()["paused"])

    async def test_provider_quota_error_in_http_200_is_execution_failure_not_semantic(self):
        self.ready()
        with self.usage.bind(ROUTE), self.assertRaises(FullTransportStopped):
            async with self.client(lambda _: httpx.Response(200, json={"error": {"code": "insufficient_quota"}})) as (client, _):
                await self.post(client, payload())
        attempts = self.ledger.summary()["attempts"]
        self.assertEqual(attempts["f01-a01"]["metadata"]["error_code"], "quota")
        self.assertEqual(self.ledger.summary()["counts"]["failed"], 1)
        self.assertTrue(self.ledger.summary()["paused"])

    async def test_unknown_terminal_ack_is_not_rewritten_or_replayed(self):
        self.ready()
        finish = self.ledger.finish
        seen = []
        def uncertain(attempt, state, metadata=None):
            seen.append(state)
            finish(attempt, state, metadata)
            raise OSError("controlled lost acknowledgement")
        with self.usage.bind(ROUTE), patch.object(self.ledger, "finish", uncertain), self.assertRaises(OSError):
            async with self.client() as (client, _):
                await self.post(client, payload())
        self.assertEqual(seen, ["received"])
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.ledger.summary()["paused"])

    async def test_actual_asgi_yield_route_structure_502_can_seal_failed_and_continue(self):
        """Match production route conversion, not merely a direct client catch."""
        self.ready()
        app = FastAPI()
        observed_records = []
        async def dependency():
            with self.usage.bind(ROUTE):
                async with self.client(lambda _: httpx.Response(200, content=b"not JSON")) as (client, observed):
                    try:
                        yield client
                    finally:
                        observed_records.extend(observed.records)

        class FakeAdapter:
            async def run(adapter, client):
                response = await self.post(client, payload())
                try:
                    _response(strict_json(response.content), {}, 256)
                except (ValueError, ProviderError):
                    raise RuntimeFailure("agent_failed") from None

        @app.post("/controlled-turn")
        async def turn(client=Depends(dependency)):
            try:
                return await FakeAdapter().run(client)
            except RuntimeFailure as error:
                raise HTTPException(502, detail={"code": "assistant_" + error.code,
                    "message": "本轮助手未完成，未自动重试；请核对会话与执行记录。"}) from None

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://controlled") as browser:
            response = await browser.post("/controlled-turn")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["detail"]["code"], "assistant_agent_failed")
        self.assertEqual(observed_records[0]["state"], "received")
        self.assertEqual(observed_records[0]["response"]["structure"], "invalid")
        self.assertFalse(self.ledger.summary()["paused"])
        self.ledger.seal_operation("f01", business_outcome="failed")
        self.ledger.start_operation("f02", BINDING)
        self.assertEqual(self.used(), 1)

    async def test_asgi_usage_timeout_version_and_unknown_route_errors_still_pause(self):
        cases = [(429, "business_limit", "limited"), (504, "assistant_timeout", "timeout"),
            (503, "assistant_full_runtime_not_ready", "version"), (502, "assistant_context_changed",
                "本轮助手未完成，未自动重试；请核对会话与执行记录。"),
            (502, "assistant_agent_failed", "unrecognized detail must not become an allowed result")]
        for number, (status, code, message) in enumerate(cases):
            folder = self.directory / f"negative-{number}"
            folder.mkdir(mode=0o700)
            self.ledger = FullAcceptanceLedger.create(folder / FILENAME, binding=binding())
            self.ready()
            app = FastAPI()
            async def dependency():
                with self.usage.bind(ROUTE):
                    async with self.client() as (client, _):
                        yield client
            @app.post("/controlled-turn")
            async def turn(client=Depends(dependency)):
                await self.post(client, payload())
                raise HTTPException(status, detail={"code": code, "message": message})
            with self.subTest(code=code):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://controlled") as browser:
                    response = await browser.post("/controlled-turn")
                self.assertEqual(response.status_code, status)
                self.assertEqual(self.ledger.summary()["counts"]["received"], 5)
                self.assertTrue(self.ledger.summary()["paused"])

    async def test_direct_version_failure_and_empty_business_failure_do_not_bypass_pause(self):
        self.ready()
        with self.usage.bind(ROUTE), self.assertRaises(RuntimeFailure):
            async with self.client() as (client, _):
                await self.post(client, payload())
                raise RuntimeFailure("context_changed")
        self.assertTrue(self.ledger.summary()["paused"])
        folder = self.directory / "empty"
        folder.mkdir(mode=0o700)
        self.ledger = FullAcceptanceLedger.create(folder / FILENAME, binding=binding())
        self.ready()
        with self.usage.bind(ROUTE), self.assertRaises(RuntimeFailure):
            async with self.client():
                raise RuntimeFailure("agent_failed")
        self.assertTrue(self.ledger.summary()["paused"])

    async def test_ordinary_route_failure_with_pending_sibling_still_pauses(self):
        self.ready()
        self.ledger.claim("f01", "controlled.sibling", digest(b"in-flight-unknown"), binding_sha256=BINDING)
        with self.usage.bind(ROUTE), self.assertRaises(HTTPException):
            async with self.client() as (client, _):
                await self.post(client, payload())
                raise HTTPException(502, detail={"code": "assistant_agent_failed",
                    "message": "本轮助手未完成，未自动重试；请核对会话与执行记录。"})
        self.assertEqual(self.ledger.summary()["counts"]["pending"], 1)
        self.assertTrue(self.ledger.summary()["paused"])

    async def test_received_only_fixed_model_rejections_and_budget_failure_are_business_failed(self):
        for number, code in enumerate(("intent_structure", "travel_multiple_tools", "travel_tool_required", "travel_multiple_proposals",
                "travel_action_invalid", "travel_requirements_invalid", "travel_parallel_gated", "task_budget")):
            folder = self.directory / f"business-{number}"
            folder.mkdir(mode=0o700)
            self.ledger = FullAcceptanceLedger.create(folder / FILENAME, binding=binding())
            self.ready()
            with self.subTest(code=code), self.usage.bind(ROUTE), self.assertRaises(HTTPException):
                async with self.client() as (client, _):
                    await self.post(client, payload())
                    raise HTTPException(502, detail={"code": "assistant_" + code,
                        "message": "本轮助手未完成，未自动重试；请核对会话与执行记录。"})
            self.assertFalse(self.ledger.summary()["paused"])
            self.ledger.seal_operation("f01", business_outcome="failed")

    async def test_default_http_transport_disables_environment_proxy_and_retry(self):
        self.ready()
        peer = httpx.MockTransport(self.handler)
        with patch("tools.echomind_full_acceptance_transport.httpx.AsyncHTTPTransport", return_value=peer) as factory:
            async with full_acceptance_client(ledger=self.ledger, operation_id="f01", binding_sha256=BINDING,
                    settings=self.settings, verify=lambda: None):
                pass  # No send; inspecting trusted construction only.
            factory.assert_called_once_with(retries=0, trust_env=False)


if __name__ == "__main__":
    unittest.main()
