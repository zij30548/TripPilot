"""Controlled transport only. Never loads real credentials or calls live Qwen."""
import asyncio
import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from app.config import Settings
from app.integrations.qwen import MODEL, QwenError
from app.services.knowledge import DATA
from tools.knowledge_ab_trial import BATCH, TrialLedger, TrialStopped, initialize, next_operation, pending, wire_hash
from tools.knowledge_evidence_trial import prepare


def settings() -> Settings:
    return Settings(_env_file=None, qwen_api_key="controlled-not-a-real-secret", qwen_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1", qwen_model=MODEL)


def fixture(count: int = 2) -> dict:
    row = json.loads((DATA / "answer-fix-evaluation-report.json").read_text())["final_cases"][0]
    packet = prepare({"response": row["response"]})
    ops = []
    for i in range(count):
        arm = "A" if i % 2 == 0 else "B"
        payload = packet["arms"]["baseline" if arm == "A" else "selection"]
        ops.append({"operation": f"{BATCH}:controlled-{i}:{arm}", "case": f"controlled-{i}", "arm": arm,
                    "payload": payload, "payload_hash": wire_hash(payload), "context": row["response"],
                    "requires_model": True, "request_id": f"test-{i}", "prompt_version": arm,
                    "prompt_hash": "controlled", "contract_hash": "controlled", "retrieval_hash": "controlled"})
    return {"operations": ops, "corpus_hash": "controlled", "code_head": "controlled"}


def valid(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    if body["response_format"]["json_schema"]["name"] == "knowledge_answer":
        output = {"answers": [{"text": "已收录资料：300元押金或有效证件，仅限馆内。", "evidence_ids": ["E1"]}], "status": "answered", "missing": []}
    else:
        output = {"subquestions": [{"question_span": "婴儿童车怎么借", "evidence_ids": ["E1"], "missing": []}]}
    return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(output)}}], "usage": {"prompt_tokens": 100, "completion_tokens": 20}})


