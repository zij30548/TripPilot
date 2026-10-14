"""Offline only, independent temp ledgers and MockTransport; no real credentials."""
import asyncio
import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from app.integrations.qwen import ALLOWED_MODELS, QwenError, configured_endpoint, MAX_OUTPUT_BYTES
from app.services.knowledge import DATA, fingerprint
from tests.test_knowledge_ab_trial import settings
from tests.test_knowledge_b_validation import bound_for
from tools.knowledge_ab_trial import TrialStopped, pending, wire_hash
from tools.knowledge_b_candidate import VERSION, SYSTEM, payload_for
from tools.knowledge_model_selection import (
    FLASH, PLUS, MODELS, CASES, SelectionLedger, ObservedTransport,
    captured, experiment_payload, generate, initialize, make_operations, next_operation,
)


def fixture() -> dict:
    cases = json.loads((DATA / "answer-evaluation.json").read_text())["cases"]
    source = []
    for case in cases:
        if case["id"] not in CASES: continue
        bound, request, retrieval = bound_for(case["question"], case["venue_id"], f"prior-{case['id']}")
        payload = payload_for(bound)
        source.append({"case": case["id"], "request": request.model_dump(mode="json"),
                       "retrieval": retrieval.model_dump(mode="json"), "binding_hash": bound.identity,
                       "requires_model": True, "payload": payload, "payload_hash": wire_hash(payload),
                       "prompt_version": VERSION, "prompt_hash": fingerprint(SYSTEM),
                       "contract_hash": fingerprint(payload["response_format"]), "retrieval_hash": fingerprint(retrieval.model_dump(mode="json"))})
    return {"operations": make_operations({"operations": source}), "corpus_hash": retrieval.corpus_hash,
            "code_head": "controlled", "endpoint_hash": fingerprint(configured_endpoint(settings()))}


