"""Synthetic evaluation only. Never searches or grades the real corpus."""
from __future__ import annotations

from contextlib import redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core.schemas.knowledge_national import NationalKnowledgeResponse
from tests.test_knowledge_national import controlled_index, corpus_values, request, reviewed
from tools import knowledge_national_evaluation as evaluation

_REAL_SOURCE_PATHS = evaluation._source_paths


def dataset_values(index=None) -> dict:
    index = index or controlled_index()
    cases = []
    for split in ("development", "validation"):
        for number in range(30):
            absent = number >= 20
            cases.append({"id": f"{split}-{number:02d}", "split": split, "city_id": "310000",
                "venue_id": "shanghai-museum", "question": f"{split}星际量子传送{number}" if absent else f"{split}儿童免票预约{number}",
                "visit_date": None, "relevant": [] if absent else ["sh-entry/entry"],
                "required": [] if absent else ["sh-entry/entry"],
                "expectation": "no_material" if absent else "limited" if number % 2 else "evidence",
                "rationale": "受控合成题与合成资料，不是真实官方评测。"})
    return {"schema_version": "knowledge-evaluation-national-v1", "corpus_version": index.corpus.corpus_version,
        "corpus_hash": index.hash, "protocol_version": evaluation.PROTOCOL, "method": index.method,
        "thresholds": {"hit_at_3": .9, "required_at_5": .9, "no_material_false_hit_rate": .1, "cross_scope_hits": 0},
        "cases": cases}


def dataset(index=None):
    return evaluation.EvaluationDataset.model_validate_json(json.dumps(dataset_values(index)))


class NationalEvaluationMetricTests(unittest.TestCase):
    def setUp(self):
        self.index = controlled_index()
        self.dataset = dataset(self.index)

    def test_fixed_group_counts_versions_thresholds_and_duplicate_questions(self):
        for mutate in (
            lambda x: x.update(protocol_version="modified"),
            lambda x: x.update(method="pretend-hybrid"),
            lambda x: x["thresholds"].update(hit_at_3=.8),
            lambda x: x["thresholds"].update(cross_scope_hits=False),
            lambda x: x["cases"].pop(),
            lambda x: x["cases"][0].update(split="validation"),
            lambda x: x["cases"][0].update(id=x["cases"][1]["id"]),
            lambda x: x["cases"][0].update(question=x["cases"][1]["question"]),
            lambda x: x["cases"][0].update(required=[]),
            lambda x: x["cases"][20].update(relevant=["sh-entry/entry"]),
            lambda x: x["cases"][0].update(required=["sh-entry/entry", "sh-entry/entry"]),
            lambda x: x["cases"][0].update(unknown="forbidden"),
        ):
            raw = dataset_values(self.index); mutate(raw)
            with self.assertRaises(ValueError):
                evaluation.EvaluationDataset.model_validate_json(json.dumps(raw))

    def test_references_scope_corpus_and_method_are_validated_before_any_search(self):
        for mutate in (
            lambda x: x.update(corpus_hash="f" * 64),
            lambda x: x.update(corpus_version="changed-v1"),
            lambda x: x["cases"][0].update(city_id="110000"),
            lambda x: x["cases"][0].update(relevant=["forged/unit"], required=["forged/unit"]),
            lambda x: x["cases"][0].update(relevant=["bj-entry/entry"], required=["bj-entry/entry"]),
        ):
            raw = dataset_values(self.index); mutate(raw)
            candidate = evaluation.EvaluationDataset.model_validate_json(json.dumps(raw))
            with patch.object(self.index, "search", side_effect=AssertionError("No search during annotation validation")):
                with self.assertRaises(ValueError):
                    evaluation.validate_dataset(candidate, self.index)

    def test_real_synthetic_queries_metrics_have_explicit_numerators_and_denominators(self):
        rows = evaluation.evaluate_cases(self.index, self.dataset.cases)
        result = evaluation.summarize(rows, self.dataset.thresholds)
        self.assertEqual(result["hit_at_3"], {"numerator": 40, "denominator": 40, "value": 1.0})
        self.assertEqual(result["required_at_5"]["numerator"], 40)
        self.assertEqual(result["no_material_false_hits"], {"numerator": 0, "denominator": 20, "value": 0.0})
        self.assertEqual(result["mrr"]["denominator"], 40)
        self.assertTrue(result["gate_passed"])
        self.assertEqual(len(rows), 60)
        self.assertEqual(rows[0]["applicability"][0]["status"], "no_visit_date")

    def test_failure_remains_in_denominator_and_no_material_failure_does_not_pass_gate(self):
        cases = [self.dataset.cases[0], self.dataset.cases[20]]
        with patch.object(self.index, "search", side_effect=ValueError("private source text")):
            rows = evaluation.evaluate_cases(self.index, cases)
        result = evaluation.summarize(rows, self.dataset.thresholds)
        self.assertEqual(result["hit_at_3"], {"numerator": 0, "denominator": 1, "value": 0.0})
        self.assertEqual(result["no_material_false_hits"]["denominator"], 1)
        self.assertEqual(result["execution_errors"], 2)
        self.assertEqual(result["no_material_execution_errors"], 1)
        self.assertFalse(result["gate_passed"])
        self.assertEqual(rows[0]["error"], "retrieval_error")
        self.assertNotIn("private", json.dumps(rows))

    def test_relevant_rank_required_coverage_false_hits_and_scope_count_are_separate(self):
        positive, absent = self.dataset.cases[0], self.dataset.cases[20]
        natural_search = self.index.search
        def changed(query):
            result = natural_search(query)
            if query.question == absent.question:
                extra = natural_search(request())
                return extra.model_copy(update={"request": query})
            forged = result.model_copy(deep=True)
            forged.matches[0].source.venue_id = "beijing-museum"
            return forged
        with patch.object(self.index, "search", side_effect=changed):
            rows = evaluation.evaluate_cases(self.index, [positive, absent])
        self.assertEqual(rows[0]["first_relevant_rank"], 1)
        self.assertTrue(rows[0]["hit_at_3"])
        self.assertTrue(rows[0]["all_required_at_5"])
        self.assertEqual(rows[0]["cross_scope_hits"], 1)
        self.assertEqual(rows[0]["reference_errors"], 1)
        self.assertTrue(rows[1]["no_material_false_hit"])
        result = evaluation.summarize(rows, self.dataset.thresholds)
        self.assertFalse(result["gate_passed"])
        self.assertEqual(result["no_material_false_hits"]["numerator"], 1)

    def test_mrr_and_required_at_five_not_just_first_relevant(self):
        cases = [self.dataset.cases[0], self.dataset.cases[1], self.dataset.cases[20]]
        rows = evaluation.evaluate_cases(self.index, cases)
        rows[0].update(first_relevant_rank=2, all_required_at_5=False)
        rows[1].update(first_relevant_rank=None, hit_at_3=False, all_required_at_5=False)
        result = evaluation.summarize(rows, self.dataset.thresholds)
        self.assertEqual(result["mrr"], {"reciprocal_rank_sum": .5, "denominator": 2, "value": .25})
        self.assertEqual(result["required_at_5"]["numerator"], 0)
        self.assertEqual(result["hit_at_3"]["numerator"], 1)
        self.assertFalse(result["gate_passed"])


class NationalEvaluationFreezeTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory(prefix="national-eval-synthetic-"))
        self.folder = Path(directory).resolve()
        self.corpus_path, self.manifest_path = self.folder / "corpus.json", self.folder / "manifest.json"
        self.dataset_path, self.marker = self.folder / "dataset.json", self.folder / "engine-marker.py"
        self.receipt, self.report = self.folder / "freeze.json", self.folder / "report.json"
        corpus, manifest = reviewed(corpus_values())
        self.corpus_path.write_text(corpus.model_dump_json(), encoding="utf-8")
        self.manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")
        self.dataset_path.write_text(json.dumps(dataset_values()), encoding="utf-8")
        self.marker.write_text("# controlled code identity\n", encoding="utf-8")
        self.enterContext(patch.object(evaluation, "DATASET_PATH", self.dataset_path))
        self.enterContext(patch.object(evaluation, "CORPUS_PATH", self.corpus_path))
        self.enterContext(patch.object(evaluation, "MANIFEST_PATH", self.manifest_path))
        self.enterContext(patch.object(evaluation, "_source_paths", return_value={
            "backend/evaluations/knowledge-national-v1.json": self.dataset_path,
            "backend/knowledge/national-corpus-v2.json": self.corpus_path,
            "backend/knowledge/national-sources-v2.json": self.manifest_path,
            "backend/tooling/travel/knowledge_national.py": self.marker,
            "backend/tools/knowledge_national_evaluation.py": Path(evaluation.__file__).resolve(),
        }))

    def test_freeze_verify_never_search_and_receipt_is_exclusive_private(self):
        with patch("tooling.travel.knowledge_national.NationalKnowledgeIndex.search", side_effect=AssertionError("Freeze is not a retrieval run")):
            frozen = evaluation.freeze(self.receipt)
            checked, loaded, _ = evaluation.verify(self.receipt)
        self.assertEqual(frozen, checked)
        self.assertEqual(len(loaded.cases), 60)
        self.assertEqual(self.receipt.stat().st_mode & 0o777, 0o600)
        saved = self.receipt.read_bytes()
        with self.assertRaises(evaluation.EvaluationError):
            evaluation.freeze(self.receipt)
        self.assertEqual(self.receipt.read_bytes(), saved)

    def test_source_dataset_or_tool_hash_change_blocks_run_without_overwriting_or_search(self):
        evaluation.freeze(self.receipt)
        for target in (self.marker, self.dataset_path, self.corpus_path, self.manifest_path):
            original = target.read_bytes()
            target.write_bytes(original + b"\n")
            try:
                with patch("tooling.travel.knowledge_national.NationalKnowledgeIndex.search", side_effect=AssertionError("Frozen mismatch must stop first")):
                    with self.assertRaises(ValueError):
                        evaluation.run(self.receipt, self.report)
                self.assertFalse(self.report.exists())
            finally:
                target.write_bytes(original)
        evaluation.verify(self.receipt)

    def test_run_requires_freeze_and_writes_all_rows_once_with_no_external_or_database_calls(self):
        with self.assertRaises(ValueError):
            evaluation.run(self.receipt, self.report)
        with patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")), patch("os.getenv", side_effect=AssertionError("No environment access")):
            evaluation.freeze(self.receipt)
            report = evaluation.run(self.receipt, self.report)
        self.assertEqual((report["model_calls"], report["external_calls"]), (0, 0))
        self.assertEqual(report["scope"], "reference_retrieval_only_not_answer_accuracy")
        self.assertEqual(len(report["cases"]), 60)
        self.assertEqual(report["splits"]["development"]["hit_at_3"]["denominator"], 20)
        self.assertEqual(report["splits"]["validation"]["no_material_false_hits"]["denominator"], 10)
        self.assertEqual(self.report.stat().st_mode & 0o777, 0o600)
        saved = self.report.read_bytes()
        with self.assertRaises(evaluation.EvaluationError):
            evaluation.run(self.receipt, self.report)
        self.assertEqual(self.report.read_bytes(), saved)

    def test_private_receipt_tampering_bool_counts_links_and_output_location_rejected(self):
        evaluation.freeze(self.receipt)
        original = self.receipt.read_bytes()
        for modify in (lambda x: x.update(model_calls=False), lambda x: x.update(external_calls=1),
                       lambda x: x.update(extra="unknown"),
                       lambda x: x["files"]["backend/tooling/travel/knowledge_national.py"].update(bytes=float(len(self.marker.read_bytes())))):
            changed = json.loads(original); modify(changed)
            self.receipt.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaises(ValueError):
                evaluation.verify(self.receipt)
        self.receipt.write_bytes(original)
        self.receipt.chmod(0o644)
        with self.assertRaises(ValueError):
            evaluation.verify(self.receipt)
        self.receipt.chmod(0o600)
        link = self.folder / "link.json"; link.symlink_to(self.receipt)
        with self.assertRaises(ValueError):
            evaluation.verify(link)
        with self.assertRaises(ValueError):
            evaluation.freeze(Path("relative.json"))
        with self.assertRaises(ValueError):
            evaluation.freeze(evaluation.ROOT / "forbidden-evaluation-test-receipt.json")
        self.assertFalse((evaluation.ROOT / "forbidden-evaluation-test-receipt.json").exists())

    def test_fifo_and_hardlinked_receipts_refused_without_blocking_or_mutating_content(self):
        fifo = self.folder / "not-a-regular-receipt"
        os.mkfifo(fifo, 0o600)
        with self.assertRaises(evaluation.EvaluationError):
            evaluation._read(fifo, evaluation.MAX_RECEIPT_BYTES, private=True)
        evaluation.freeze(self.receipt)
        original = self.receipt.read_bytes()
        hardlink = self.folder / "hardlinked-receipt.json"
        os.link(self.receipt, hardlink)
        for path in (self.receipt, hardlink):
            with self.assertRaises(evaluation.EvaluationError):
                evaluation.verify(path)
        self.assertEqual(self.receipt.read_bytes(), original)
        self.assertEqual(hardlink.read_bytes(), original)

    def test_fixed_source_whitelist_includes_imported_destination_reference(self):
        # The real production function is covered independently from this
        # class's synthetic artifact path injection used in freeze tests.
        paths = _REAL_SOURCE_PATHS()
        self.assertEqual(len(paths), 14)
        self.assertEqual(paths["shared/destinations.json"], evaluation.ROOT / "shared/destinations.json")

    def test_cli_only_reports_safe_summary_and_fails_closed(self):
        log = io.StringIO()
        with redirect_stdout(log):
            self.assertEqual(evaluation.main(["freeze", "--receipt", str(self.receipt)]), 0)
            self.assertEqual(evaluation.main(["verify", "--receipt", str(self.receipt)]), 0)
            self.assertEqual(evaluation.main(["run", "--receipt", str(self.receipt), "--output", str(self.report)]), 0)
            self.assertEqual(evaluation.main(["run", "--receipt", str(self.receipt), "--output", str(self.report)]), 2)
        text = log.getvalue()
        self.assertNotIn("受控", text)
        self.assertNotIn(str(self.folder), text)
        self.assertEqual(len(text.splitlines()), 4)
        self.assertTrue(all(json.loads(line)["model_calls"] == 0 for line in text.splitlines()))


if __name__ == "__main__":
    unittest.main()