class TrialTests(unittest.IsolatedAsyncioTestCase):
    async def test_frozen_restart_success_metadata_and_no_duplicate_operation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); frozen = fixture(); initialize(root, frozen)
            seen = []
            def send(request: httpx.Request) -> httpx.Response:
                # Durable reserve already visible before network callback.
                seen.append(request)
                rows = TrialLedger(root, frozen).rows()
                self.assertEqual(sum(r["event"] == "attempt" for r in rows), len(seen))
                return valid(request)
            for arm in ["A", "B"]:
                initialize(root, frozen)  # reopens, never resets
                result = await next_operation(root, frozen, settings(), httpx.MockTransport(send))
                self.assertEqual((result["arm"], result["result"]), (arm, "structured_output"))
            done = await next_operation(root, frozen, settings(), httpx.MockTransport(send))
            self.assertEqual(done["attempts"], 2)
            ledger = TrialLedger(root, frozen)
            with self.assertRaises(TrialStopped):
                ledger.append({"event": "attempt", "operation": frozen["operations"][0]["operation"], "payload_hash": frozen["operations"][0]["payload_hash"]}, reserve=True)
            self.assertEqual(len(seen), 2)
            rows = ledger.rows()
            self.assertEqual([r["arm"] for r in rows if r["event"] == "attempt"], ["A", "B"])
            self.assertNotIn("controlled-not-a-real-secret", ledger.path.read_text())
            self.assertNotIn("Authorization", ledger.path.read_text())

    async def test_bad_structure_recorded_and_following_arm_continues(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); frozen = fixture(); initialize(root, frozen)
            malformed = httpx.MockTransport(lambda _: httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": '{"bad":true}'}}]}))
            result = await next_operation(root, frozen, settings(), malformed)
            self.assertEqual(result["result"], "quality_failure")
            self.assertEqual(result["model_output"], {"bad": True})
            self.assertEqual(result["usage"], {"input_tokens": None, "output_tokens": None})
            result = await next_operation(root, frozen, settings(), httpx.MockTransport(valid))
            self.assertEqual(result["arm"], "B")

    async def test_timeout_cancel_and_auth_pause_without_reset_or_retry(self) -> None:
        for failure in ["timeout", "cancel", "auth"]:
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); frozen = fixture(); initialize(root, frozen)
                async def send(request: httpx.Request) -> httpx.Response:
                    if failure == "timeout": raise httpx.ReadTimeout("controlled", request=request)
                    if failure == "cancel": raise asyncio.CancelledError()
                    return httpx.Response(401, json={"error": {"message": "sensitive must not be recorded"}})
                result = await next_operation(root, frozen, settings(), httpx.MockTransport(send))
                self.assertEqual(result["result"], "execution_failure")
                self.assertEqual(result["attempt"], 1)
                initialize(root, frozen)
                with self.assertRaises(TrialStopped):
                    await next_operation(root, frozen, settings(), httpx.MockTransport(valid))
                self.assertEqual(sum(r["event"] == "attempt" for r in TrialLedger(root, frozen).rows()), 1)
                self.assertNotIn("sensitive", (root / "calls.jsonl").read_text())

    async def test_fsync_failure_prevents_send_and_orphan_attempt_never_repeats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); frozen = fixture(); initialize(root, frozen); sent = []
            with patch("os.fsync", side_effect=OSError("controlled")), self.assertRaises(OSError):
                await next_operation(root, frozen, settings(), httpx.MockTransport(lambda req: sent.append(req)))
            self.assertFalse(sent)
            with self.assertRaises(TrialStopped): pending(TrialLedger(root, frozen))

    def test_concurrent_duplicate_and_global_twenty_ceiling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); frozen = fixture(24); initialize(root, frozen)
            def reserve(i: int) -> bool:
                op = frozen["operations"][i]
                try:
                    TrialLedger(root, frozen).append({"event": "attempt", "operation": op["operation"], "payload_hash": op["payload_hash"]}, reserve=True)
                    return True
                except (TrialStopped, QwenError): return False
            with ThreadPoolExecutor(max_workers=5) as pool:
                self.assertEqual(sum(pool.map(reserve, [0] * 5)), 1)
                self.assertEqual(sum(pool.map(reserve, range(1, 24))), 19)
            self.assertEqual(sum(r["event"] == "attempt" for r in TrialLedger(root, frozen).rows()), 20)

    async def test_manifest_ledger_configuration_mismatch_all_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); frozen = fixture(); initialize(root, frozen)
            wrong = copy.deepcopy(frozen); wrong["code_head"] = "changed"
            with self.assertRaises(TrialStopped): initialize(root, wrong)
            with self.assertRaises(TrialStopped):
                await next_operation(root, wrong, settings(), httpx.MockTransport(valid))
            with self.assertRaises(TrialStopped):
                await next_operation(root, frozen, Settings(_env_file=None, qwen_api_key=""), httpx.MockTransport(valid))
            rows = TrialLedger(root, frozen).rows()
            self.assertEqual(sum(r["event"] == "attempt" for r in rows), 0)
            self.assertEqual(rows[-1]["reason"], "configuration")
            (root / "calls.jsonl").unlink()  # only this test's disposable directory
            with self.assertRaises(TrialStopped): initialize(root, frozen)

    async def test_local_operations_never_send_and_unknown_usage_not_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); frozen = fixture()
            source = json.loads((DATA / "answer-fix-evaluation-report.json").read_text())["final_cases"][7]["response"]
            for op in frozen["operations"]:
                op.update(requires_model=False, payload=None, payload_hash=None, context=source)
            initialize(root, frozen)
            for _ in range(2):
                result = await next_operation(root, frozen, settings(), httpx.MockTransport(lambda _: self.fail("no HTTP")))
                self.assertEqual(result["result"], "local_no_match")
                self.assertIsNone(result["usage"]["input_tokens"])
            self.assertEqual(sum(r["event"] == "attempt" for r in TrialLedger(root, frozen).rows()), 0)


if __name__ == "__main__":
    unittest.main()
