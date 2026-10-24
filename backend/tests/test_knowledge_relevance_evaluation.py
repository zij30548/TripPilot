"""Synthetic offline A/B tests; no scoring/searching the official corpus."""
from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType
from typing import Literal
import unittest
from unittest.mock import patch

from core.schemas.knowledge_national import NationalKnowledgeResponse
from tests.test_knowledge_national import controlled_index, corpus_values, request, reviewed
from tests.test_knowledge_national_evaluation import dataset_values as old_dataset_values
from tools import knowledge_relevance_evaluation as evaluation
from tooling.travel.knowledge_national import NationalKnowledgeIndex

_REAL_SOURCE_PATHS = evaluation._source_paths
_REAL_INPUT_PATHS = evaluation._input_paths


class ControlledResponse(NationalKnowledgeResponse):
    schema_version: Literal["knowledge-relevance-offline-v1"] = "knowledge-relevance-offline-v1"
    method: Literal["topic-gated-lexical-candidate-v1"] = evaluation.CANDIDATE_METHOD
    candidate_version: Literal["knowledge-relevance-v1"] = evaluation.CANDIDATE_VERSION
    query_topics: list[str] = []
    rejected_count: int = 0
    abstention_reason: Literal["no_supported_evidence", "matched_evidence"] = "matched_evidence"


class ControlledCandidate:
    method = evaluation.CANDIDATE_METHOD
    candidate_version = evaluation.CANDIDATE_VERSION

    def __init__(self, base):
        self.base, self.corpus, self.hash = base, base.corpus, base.hash
        self.seen = []

    def search(self, query):
        self.seen.append(query.model_dump(mode="json"))
        result = self.base.search(query).model_dump(mode="json")
        result.update(schema_version="knowledge-relevance-offline-v1", method=self.method,
                      candidate_version=self.candidate_version)
        return ControlledResponse.model_validate_json(json.dumps(result))


class CandidateBaseProjectionTests(unittest.TestCase):
    def candidate_module(self, mutate=None):
        class CloneCandidate:
            method = evaluation.CANDIDATE_METHOD
            candidate_version = evaluation.CANDIDATE_VERSION

            def __init__(self, base):
                self._base = base
                self.corpus, self.hash = base.corpus, base.hash

            @property
            def base(self):
                clone = NationalKnowledgeIndex(self._base.corpus, self._base.manifest)
                if mutate is not None:
                    mutate(clone)
                return clone

        module = ModuleType("tooling.travel.knowledge_relevance")
        module.RelevanceIndex, module.RelevanceResponse = CloneCandidate, ControlledResponse
        return module

    def test_equivalent_independently_owned_baseline_is_allowed_without_search(self):
        base = controlled_index()
        before = evaluation._base_projection(base)
        with patch.dict(sys.modules, {"tooling.travel.knowledge_relevance": self.candidate_module()}), patch.object(
                NationalKnowledgeIndex, "search", side_effect=AssertionError("No retrieval during identity check")):
            candidate, response = evaluation._candidate(base)
        self.assertIsNot(candidate.base, base)
        self.assertEqual(evaluation._base_projection(candidate.base), before)
        self.assertEqual(evaluation._base_projection(base), before)
        self.assertIs(response, ControlledResponse)

    def test_clone_summary_or_source_url_change_rejected_despite_claimed_same_hash(self):
        for mutate in (
            lambda clone: setattr(clone.chunks[0][0].section, "summary", "克隆内的未审核摘要"),
            lambda clone: setattr(clone.chunks[0][0].source, "url", "https://unreviewed.example.test/replacement"),
        ):
            base = controlled_index()
            before = evaluation._base_projection(base)
            with patch.dict(sys.modules, {"tooling.travel.knowledge_relevance": self.candidate_module(mutate)}):
                with self.assertRaisesRegex(evaluation.EvaluationError, "candidate_identity_mismatch"):
                    evaluation._candidate(base)
            self.assertEqual(evaluation._base_projection(base), before)


def dataset_values(index=None):
    old = old_dataset_values(index)
    return {"schema_version": "knowledge-relevance-evaluation-v1", "protocol_version": evaluation.PROTOCOL,
            "corpus_version": old["corpus_version"], "corpus_hash": old["corpus_hash"],
            "thresholds": old["thresholds"], "cases": [case | {"question": case["question"] + "新表述",
                "unanswered": ["受控资料未提供当前余量。"] if case["expectation"] == "limited" else []}
                for case in old["cases"]]}


