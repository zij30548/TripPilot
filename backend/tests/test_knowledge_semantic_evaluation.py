"""Synthetic corpus/questions/vectors only; never grades reviewed real material."""
from __future__ import annotations

from contextlib import redirect_stdout
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
from typing import Literal
import unittest
from unittest.mock import patch

from core.schemas.knowledge_national import NationalKnowledgeResponse, OfflineVectorArtifact
from tests.test_knowledge_national import controlled_index, corpus_values, reviewed
from tests.test_knowledge_national_evaluation import dataset_values as old_dataset_values
from tests.test_knowledge_relevance_evaluation import dataset_values as relevance_dataset_values
from tooling.travel.knowledge_national import NationalKnowledgeIndex, fingerprint
from tools import knowledge_semantic_evaluation as evaluation

_REAL_CANDIDATE = evaluation._candidate


def dataset_values(base=None):
    base = base or controlled_index()
    cases = []
    for split in ("development", "validation"):
        for number in range(20):
            absent = number >= 12
            limited = not absent and number % 2 == 1
            missing = [f"受控缺失事项{number}"] if absent or limited else []
            parts = [] if absent else [{"text": "儿童预约", "required": ["sh-entry/entry"],
                                        "availability": "evidence", "missing_reason": None}]
            parts += [{"text": text, "required": [], "availability": "missing", "missing_reason": "live_uncollected"}
                      for text in missing]
            cases.append({"id": f"synthetic-{split}-{number}", "split": split, "city_id": "310000",
                "venue_id": "shanghai-museum", "question": f"合成新题{split}儿童预约{number}" if not absent else f"合成新题{split}星际量子传送{number}",
                "visit_date": None, "relevant": [] if absent else ["sh-entry/entry"],
                "required": [] if absent else ["sh-entry/entry"],
                "expectation": "no_material" if absent else "limited" if limited else "evidence",
                "rationale": "受控语料与受控问题，不是官方资料质量结果。", "unanswered": missing if limited else [], "subquestions": parts})
    return {"schema_version": "knowledge-semantic-evaluation-v1", "protocol_version": evaluation.PROTOCOL,
            "corpus_version": base.corpus.corpus_version, "corpus_hash": base.hash,
            "thresholds": {"hit_at_3": .9, "required_at_5": .9, "no_material_false_hit_rate": .1, "cross_scope_hits": 0},
            "cases": cases}


def artifact_values(base, dataset):
    return {"schema_version": "knowledge-semantic-controlled-artifact-v1", "provenance": "synthetic_controlled_not_model",
        "fixture_version": "synthetic-v1", "corpus_version": dataset.corpus_version,
        "document_vectors": {"schema_version": "knowledge-offline-vectors-v1", "corpus_hash": base.hash,
            "model": evaluation.CONTROLLED_MODEL, "dimension": 2,
            "units": [{"chunk_id": identifier, "unit_hash": digest, "values": [1.0, 0.0]}
                      for identifier, digest in base.unit_hashes.items()]},
        "queries": [{"request_hash": fingerprint(evaluation._request(case).model_dump(mode="json")),
            "question_sha256": hashlib.sha256(case.question.encode()).hexdigest(), "values": [1.0, 0.0]}
            for case in dataset.cases]}


class ControlledResponse(NationalKnowledgeResponse):
    schema_version: Literal["knowledge-semantic-offline-v1"] = "knowledge-semantic-offline-v1"
    method: Literal["offline-cosine-national-v1", "offline-rrf-national-v1"]
    candidate_version: Literal["knowledge-semantic-candidate-v1"] = evaluation.CANDIDATE_VERSION
    arm: Literal["semantic", "hybrid"]
    embedding_model: str
    embedding_dimension: int


class ControlledCandidate:
    candidate_version = evaluation.CANDIDATE_VERSION

    def __init__(self, base, vectors):
        self.base = NationalKnowledgeIndex(base.corpus, base.manifest)
        self.corpus, self.hash, self.dimension = self.base.corpus, self.base.hash, vectors.dimension
        self.seen = []

    def search(self, request, *, arm, query_vector, query_model):
        self.seen.append((request.model_dump(mode="json"), arm, query_vector, query_model))
        value = self.base.search(request).model_dump(mode="json")
        value.update(schema_version="knowledge-semantic-offline-v1", arm=arm,
            method=evaluation.METHODS["B" if arm == "semantic" else "C"],
            candidate_version=self.candidate_version, embedding_model=query_model, embedding_dimension=self.dimension)
        return ControlledResponse.model_validate_json(json.dumps(value))


