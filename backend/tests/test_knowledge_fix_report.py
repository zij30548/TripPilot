"""Offline consistency of the saved regression report; NOT model re-evaluation."""
import hashlib
import json
from pathlib import Path
import unittest

from core.schemas.knowledge_answer import AnswerResponse
from tooling.travel.knowledge import build_index

ROOT = Path(__file__).resolve().parents[1]


class ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = json.loads((ROOT / "knowledge/answer-fix-evaluation-report.json").read_text())
        cls.spec = json.loads((ROOT / "knowledge/answer-evaluation.json").read_text())

    def test_original_twelve_once_without_changed_questions_or_expectations(self):
        report = self.report
        cases = report["final_cases"]
        self.assertEqual(len(cases), 12)
        self.assertEqual({c["case_id"] for c in cases}, {c["id"] for c in self.spec["cases"]})
        for case in cases:
            spec = next(c for c in self.spec["cases"] if c["id"] == case["case_id"])
            self.assertEqual((case["question"], case["venue_id"]), (spec["question"], spec["venue_id"]))
        self.assertEqual(hashlib.sha256((ROOT / "knowledge/answer-evaluation.json").read_bytes()).hexdigest(), report["spec_sha256"])

    def test_final_one_version_legal_contract_does_not_hide_semantic_failures(self):
        report = self.report; index = build_index()
        for case in report["final_cases"]:
            self.assertEqual(case["prompt_version"], report["final_version"]["prompt_version"])
            self.assertEqual(case["prompt_hash"], report["final_version"]["prompt_hash"])
            self.assertEqual(case["corpus_hash"], index.hash)
            AnswerResponse.model_validate_json(json.dumps(case["response"]))
        results = [c["review"]["result"] for c in report["final_cases"]]
        self.assertEqual({v:results.count(v) for v in set(results)}, {"pass":6, "fail":5, "needs_review":1})
        self.assertEqual(report["status"], "回答质量验收未完成")

    def test_every_attempt_accounted_and_no_match_remains_zero(self):
        report = self.report
        all_cases = report["targeted_and_repair"] + report["final_cases"]
        self.assertEqual(sorted(c["attempt"] for c in all_cases if c["attempt"]), list(range(1,17)))
        self.assertEqual([c["case_id"] for c in report["final_cases"] if c["attempt"] == 0], ["a08", "a09"])
        self.assertEqual(sum(c["http_status"] != 200 for c in all_cases), 1)
        self.assertEqual(sum((c["usage"]["input_tokens"] or 0) for c in all_cases), 19309)
        self.assertEqual(sum((c["usage"]["output_tokens"] or 0) for c in all_cases), 2374)
