"""Offline evaluation-runner checks; no live credentials or model-quality claims."""
import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from app.integrations.qwen import QwenError, configured_endpoint
from app.services.knowledge import fingerprint
from tests.test_knowledge_ab_trial import settings
from tests.test_knowledge_model_selection import response
from tools.knowledge_ab_trial import TrialStopped, pending
from tools.knowledge_plus_gap_candidate import MODEL, payload_for
from tools.knowledge_plus_gap_validation import captured
from tools.knowledge_plus_full_regression import (
    RUBRIC, RegressionLedger, initialize, make_operations, next_operation, summarize_reviews,
)


def fixture() -> dict:
    rubric = json.loads(RUBRIC.read_text())
    ops = make_operations(rubric)
    return {"operations": ops, "code_head": "controlled", "corpus_hash": ops[0]["request"]["corpus_hash"],
            "endpoint_hash": fingerprint(configured_endpoint(settings()))}


class FullRegressionTests(unittest.IsolatedAsyncioTestCase):
    def test_twenty_unique_fixed_payloads_and_predeclared_expectations(self) -> None:
        f = fixture(); ops = f["operations"]
        self.assertEqual(len(ops), 20); self.assertEqual(len({o["operation"] for o in ops}), 20)
        self.assertEqual(sum(o["requires_model"] for o in ops), 18)
        self.assertEqual([o["case"] for o in ops if not o["requires_model"]], ["a08", "a09"])
        for o in ops:
            self.assertEqual(o["request"]["question"], o["original_expectation"]["question"])
            self.assertEqual(o["expectation"]["question"], o["original_expectation"]["question"])
            if not o["requires_model"]:
                self.assertIsNone(o["payload"]); continue
            self.assertEqual(o["payload"], payload_for(captured(o)))
            self.assertEqual(o["payload"]["model"], MODEL)
            self.assertFalse(o["payload"]["stream"]); self.assertFalse(o["payload"]["enable_thinking"])
            self.assertEqual(o["payload"]["max_tokens"], 1024)
            body = json.loads(o["payload"]["messages"][1]["content"])
            self.assertFalse({"required", "forbidden", "status", "expectation", "id"} & body.keys())
            self.assertNotIn(o["request_id"], json.dumps(o["payload"]))
        b03 = next(o for o in ops if o["case"] == "b03")
        self.assertIn("至少", b03["original_expectation"]["required"][0])
        self.assertEqual(b03["expectation"]["required"][1:], b03["original_expectation"]["required"][1:])
        a10 = next(o for o in ops if o["case"] == "a10")
        self.assertEqual(a10["original_expectation"]["status"], ["answered", "partial"])
        self.assertEqual(a10["expectation"]["status"], ["answered"])
        self.assertEqual(a10["expectation"]["missing"], [])

    async def test_full_plan_eighteen_sends_and_two_local_results_restart_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp); f = fixture(); initialize(p, f); sent = []
            def send(req: httpx.Request) -> httpx.Response:
                sent.append(req)
                self.assertEqual(sum(r["event"] == "attempt" for r in RegressionLedger(p, f).rows()), len(sent))
                return response(req)
            for op in f["operations"]:
                initialize(p, f)
                r = await next_operation(p, f, settings(), httpx.MockTransport(send))
                self.assertEqual(r["case"], op["case"])
                if op["requires_model"]: self.assertEqual(r["result"], "structured_output")
                else:
                    self.assertEqual(r["result"], "local_no_match"); self.assertIsNone(r["model_output"])
                    self.assertIsNone(r["attempt"]); self.assertIsNone(r["usage"]["input_tokens"])
                    self.assertEqual(r["rendered"]["status"], "insufficient")
                    self.assertEqual(r["rendered"]["units"], [])
            self.assertEqual(len(sent), 18)
            self.assertEqual((await next_operation(p, f, settings(), httpx.MockTransport(send)))["attempts"], 18)

    async def test_structure_failure_continues_original_output_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp); f = fixture(); initialize(p, f)
            r = await next_operation(p, f, settings(), httpx.MockTransport(lambda req: response(req, {"invented": True})))
            self.assertEqual(r["result"], "quality_failure"); self.assertEqual(r["model_output"], {"invented": True})
            self.assertIsNone(r["rendered"])
            r = await next_operation(p, f, settings(), httpx.MockTransport(lambda req: response(req, {"evidence_ids":["E1"],"missing":["current_applicability"]})))
            self.assertEqual(r["attempt"], 2); self.assertEqual(r["rendered"]["missing"][0]["code"], "current_applicability")

    async def test_execution_failure_pauses_no_automatic_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp); f = fixture(); initialize(p, f)
            async def fail(req: httpx.Request) -> httpx.Response:
                raise httpx.ReadTimeout("private", request=req)
            r = await next_operation(p, f, settings(), httpx.MockTransport(fail))
            self.assertEqual((r["result"], r["attempt"]), ("execution_failure", 1))
            initialize(p, f)
            with self.assertRaises(TrialStopped): await next_operation(p, f, settings(), httpx.MockTransport(lambda _: self.fail("send")))
            self.assertNotIn("private", (p / "calls.jsonl").read_text())

    async def test_version_aba_orphan_and_deleted_ledger_cannot_reset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp); f=fixture(); initialize(p,f); changed=copy.deepcopy(f); changed["code_head"]="changed"
            for value in [changed,f]:
                with self.assertRaises(TrialStopped): await next_operation(p,value,settings(),httpx.MockTransport(lambda _:self.fail("send")))
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp); f=fixture(); initialize(p,f)
            with patch("os.fsync",side_effect=OSError("controlled")), self.assertRaises(OSError):
                await next_operation(p,f,settings(),httpx.MockTransport(lambda _:self.fail("send")))
            with self.assertRaises(TrialStopped): pending(RegressionLedger(p,f))
            (p/"calls.jsonl").unlink()
            with self.assertRaises(TrialStopped): initialize(p,f)

    def test_eighteen_ceiling_duplicate_and_local_reservation_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp); f=fixture(); extra=copy.deepcopy(f["operations"][0]); extra["operation"] += ":extra"
            f["operations"].append(extra); initialize(p,f)
            def reserve(i: int) -> bool:
                op=f["operations"][i]
                try:
                    RegressionLedger(p,f).append({"event":"attempt",**{k:op[k] for k in ["operation","request_id","payload_hash"]}},True)
                    return True
                except (TrialStopped,QwenError): return False
            self.assertFalse(reserve(7)); self.assertFalse(reserve(8))
            with ThreadPoolExecutor(max_workers=3) as pool:
                self.assertEqual(sum(pool.map(reserve,[0]*3)),1)
                self.assertEqual(sum(pool.map(reserve,range(1,21))),17)

    async def test_tampered_local_and_input_binding_stop_before_send(self) -> None:
        for mode in ["binding", "local"]:
            with tempfile.TemporaryDirectory() as tmp:
                p=Path(tmp); f=fixture()
                if mode=="binding": f["operations"][0]["binding_hash"]="wrong"
                else: f["operations"][0]["requires_model"]=False; f["operations"][0]["payload"]=None
                initialize(p,f)
                with self.assertRaises(TrialStopped): await next_operation(p,f,settings(),httpx.MockTransport(lambda _:self.fail("send")))
                self.assertFalse(any(r["event"]=="attempt" for r in RegressionLedger(p,f).rows()))

    def test_business_gate_never_hides_blocking_or_pending_behind_average(self) -> None:
        dimensions=["relevance","coverage","conditions_sources","missing","status","references_structure"]
        good={"case":"x","checks":dict.fromkeys(dimensions,True),"strict":"pass","business":"pass","reason":"controlled"}
        conservative=copy.deepcopy(good); conservative.update(case="c",strict="fail",business="conservative",conservative_conditions_verified=True)
        conservative["checks"].update(missing=False,status=False)
        r=summarize_reviews([good,conservative],2)
        self.assertTrue(r["eligible_for_integration_acceptance"]); self.assertEqual(r["strict"]["fail"],1)
        blocking=copy.deepcopy(conservative); blocking.update(case="b",business="blocking"); blocking["checks"]["coverage"]=False
        self.assertFalse(summarize_reviews([good,blocking],2)["eligible_for_integration_acceptance"])
        unknown=copy.deepcopy(good); unknown.update(case="p",strict="pending",business="pending"); unknown["checks"]["coverage"]=None
        self.assertFalse(summarize_reviews([good,unknown],2)["eligible_for_integration_acceptance"])
        self.assertFalse(summarize_reviews([good],2)["eligible_for_integration_acceptance"])
        for invalid in [dict(blocking,business="conservative"),dict(conservative,conservative_conditions_verified=False),dict(good,reading_burden=True)]:
            with self.assertRaises(ValueError): summarize_reviews([invalid],1)


if __name__ == "__main__":
    unittest.main()