def response(request: httpx.Request, content: object = None, reported: str | None = None) -> httpx.Response:
    payload = json.loads(request.content)
    body = {"model": reported or payload["model"], "choices": [{"finish_reason": "stop", "message": {
        "role": "assistant", "content": json.dumps(content if content is not None else {"evidence_ids": ["E1"], "missing": []})}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20}}
    return httpx.Response(200, stream=httpx.ByteStream(json.dumps(body).encode()))


class SelectionTests(unittest.IsolatedAsyncioTestCase):
    def test_twenty_unique_plan_payload_equality_reverse_second_round(self) -> None:
        f = fixture(); ops = f["operations"]
        self.assertEqual(len(ops), 20)
        self.assertEqual(len({o["operation"] for o in ops}), 20)
        for i, case in enumerate(CASES):
            a = [o for o in ops if o["case"] == case]
            self.assertEqual([o["repetition"] for o in a], [1, 1, 2, 2])
            order = list(MODELS if i % 2 == 0 else MODELS[::-1])
            self.assertEqual([o["model"] for o in a], order + order[::-1])
            self.assertEqual(len({o["business_payload_hash"] for o in a}), 1)
            for o in a:
                p = o["payload"]
                self.assertFalse(p["enable_thinking"]); self.assertFalse(p["stream"])
                self.assertEqual(p["max_tokens"], 1024)
                self.assertNotIn("temperature", p); self.assertNotIn("top_p", p)
                self.assertNotIn(o["request_id"], json.dumps(p))
        self.assertFalse(any(o["case"] == "b03" for o in ops))

    async def test_only_allowlisted_model_and_exact_payload_production_unchanged(self) -> None:
        bound, _, _ = bound_for(); original = set(ALLOWED_MODELS)
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as http:
            for model in MODELS:
                p = experiment_payload(bound, model)
                self.assertEqual((await generate(bound, p, settings(), http))[0]["missing"], [])
            for model in ["qwen3.7-plus", "qwen3.7-flash", "arbitrary"]:
                with self.assertRaises(TrialStopped): experiment_payload(bound, model)
            p = experiment_payload(bound, PLUS); p["enable_thinking"] = True
            with self.assertRaises(TrialStopped): await generate(bound, p, settings(), http)
            for url in ["http://dashscope.aliyuncs.com/compatible-mode/v1", "https://evil.invalid/compatible-mode/v1"]:
                s = settings().model_copy(update={"qwen_base_url": url})
                with self.assertRaises(QwenError): await generate(bound, experiment_payload(bound, PLUS), s, http)
        with self.assertRaises(QwenError): configured_endpoint(settings().model_copy(update={"qwen_model": PLUS}))
        self.assertEqual(ALLOWED_MODELS, original)
        self.assertNotIn(PLUS, ALLOWED_MODELS)

    async def test_full_plan_success_persistent_precount_metadata_no_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp); f = fixture(); initialize(p, f); sent = []
            def send(request: httpx.Request) -> httpx.Response:
                sent.append(request)
                rows = SelectionLedger(p, f).rows()
                self.assertEqual(sum(r["event"] == "attempt" for r in rows), len(sent))
                return response(request)
            for op in f["operations"]:
                initialize(p, f)
                r = await next_operation(p, f, settings(), httpx.MockTransport(send))
                self.assertEqual(r["reported_model"], op["model"])
                self.assertEqual(r["usage"], {"input_tokens": 100, "output_tokens": 20})
                self.assertEqual(r["repetition"], op["repetition"])
            self.assertEqual((await next_operation(p, f, settings(), httpx.MockTransport(send)))["attempts"], 20)
            self.assertEqual(len(sent), 20)
            op = f["operations"][0]
            with self.assertRaises(TrialStopped): SelectionLedger(p, f).append({"event": "attempt", **{k: op[k] for k in ["operation", "request_id", "payload_hash"]}}, reserve=True)
            self.assertNotIn("controlled-not-a-real-secret", (p / "calls.jsonl").read_text())

    async def test_quality_failure_keeps_usage_and_continues_no_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp); f = fixture(); initialize(p, f)
            r = await next_operation(p, f, settings(), httpx.MockTransport(lambda req: response(req, {"bad": True})))
            self.assertEqual((r["result"], r["attempt"]), ("quality_failure", 1))
            self.assertEqual(r["model_output"], {"bad": True}); self.assertIsNone(r["rendered"])
            self.assertEqual(r["usage"]["output_tokens"], 20)
            self.assertEqual((await next_operation(p, f, settings(), httpx.MockTransport(response)))["attempt"], 2)

    async def test_auth_unsupported_timeout_cancel_rate_pause_not_repeat(self) -> None:
        for fail in [401, 400, 429, "timeout", "cancel"]:
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp); f = fixture(); initialize(p, f)
                async def send(req: httpx.Request) -> httpx.Response:
                    if fail == "timeout": raise httpx.ReadTimeout("private", request=req)
                    if fail == "cancel": raise asyncio.CancelledError()
                    return httpx.Response(fail, stream=httpx.ByteStream(b'{"error":{"message":"private"}}'))
                r = await next_operation(p, f, settings(), httpx.MockTransport(send))
                self.assertEqual((r["result"], r["attempt"]), ("execution_failure", 1))
                initialize(p, f)
                with self.assertRaises(TrialStopped): await next_operation(p, f, settings(), httpx.MockTransport(lambda _: self.fail("send")))
                self.assertNotIn("private", (p / "calls.jsonl").read_text())

    async def test_fsync_or_orphan_cannot_send_or_reset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp); f = fixture(); initialize(p, f)
            with patch("os.fsync", side_effect=OSError("controlled")), self.assertRaises(OSError):
                await next_operation(p, f, settings(), httpx.MockTransport(lambda _: self.fail("send")))
            with self.assertRaises(TrialStopped): pending(SelectionLedger(p, f))
            (p / "calls.jsonl").unlink()
            with self.assertRaises(TrialStopped): initialize(p, f)

    async def test_version_aba_and_wrong_binding_fail_before_send(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp); f = fixture(); initialize(p, f); wrong = copy.deepcopy(f); wrong["code_head"] = "different"
            with self.assertRaises(TrialStopped): await next_operation(p, wrong, settings(), httpx.MockTransport(lambda _: self.fail("send")))
            with self.assertRaises(TrialStopped): await next_operation(p, f, settings(), httpx.MockTransport(lambda _: self.fail("send")))
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp); f = fixture(); f["operations"][0]["binding_hash"] = "invalid"; initialize(p, f)
            with self.assertRaises(TrialStopped): await next_operation(p, f, settings(), httpx.MockTransport(lambda _: self.fail("send")))

    def test_concurrent_unique_ceiling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp); f = fixture()
            extra = copy.deepcopy(f["operations"][0]); extra["operation"] += ":controlled-extra"
            f["operations"].append(extra); initialize(p, f)
            def reserve(i: int) -> bool:
                o = f["operations"][i]
                try:
                    SelectionLedger(p, f).append({"event": "attempt", **{k: o[k] for k in ["operation", "request_id", "payload_hash"]}}, reserve=True)
                    return True
                except (TrialStopped, QwenError): return False
            with ThreadPoolExecutor(max_workers=4) as pool:
                self.assertEqual(sum(pool.map(reserve, [0]*4)), 1)
                self.assertEqual(sum(pool.map(reserve, range(1, 21))), 19)

    async def test_reported_model_alias_unknown_mismatch_and_failure_usage(self) -> None:
        for reported, outcome in [("qwen3.7-flash", "structured_output"), (PLUS, "execution_failure")]:
            with tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp); f = fixture(); initialize(p, f)
                r = await next_operation(p, f, settings(), httpx.MockTransport(lambda req: response(req, reported=reported)))
                self.assertEqual((r["reported_model"], r["result"]), (reported, outcome))
        metadata = {"reported_model": None, "usage": {"input_tokens": None, "output_tokens": None}}
        async with httpx.AsyncClient(transport=ObservedTransport(httpx.MockTransport(lambda _: httpx.Response(
            200, stream=httpx.ByteStream(b'{"model":"untrusted/private","usage":{}}'))), metadata)) as client:
            await client.get("https://example.invalid")
        self.assertIsNone(metadata["reported_model"])
        self.assertIsNone(metadata["usage"]["output_tokens"])

    async def test_existing_capacity_redirect_reasoning_checks_still_apply(self) -> None:
        for mode in ["oversize", "redirect", "reasoning", "truncated"]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp); f = fixture(); initialize(p, f)
                def send(req: httpx.Request) -> httpx.Response:
                    if mode == "oversize": return httpx.Response(200, stream=httpx.ByteStream(b'x'*(MAX_OUTPUT_BYTES+1)))
                    if mode == "redirect": return httpx.Response(302, headers={"Location": "https://evil.invalid"})
                    msg = {"role": "assistant", "content": '{}'}
                    if mode == "reasoning": msg["reasoning_content"] = "hidden"
                    body = {"model": FLASH, "choices": [{"finish_reason": "length" if mode == "truncated" else "stop", "message": msg}],
                            "usage": {"prompt_tokens": 100, "completion_tokens": 1024}}
                    return httpx.Response(200, stream=httpx.ByteStream(json.dumps(body).encode()))
                r = await next_operation(p, f, settings(), httpx.MockTransport(send))
                self.assertEqual(r["attempt"], 1)
                self.assertEqual(r["result"], "execution_failure" if mode == "redirect" else "quality_failure")
                if mode == "truncated": self.assertEqual(r["usage"]["output_tokens"], 1024)


if __name__ == "__main__":
    unittest.main()