class DatasetAndArtifactTests(unittest.TestCase):
    def setUp(self):
        self.base = controlled_index()
        self.dataset = evaluation.EvaluationDataset.model_validate_json(json.dumps(dataset_values(self.base)))
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="semantic-artifact-synthetic-"))).resolve()
        self.folder.chmod(0o700)
        self.artifact = self.folder / "vectors.json"
        self.raw = artifact_values(self.base, self.dataset)
        self.write(self.raw)

    def write(self, raw):
        self.artifact.write_text(json.dumps(raw, allow_nan=True), encoding="utf-8")
        self.artifact.chmod(0o600)

    def test_forty_fixed_cases_have_real_subquestion_denominators_and_unknowns(self):
        for mutate in (
            lambda value: value["cases"].pop(),
            lambda value: value["cases"][0].update(id=value["cases"][1]["id"]),
            lambda value: value["cases"][0].update(question=value["cases"][1]["question"]),
            lambda value: value["cases"][0].update(split="validation"),
            lambda value: value["cases"][12].update(expectation="evidence"),
            lambda value: value["cases"][1].update(unanswered=[]),
            lambda value: value["cases"][1]["subquestions"][1].update(required=["sh-entry/entry"]),
            lambda value: value["cases"][0]["subquestions"][0].update(missing_reason="not_in_materials"),
            lambda value: value["cases"][0]["subquestions"][0].update(required=[]),
            lambda value: value["cases"][0]["subquestions"][0].update(unknown="forbidden"),
            lambda value: value["thresholds"].update(hit_at_3=.8),
        ):
            raw = dataset_values(self.base); mutate(raw)
            with self.assertRaises(ValueError):
                evaluation.EvaluationDataset.model_validate_json(json.dumps(raw))

    def test_controlled_artifact_exact_document_and_request_bindings(self):
        artifact, queries, digest = evaluation.load_artifact(self.artifact, self.dataset, self.base)
        self.assertEqual(artifact.document_vectors.model, evaluation.CONTROLLED_MODEL)
        self.assertEqual(len(queries), 40)
        self.assertEqual(digest, hashlib.sha256(self.artifact.read_bytes()).hexdigest())
        self.assertEqual(set(evaluation._request(self.dataset.cases[1]).model_dump()),
                         {"city_id", "venue_id", "question", "visit_date", "limit"})

    def test_missing_query_or_document_never_becomes_successful_no_match(self):
        for mutate in (
            lambda x: x["queries"].pop(),
            lambda x: x["queries"].append(x["queries"][0]),
            lambda x: x["queries"][0].update(request_hash="a" * 64),
            lambda x: x["queries"][0].update(question_sha256="a" * 64),
            lambda x: x["document_vectors"]["units"].pop(),
            lambda x: x["document_vectors"]["units"][0].update(chunk_id="forged/unit"),
            lambda x: x["document_vectors"]["units"][0].update(unit_hash="a" * 64),
            lambda x: x["document_vectors"]["units"].__setitem__(1, x["document_vectors"]["units"][0]),
        ):
            raw = copy.deepcopy(self.raw); mutate(raw); self.write(raw)
            with self.assertRaises(ValueError):
                evaluation.load_artifact(self.artifact, self.dataset, self.base)

    def test_unknown_model_dimension_version_real_claim_extra_and_nonfinite_rejected(self):
        for mutate in (
            lambda x: x.update(provenance="real_recorded_embedding"),
            lambda x: x.update(schema_version="unknown"),
            lambda x: x.update(extra="forbidden"),
            lambda x: x.update(corpus_version="wrong-version"),
            lambda x: x["document_vectors"].update(model="qwen-real-claim"),
            lambda x: x["document_vectors"].update(corpus_hash="f" * 64),
            lambda x: x["document_vectors"].update(dimension=3),
            lambda x: x["document_vectors"]["units"][0].update(values=[0.0, 0.0]),
            lambda x: x["queries"][0].update(values=[0.0, 0.0]),
            lambda x: x["queries"][0].update(values=[float("nan"), 0.0]),
            lambda x: x["queries"][0].update(values=[1e308, 1e308]),
            lambda x: x["queries"][0].update(values=[True, 0.0]),
        ):
            raw = copy.deepcopy(self.raw); mutate(raw); self.write(raw)
            with self.assertRaises(ValueError):
                evaluation.load_artifact(self.artifact, self.dataset, self.base)

    def test_duplicate_json_keys_and_private_file_permissions_rejected(self):
        self.artifact.write_text('{"schema_version":"a","schema_version":"b"}', encoding="utf-8")
        with self.assertRaises(ValueError):
            evaluation.load_artifact(self.artifact, self.dataset, self.base)
        self.write(self.raw); self.artifact.chmod(0o644)
        with self.assertRaises(ValueError):
            evaluation.load_artifact(self.artifact, self.dataset, self.base)

    def test_same_query_vector_both_arms_no_assessment_labels_into_candidate(self):
        _, queries, _ = evaluation.load_artifact(self.artifact, self.dataset, self.base)
        vectors = OfflineVectorArtifact.model_validate_json(json.dumps(self.raw["document_vectors"]))
        candidate = ControlledCandidate(self.base, vectors)
        query = evaluation._request(self.dataset.cases[1])
        for arm in ("B", "C"):
            evaluation._BoundArm(candidate, arm, queries).search(query)
        first, second = candidate.seen
        self.assertEqual(first[0], second[0]); self.assertIs(first[2], second[2])
        self.assertEqual(set(first[0]), {"city_id", "venue_id", "question", "visit_date", "limit"})
        self.assertNotIn("subquestions", first[0])

    def test_subquestion_evidence_does_not_credit_missing_or_turn_failure_into_abstention(self):
        rows = evaluation.previous.evaluate_arm(self.base, NationalKnowledgeResponse, self.base, self.dataset.cases[:2])
        evaluation._subquestions(rows, self.dataset.cases[:2])
        summary = evaluation.summarize(rows, self.dataset.thresholds)
        self.assertEqual(summary["subquestion_evidence_at_5"], {"numerator": 2, "denominator": 2, "value": 1.0})
        self.assertEqual(summary["annotated_missing_subquestions"], 1)
        self.assertEqual(summary["semantic_quality_acceptance"], "not_assessed_controlled_vectors")
        rows[0]["error"] = "invalid_result"
        evaluation._subquestions(rows, self.dataset.cases[:2])
        self.assertEqual(evaluation.summarize(rows, self.dataset.thresholds)["subquestion_evidence_at_5"]["numerator"], 1)

    def test_wrong_arm_model_dimension_or_version_is_not_an_accepted_result(self):
        _, queries, _ = evaluation.load_artifact(self.artifact, self.dataset, self.base)
        vectors = OfflineVectorArtifact.model_validate_json(json.dumps(self.raw["document_vectors"]))
        candidate = ControlledCandidate(self.base, vectors)
        original = candidate.search
        for update in ({"arm": "hybrid"}, {"embedding_model": "claimed-other-model"},
                       {"embedding_dimension": 3}, {"candidate_version": "forged-version"}):
            def changed(request, **kwargs):
                return original(request, **kwargs).model_copy(update=update)
            with patch.object(candidate, "search", side_effect=changed):
                rows = evaluation.previous.evaluate_arm(evaluation._BoundArm(candidate, "B", queries),
                    ControlledResponse, self.base, self.dataset.cases[:1])
            self.assertEqual(rows[0]["error"], "invalid_result")
            self.assertFalse(rows[0]["all_required_at_5"])

    def test_complete_source_projection_still_rejects_tampering_with_unchanged_ids_and_hashes(self):
        _, queries, _ = evaluation.load_artifact(self.artifact, self.dataset, self.base)
        vectors = OfflineVectorArtifact.model_validate_json(json.dumps(self.raw["document_vectors"]))
        candidate = ControlledCandidate(self.base, vectors)
        original = candidate.search
        for kind in ("summary", "source_url", "acquisition_time"):
            def changed(request, **kwargs):
                result = original(request, **kwargs)
                if kind == "summary":
                    result.matches[0].section.summary = "未审核的替代摘要"
                elif kind == "source_url":
                    result.matches[0].source.url = "https://unreviewed.example.test/replaced"
                else:
                    result.matches[0].source.fetched_at = result.matches[0].source.fetched_at.replace(day=23)
                return result
            with patch.object(candidate, "search", side_effect=changed):
                rows = evaluation.previous.evaluate_arm(evaluation._BoundArm(candidate, "B", queries),
                    ControlledResponse, self.base, self.dataset.cases[:1])
            self.assertEqual(rows[0]["error"], "result_integrity_failed")
            self.assertEqual(rows[0]["reference_errors"], 1)
            self.assertFalse(rows[0]["hit_at_3"])

    def test_missing_subquestion_is_not_counted_as_answered_by_any_returned_unit(self):
        case = self.dataset.cases[1]
        rows = evaluation.previous.evaluate_arm(self.base, NationalKnowledgeResponse, self.base, [case])
        evaluation._subquestions(rows, [case])
        self.assertTrue(rows[0]["subquestion_evidence"][0]["evidence_covered"])
        self.assertIsNone(rows[0]["subquestion_evidence"][1]["evidence_covered"])
        self.assertEqual(evaluation.summarize(rows, self.dataset.thresholds)["subquestion_evidence_at_5"]["denominator"], 1)

    def test_source_whitelist_includes_candidate_tools_tests_and_fixed_embedding_policy(self):
        paths = evaluation._source_paths()
        for name in ("backend/evaluations/knowledge-semantic-v1.json",
                     "backend/tooling/travel/knowledge_semantic.py", "backend/tests/test_knowledge_semantic.py",
                     "backend/tools/knowledge_embedding_plan.py", "backend/tests/test_knowledge_embedding_plan.py",
                     "backend/echomind_core/embedding.py", "backend/core/llm_utils.py",
                     "backend/echomind_core/upstream/core/llm_utils.py"):
            self.assertIn(name, paths)


class FreezeAndRunTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="semantic-freeze-synthetic-"))).resolve()
        self.folder.chmod(0o700)
        self.base = controlled_index()
        self.files = {name: self.folder / f"{name}.json" for name in ("corpus", "manifest", "fresh", "old", "relevance")}
        corpus, manifest = reviewed(corpus_values())
        self.files["corpus"].write_text(corpus.model_dump_json(), encoding="utf-8")
        self.files["manifest"].write_text(manifest.model_dump_json(), encoding="utf-8")
        self.files["fresh"].write_text(json.dumps(dataset_values(self.base)), encoding="utf-8")
        self.files["old"].write_text(json.dumps(old_dataset_values(self.base)), encoding="utf-8")
        self.files["relevance"].write_text(json.dumps(relevance_dataset_values(self.base)), encoding="utf-8")
        self.marker = self.folder / "candidate.py"
        self.marker.write_text("# controlled candidate marker\n", encoding="utf-8")
        self.inputs, self.receipt, self.output, self.artifact = (self.folder / name for name in
                                                               ("inputs.json", "freeze.json", "report.json", "artifact.json"))
        self.dataset = evaluation.EvaluationDataset.model_validate_json(self.files["fresh"].read_bytes())
        self.artifact.write_text(json.dumps(artifact_values(self.base, self.dataset)), encoding="utf-8")
        self.artifact.chmod(0o600)
        for name, key in (("CORPUS_PATH", "corpus"), ("MANIFEST_PATH", "manifest"), ("DATASET_PATH", "fresh"),
                          ("OLD_DATASET_PATH", "old"), ("RELEVANCE_DATASET_PATH", "relevance")):
            self.enterContext(patch.object(evaluation, name, self.files[key]))
        self.source_paths = self.files | {"candidate": self.marker, "tool": Path(evaluation.__file__).resolve()}
        self.enterContext(patch.object(evaluation, "_input_paths", return_value=self.files))
        self.enterContext(patch.object(evaluation, "_source_paths", return_value=self.source_paths))
        self.candidates = []
        def candidate(base, vectors=None):
            if vectors is None:
                return ControlledCandidate, ControlledResponse
            value = ControlledCandidate(base, vectors); self.candidates.append(value)
            return value, ControlledResponse
        self.enterContext(patch.object(evaluation, "_candidate", side_effect=candidate))

    def prepare(self):
        evaluation.freeze_inputs(self.inputs)
        return evaluation.freeze(self.inputs, self.receipt)

    def test_freeze_inputs_no_candidate_required_search_or_network(self):
        self.marker.unlink()
        with patch.object(evaluation, "_candidate", side_effect=AssertionError("No candidate")), patch.object(
                NationalKnowledgeIndex, "search", side_effect=AssertionError("No search")):
            value = evaluation.freeze_inputs(self.inputs)
            checked, *_ = evaluation.verify_inputs(self.inputs)
        self.assertEqual(value, checked)
        self.assertEqual(len(value["files"]), 5)
        self.assertEqual(self.inputs.stat().st_mode & 0o777, 0o600)

    def test_fresh_inputs_cannot_relabel_either_old_dataset_as_new(self):
        for key in ("old", "relevance"):
            raw = dataset_values(self.base)
            raw["cases"][0]["question"] = json.loads(self.files[key].read_bytes())["cases"][0]["question"]
            self.files["fresh"].write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "known_question_reused_as_fresh"):
                evaluation.freeze_inputs(self.inputs)
            self.assertFalse(self.inputs.exists())

    def test_code_freeze_without_vectors_exists_and_is_exclusive(self):
        self.artifact.unlink()
        with patch.object(NationalKnowledgeIndex, "search", side_effect=AssertionError("No search")):
            frozen = self.prepare()
            checked, *_ = evaluation.verify(self.receipt)
        self.assertEqual(frozen, checked)
        original = self.receipt.read_bytes()
        with self.assertRaises(ValueError):
            evaluation.freeze(self.inputs, self.receipt)
        self.assertEqual(self.receipt.read_bytes(), original)

    def test_freeze_and_verify_reject_candidate_import_mutation(self):
        evaluation.freeze_inputs(self.inputs)
        def mutate(base, vectors=None):
            base.chunks[0][0].section.summary = "未授权的修改"
            return ControlledCandidate, ControlledResponse
        with patch.object(evaluation, "_candidate", side_effect=mutate):
            with self.assertRaisesRegex(ValueError, "source_changed_during_freeze"):
                evaluation.freeze(self.inputs, self.receipt)
        self.assertFalse(self.receipt.exists())
        evaluation.freeze(self.inputs, self.receipt)
        with patch.object(evaluation, "_candidate", side_effect=mutate):
            with self.assertRaisesRegex(ValueError, "source_changed_during_verification"):
                evaluation.verify(self.receipt)

    def test_code_or_input_or_receipt_drift_stops_before_search(self):
        self.prepare()
        for target in (self.marker, self.files["fresh"], self.files["old"], self.inputs):
            original = target.read_bytes(); target.write_bytes(original + b"\n")
            try:
                with patch.object(ControlledCandidate, "search", side_effect=AssertionError("No search")):
                    with self.assertRaises(ValueError):
                        evaluation.run(self.receipt, self.artifact, self.output, "development")
                self.assertFalse(self.output.exists())
            finally:
                target.write_bytes(original)

    def test_invalid_or_missing_artifact_stops_before_all_three_arms(self):
        self.prepare()
        self.artifact.unlink()
        with patch.object(NationalKnowledgeIndex, "search", side_effect=AssertionError("No search")):
            with self.assertRaises(ValueError):
                evaluation.run(self.receipt, self.artifact, self.output, "development")
        self.assertFalse(self.output.exists())

    def test_same_requests_three_arms_controlled_no_credentials_or_network_and_exclusive_output(self):
        self.prepare()
        with patch("socket.socket.connect", side_effect=AssertionError("No network")), patch(
                "os.getenv", side_effect=AssertionError("No credentials")):
            result = evaluation.run(self.receipt, self.artifact, self.output, "development")
        self.assertEqual(set(result["arms"]), {"A", "B", "C"})
        self.assertEqual((result["model_calls"], result["external_calls"]), (0, 0))
        self.assertTrue(result["mechanism_only"])
        self.assertTrue(result["quality_acceptance_not_assessed"])
        self.assertEqual(result["artifact_provenance"], "synthetic_controlled_not_model")
        self.assertEqual(result["summary"]["A"]["hit_at_3"]["denominator"], 12)
        self.assertEqual(result["summary"]["A"]["no_material_false_hits"]["denominator"], 8)
        requests = [[row["result"]["request"] for row in result["arms"][arm]] for arm in ("A", "B", "C")]
        self.assertEqual(requests[0], requests[1]); self.assertEqual(requests[1], requests[2])
        seen = self.candidates[0].seen
        self.assertEqual(len(seen), 40)
        for first, second in zip(seen[:20], seen[20:], strict=True):
            self.assertIs(first[2], second[2])
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        original = self.output.read_bytes()
        with self.assertRaises(ValueError):
            evaluation.run(self.receipt, self.artifact, self.output, "development")
        self.assertEqual(self.output.read_bytes(), original)

    def test_actual_candidate_wiring_on_synthetic_corpus_not_real_quality(self):
        _REAL_CANDIDATE(self.base)  # Pydantic imports may read its own plugin switch.
        with patch.object(evaluation, "_candidate", new=_REAL_CANDIDATE), patch(
                "socket.socket.connect", side_effect=AssertionError("No network")), patch(
                "os.getenv", side_effect=AssertionError("No credentials")):
            self.prepare()
            result = evaluation.run(self.receipt, self.artifact, self.output, "validation")
        self.assertEqual(result["arms"]["B"][0]["result"]["schema_version"], "knowledge-semantic-offline-v1")
        self.assertEqual(result["arms"]["B"][0]["result"]["embedding_dimension"], 2)
        self.assertEqual(result["summary"]["B"]["no_material_false_hits"]["numerator"], 8)
        self.assertFalse(result["summary"]["B"]["mechanical_thresholds_passed"])
        self.assertTrue(result["quality_acceptance_not_assessed"])

    def test_constructor_mutation_blocks_run_before_search(self):
        self.prepare()
        def mutate(base, vectors=None):
            candidate = ControlledCandidate(base, vectors) if vectors is not None else ControlledCandidate
            if vectors is not None:
                base.chunks[0][0].section.summary = "破坏共同基线"
            return candidate, ControlledResponse
        with patch.object(evaluation, "_candidate", side_effect=mutate), patch.object(
                ControlledCandidate, "search", side_effect=AssertionError("No search")):
            with self.assertRaisesRegex(ValueError, "candidate_mutated_shared_baseline"):
                evaluation.run(self.receipt, self.artifact, self.output, "development")
        self.assertFalse(self.output.exists())

    def test_constructor_cannot_mutate_shared_document_vectors_between_arms(self):
        self.prepare()
        def mutate(base, vectors=None):
            candidate = ControlledCandidate(base, vectors) if vectors is not None else ControlledCandidate
            if vectors is not None:
                vectors.units[0].values[0] = 0.5
            return candidate, ControlledResponse
        with patch.object(evaluation, "_candidate", side_effect=mutate), patch.object(
                ControlledCandidate, "search", side_effect=AssertionError("No search")):
            with self.assertRaisesRegex(ValueError, "candidate_mutated_shared_baseline"):
                evaluation.run(self.receipt, self.artifact, self.output, "development")
        self.assertFalse(self.output.exists())

    def test_artifact_changed_during_run_cannot_publish_report(self):
        self.prepare()
        original = ControlledCandidate.search
        changed = False
        def change(candidate, request, **kwargs):
            nonlocal changed
            if not changed:
                self.artifact.write_bytes(self.artifact.read_bytes() + b"\n"); changed = True
            return original(candidate, request, **kwargs)
        with patch.object(ControlledCandidate, "search", new=change):
            with self.assertRaisesRegex(ValueError, "frozen_version_changed_during_run"):
                evaluation.run(self.receipt, self.artifact, self.output, "development")
        self.assertFalse(self.output.exists())

    def test_private_receipts_symlinks_hardlinks_malformed_keys_and_no_live_cli(self):
        self.prepare()
        original = self.receipt.read_bytes()
        for mutate in (lambda x: x.update(model_calls=False), lambda x: x.update(extra="forbidden"),
                       lambda x: x.update(candidate_version="unknown"), lambda x: x.update(created_at="not-a-date")):
            value = json.loads(original); mutate(value)
            self.receipt.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(ValueError):
                evaluation.verify(self.receipt)
        self.receipt.write_bytes(original)
        link = self.folder / "link.json"; link.symlink_to(self.receipt)
        with self.assertRaises(ValueError):
            evaluation.verify(link)
        os.link(self.receipt, self.folder / "hard.json")
        with self.assertRaises(ValueError):
            evaluation.verify(self.receipt)
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(evaluation.main(["verify", "--receipt", str(self.receipt)]), 2)
        self.assertNotIn(str(self.folder), output.getvalue())
        self.assertNotIn("controlled", output.getvalue())


if __name__ == "__main__":
    unittest.main()