class RelevanceMetricTests(unittest.TestCase):
    def setUp(self):
        self.base = controlled_index()
        self.dataset = evaluation.EvaluationDataset.model_validate_json(json.dumps(dataset_values(self.base)))
        self.candidate = ControlledCandidate(self.base)

    def test_strict_new_dataset_groups_thresholds_and_real_unanswered_matters(self):
        for mutate in (
            lambda x: x.update(schema_version="knowledge-evaluation-national-v1"),
            lambda x: x.update(protocol_version="unknown"),
            lambda x: x.update(method="extra-field-not-accepted"),
            lambda x: x["thresholds"].update(hit_at_3=.8),
            lambda x: x["thresholds"].update(cross_scope_hits=False),
            lambda x: x["cases"].pop(),
            lambda x: x["cases"][0].update(id=x["cases"][1]["id"]),
            lambda x: x["cases"][0].update(question=x["cases"][1]["question"]),
            lambda x: x["cases"][0].update(split="validation"),
            lambda x: x["cases"][1].update(unanswered=[]),
            lambda x: x["cases"][0].update(unanswered=["未回答"]),
            lambda x: x["cases"][20].update(unanswered=["未回答"]),
            lambda x: x["cases"][1].update(unanswered=[" 缺口 "]),
            lambda x: x["cases"][1].update(unanswered=["缺口", "缺口"]),
            lambda x: x["cases"][20].update(relevant=["sh-entry/entry"]),
            lambda x: x["cases"][0].update(required=["sh-entry/entry", "sh-entry/entry"]),
        ):
            raw = dataset_values(self.base); mutate(raw)
            with self.assertRaises(ValueError):
                evaluation.EvaluationDataset.model_validate_json(json.dumps(raw))

    def test_scope_and_references_checked_without_retrieval(self):
        for mutate in (
            lambda case: case.update(city_id="110000"),
            lambda case: case.update(relevant=["forged/unit"], required=["forged/unit"]),
            lambda case: case.update(relevant=["bj-entry/entry"], required=["bj-entry/entry"]),
        ):
            raw = self.dataset.cases[0].model_dump(mode="json"); mutate(raw)
            case = evaluation.EvaluationCase.model_validate_json(json.dumps(raw))
            with patch.object(self.base, "search", side_effect=AssertionError("Must not search")):
                with self.assertRaises(ValueError):
                    evaluation._validate_cases([case], self.base)

    def test_requests_do_not_contain_labels_rationale_id_or_missing_matters(self):
        rows = evaluation.evaluate_arm(self.candidate, ControlledResponse, self.base, self.dataset.cases[:2])
        self.assertTrue(all(row["hit_at_3"] for row in rows))
        self.assertEqual(set(self.candidate.seen[0]), {"city_id", "venue_id", "question", "visit_date", "limit"})
        self.assertNotIn("unanswered", self.candidate.seen[1])
        self.assertEqual(rows[1]["unanswered"], ["受控资料未提供当前余量。"])

    def test_complete_projection_not_merely_claimed_hash_or_reference(self):
        original = self.base.search
        for field in ("summary", "excerpt", "url", "title", "fetched_at", "published_at"):
            def changed(query):
                result = original(query)
                match = result.matches[0]
                if field == "summary":
                    match.section.summary = "未经审核的替代摘要"
                elif field == "excerpt":
                    match.section.excerpt = "未经审核的摘录"
                elif field == "url":
                    match.source.url = "https://unreviewed.example.test/replaced"
                elif field == "title":
                    match.source.title = "改了来源标题"
                elif field == "fetched_at":
                    match.source.fetched_at = match.source.fetched_at.replace(day=23)
                else:
                    from datetime import date
                    match.source.published_at = date(2026, 1, 1)
                return result
            with patch.object(self.base, "search", side_effect=changed):
                row = evaluation.evaluate_arm(self.base, NationalKnowledgeResponse, self.base, self.dataset.cases[:1])[0]
            self.assertEqual(row["reference_errors"], 1, field)
            self.assertFalse(row["hit_at_3"], field)
            self.assertFalse(row["all_required_at_5"], field)
            self.assertEqual(row["error"], "result_integrity_failed", field)

    def test_strict_roundtrip_rejects_mutation_that_bypassed_assignment_validation(self):
        original = self.base.search
        for update in ({"method": "pretend-model"}, {"status": "no_match"}, {"schema_version": "forged"}):
            with patch.object(self.base, "search", side_effect=lambda query: original(query).model_copy(update=update)):
                row = evaluation.evaluate_arm(self.base, NationalKnowledgeResponse, self.base, self.dataset.cases[:1])[0]
            self.assertEqual(row["error"], "invalid_result")
            self.assertFalse(row["hit_at_3"])

    def test_missing_result_exception_remains_failure_and_not_successful_abstention(self):
        cases = [self.dataset.cases[0], self.dataset.cases[20]]
        with patch.object(self.base, "search", side_effect=RuntimeError("private text must not escape")):
            rows = evaluation.evaluate_arm(self.base, NationalKnowledgeResponse, self.base, cases)
        summary = evaluation.summarize(rows, self.dataset.thresholds)
        self.assertEqual(summary["execution_errors"], 2)
        self.assertEqual(summary["hit_at_3"]["denominator"], 1)
        self.assertEqual(summary["no_material_false_hits"]["denominator"], 1)
        self.assertFalse(summary["gate_passed"])
        self.assertNotIn("private text", json.dumps(rows))

    def test_relevant_coverage_does_not_erase_noise_or_reading_burden(self):
        raw = self.dataset.cases[0].model_dump(mode="json")
        raw["question"] = "2025儿童预约入场"
        case = evaluation.EvaluationCase.model_validate_json(json.dumps(raw))
        row = evaluation.evaluate_arm(self.base, NationalKnowledgeResponse, self.base, [case])[0]
        summary = evaluation.summarize([row, evaluation.evaluate_arm(self.base, NationalKnowledgeResponse, self.base, [self.dataset.cases[20]])[0]], self.dataset.thresholds)
        self.assertTrue(row["hit_at_3"])
        self.assertTrue(row["all_required_at_5"])
        self.assertEqual((row["relevant_units"], row["returned_units"], row["noise_units"]), (1, 2, 1))
        self.assertEqual(summary["unit_precision"], {"numerator": 1, "denominator": 2, "value": .5})
        self.assertEqual(summary["unit_noise_rate"]["numerator"], 1)
        self.assertGreater(summary["reading_characters"], 0)

    def test_independent_declared_metadata_decision_table(self):
        from datetime import date
        standing, history, interval = self.base.corpus.documents[:3]
        self.assertEqual(evaluation._applicability_status(history, None), "historical_reference")
        self.assertEqual(evaluation._applicability_status(history, date(2025, 6, 1)), "historical_reference")
        self.assertEqual(evaluation._applicability_status(standing, None), "no_visit_date")
        self.assertEqual(evaluation._applicability_status(standing, date(2026, 10, 24)), "current_applicability_unknown")
        self.assertEqual(evaluation._applicability_status(interval, date(2026, 1, 1)), "within_declared_range_unverified")
        self.assertEqual(evaluation._applicability_status(interval, date(2026, 12, 31)), "within_declared_range_unverified")
        self.assertEqual(evaluation._applicability_status(interval, date(2027, 1, 1)), "outside_declared_range")

    def test_both_arms_keep_shared_baseline_owned_projections_unchanged(self):
        before = evaluation._base_projection(self.base)
        evaluation.evaluate_arm(self.base, NationalKnowledgeResponse, self.base, self.dataset.cases[:2])
        evaluation.evaluate_arm(self.candidate, ControlledResponse, self.base, self.dataset.cases[:2])
        self.assertEqual(evaluation._base_projection(self.base), before)
        self.assertEqual(self.candidate.base, self.base)


class RelevanceFreezeTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="relevance-eval-synthetic-"))).resolve()
        self.folder.chmod(0o700)
        self.corpus = self.folder / "corpus.json"
        self.manifest = self.folder / "manifest.json"
        self.dataset = self.folder / "fresh.json"
        self.known = self.folder / "known.json"
        self.marker = self.folder / "candidate.py"
        self.inputs, self.receipt, self.report = (self.folder / name for name in ("inputs.json", "freeze.json", "report.json"))
        corpus, manifest = reviewed(corpus_values())
        self.corpus.write_text(corpus.model_dump_json(), encoding="utf-8")
        self.manifest.write_text(manifest.model_dump_json(), encoding="utf-8")
        self.dataset.write_text(json.dumps(dataset_values()), encoding="utf-8")
        self.known.write_text(json.dumps(old_dataset_values()), encoding="utf-8")
        self.marker.write_text("# synthetic candidate identity\n", encoding="utf-8")
        for name, path in (("CORPUS_PATH", self.corpus), ("MANIFEST_PATH", self.manifest),
                           ("DATASET_PATH", self.dataset), ("KNOWN_DATASET_PATH", self.known)):
            self.enterContext(patch.object(evaluation, name, path))
        self.input_paths = {"new": self.dataset, "old": self.known, "corpus": self.corpus, "manifest": self.manifest}
        self.source_paths = self.input_paths | {"candidate": self.marker, "tool": Path(evaluation.__file__).resolve()}
        self.enterContext(patch.object(evaluation, "_input_paths", return_value=self.input_paths))
        self.enterContext(patch.object(evaluation, "_source_paths", return_value=self.source_paths))
        self.enterContext(patch.object(evaluation, "_candidate", side_effect=lambda base: (ControlledCandidate(base), ControlledResponse)))

    def prepare(self):
        evaluation.freeze_inputs(self.inputs)
        return evaluation.freeze(self.inputs, self.receipt)

    def test_input_freeze_precedes_candidate_and_is_private_exclusive_without_search(self):
        self.marker.unlink()
        with patch.object(evaluation, "_candidate", side_effect=AssertionError("No candidate import")), patch(
                "tooling.travel.knowledge_national.NationalKnowledgeIndex.search", side_effect=AssertionError("No search")):
            frozen = evaluation.freeze_inputs(self.inputs)
            checked, *_ = evaluation.verify_inputs(self.inputs)
        self.assertEqual(frozen, checked)
        self.assertEqual(self.inputs.stat().st_mode & 0o777, 0o600)
        original = self.inputs.read_bytes()
        with self.assertRaises(ValueError):
            evaluation.freeze_inputs(self.inputs)
        self.assertEqual(self.inputs.read_bytes(), original)

    def test_fresh_or_old_input_corpus_manifest_drift_blocks_before_candidate_search(self):
        evaluation.freeze_inputs(self.inputs)
        for target in (self.dataset, self.known, self.corpus, self.manifest):
            original = target.read_bytes(); target.write_bytes(original + b"\n")
            try:
                with patch.object(evaluation, "_candidate", side_effect=AssertionError("Drift before candidate")):
                    with self.assertRaises(ValueError):
                        evaluation.freeze(self.inputs, self.receipt)
                self.assertFalse(self.receipt.exists())
            finally:
                target.write_bytes(original)

    def test_known_exact_question_cannot_be_renamed_as_fresh(self):
        raw = json.loads(self.dataset.read_bytes())
        raw["cases"][0]["question"] = old_dataset_values()["cases"][0]["question"]
        self.dataset.write_text(json.dumps(raw), encoding="utf-8")
        with patch.object(evaluation, "_candidate", side_effect=AssertionError("Must not import")):
            with self.assertRaisesRegex(evaluation.EvaluationError, "known_question_reused_as_fresh"):
                evaluation.freeze_inputs(self.inputs)
        self.assertFalse(self.inputs.exists())

    def test_code_freeze_binds_inputs_receipt_and_candidate_without_search(self):
        with patch("tooling.travel.knowledge_national.NationalKnowledgeIndex.search", side_effect=AssertionError("No search")):
            frozen = self.prepare()
            checked, *_ = evaluation.verify(self.receipt)
        self.assertEqual(frozen, checked)
        self.assertEqual(frozen["inputs_receipt_path"], str(self.inputs))
        self.assertEqual(self.receipt.stat().st_mode & 0o777, 0o600)
        original = self.receipt.read_bytes()
        with self.assertRaises(ValueError):
            evaluation.freeze(self.inputs, self.receipt)
        self.assertEqual(self.receipt.read_bytes(), original)

    def test_candidate_drift_blocks_run_without_output_or_search(self):
        self.prepare()
        self.marker.write_bytes(self.marker.read_bytes() + b"# changed\n")
        with patch.object(ControlledCandidate, "search", side_effect=AssertionError("Stop before search")):
            with self.assertRaises(ValueError):
                evaluation.run(self.receipt, self.report, "development")
        self.assertFalse(self.report.exists())

    def test_candidate_cannot_mutate_shared_baseline_during_construction(self):
        evaluation.freeze_inputs(self.inputs)
        def mutate(base):
            candidate = ControlledCandidate(base)
            base.chunks[0][0].section.summary = "候选改写了共同基线"
            return candidate, ControlledResponse
        with patch.object(evaluation, "_candidate", side_effect=mutate):
            with self.assertRaisesRegex(evaluation.EvaluationError, "source_changed_during_freeze"):
                evaluation.freeze(self.inputs, self.receipt)
        self.assertFalse(self.receipt.exists())

    def test_same_inputs_both_arms_split_and_known_diagnostics_are_separate(self):
        self.prepare()
        with patch("socket.socket.connect", side_effect=AssertionError("No network")), patch("os.getenv", side_effect=AssertionError("No config")):
            report = evaluation.run(self.receipt, self.report, "development")
        self.assertEqual((report["model_calls"], report["external_calls"]), (0, 0))
        self.assertEqual(len(report["arms"]["A"]), 30)
        self.assertEqual(len(report["arms"]["B"]), 30)
        self.assertEqual(report["summary"]["A"]["hit_at_3"]["denominator"], 20)
        self.assertEqual(report["summary"]["B"]["no_material_false_hits"]["denominator"], 10)
        self.assertEqual([row["result"]["request"] for row in report["arms"]["A"]], [row["result"]["request"] for row in report["arms"]["B"]])
        self.assertEqual(self.report.stat().st_mode & 0o777, 0o600)
        before = self.report.read_bytes()
        with self.assertRaises(ValueError):
            evaluation.run(self.receipt, self.report, "development")
        self.assertEqual(self.report.read_bytes(), before)
        diagnostic = evaluation.run(self.receipt, self.folder / "known-report.json", "known_diagnostic")
        self.assertEqual(len(diagnostic["arms"]["A"]), 60)
        self.assertEqual(diagnostic["annotation_scope"], "known_diagnostic_not_held_out_or_new_acceptance")
        self.assertNotIn("unanswered", diagnostic["arms"]["A"][1])

    def test_private_receipt_validation_links_and_invalid_outputs(self):
        self.prepare()
        original = self.receipt.read_bytes()
        for mutate in (lambda x: x.update(model_calls=False), lambda x: x.update(external_calls=1),
                       lambda x: x.update(extra="forbidden"), lambda x: x.update(candidate_version="changed"),
                       lambda x: x.update(created_at="not-a-time"), lambda x: x["files"]["candidate"].update(bytes=True)):
            raw = json.loads(original); mutate(raw)
            self.receipt.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(ValueError):
                evaluation.verify(self.receipt)
        self.receipt.write_bytes(original)
        self.receipt.chmod(0o644)
        with self.assertRaises(ValueError):
            evaluation.verify(self.receipt)
        self.receipt.chmod(0o600)
        link = self.folder / "receipt-link.json"; link.symlink_to(self.receipt)
        with self.assertRaises(ValueError):
            evaluation.verify(link)
        hardlink = self.folder / "receipt-hardlink.json"; os.link(self.receipt, hardlink)
        with self.assertRaises(ValueError):
            evaluation.verify(self.receipt)
        with self.assertRaises(ValueError):
            evaluation.freeze_inputs(Path("relative.json"))
        with self.assertRaises(ValueError):
            evaluation.freeze_inputs(evaluation.ROOT / "forbidden.json")
        self.assertFalse((evaluation.ROOT / "forbidden.json").exists())

    def test_cli_safe_output_no_source_bodies_or_automatic_repeated_runs(self):
        log = io.StringIO()
        with redirect_stdout(log):
            self.assertEqual(evaluation.main(["freeze-inputs", "--receipt", str(self.inputs)]), 0)
            self.assertEqual(evaluation.main(["freeze", "--inputs", str(self.inputs), "--receipt", str(self.receipt)]), 0)
            self.assertEqual(evaluation.main(["verify", "--receipt", str(self.receipt)]), 0)
            self.assertEqual(evaluation.main(["run", "--receipt", str(self.receipt), "--split", "validation", "--output", str(self.report)]), 0)
        lines = log.getvalue().splitlines()
        self.assertEqual(len(lines), 4)
        self.assertNotIn(str(self.folder), log.getvalue())
        self.assertNotIn("受控", log.getvalue())
        self.assertTrue(all(json.loads(line)["external_calls"] == 0 for line in lines))

    def test_whitelist_binds_original_fourteen_and_candidate_tool_two_tests(self):
        with patch.object(evaluation, "_input_paths", _REAL_INPUT_PATHS):
            paths = _REAL_SOURCE_PATHS()
        self.assertEqual(len(paths), 19)
        for name in ("backend/evaluations/knowledge-national-v1.json",
                     "backend/evaluations/knowledge-national-relevance-v1.json",
                     "backend/tooling/travel/knowledge_relevance.py",
                     "backend/tests/test_knowledge_relevance.py",
                     "backend/tests/test_knowledge_relevance_evaluation.py"):
            self.assertIn(name, paths)


if __name__ == "__main__":
    unittest.main()
