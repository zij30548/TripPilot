"""No model transport: exact future inputs and zero-authorization checks."""
from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
import copy
import io
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.test_knowledge_national import controlled_index
from tests.test_knowledge_semantic_evaluation import dataset_values
from tools import knowledge_embedding_plan as plan
from tools import knowledge_semantic_evaluation as evaluation


class EmbeddingPlanTests(unittest.TestCase):
    def setUp(self):
        self.base = controlled_index()
        self.dataset = evaluation.EvaluationDataset.model_validate_json(json.dumps(dataset_values(self.base)))
        self.identity = {"code_fingerprint": "a" * 64, "code_receipt_sha256": "b" * 64,
                         "inputs_receipt_sha256": "c" * 64, "protocol_version": evaluation.PROTOCOL}
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="embedding-plan-synthetic-"))).resolve()
        self.folder.chmod(0o700)
        self.receipt, self.output = self.folder / "code.json", self.folder / "plan.json"

    def build(self, cases=None):
        return plan.build_plan(self.base, self.dataset.cases if cases is None else cases, self.identity)

    def test_policy_reads_literal_data_and_keeps_existing_provider_limits(self):
        with patch("os.getenv", side_effect=AssertionError("No config")), patch(
                "socket.socket.connect", side_effect=AssertionError("No connection")):
            policy = plan.provider_policy()
            result = self.build()
        self.assertEqual((policy["EMBEDDING_MODEL"], policy["DIMENSIONS"], policy["MAX_BATCH"]),
                         ("text-embedding-v4", 1024, 10))
        self.assertEqual(policy["MAX_INPUT_BYTES"], 48000)
        self.assertEqual((result["authorized_attempts"], result["actual_model_calls"], result["external_calls"]), (0, 0, 0))
        self.assertFalse(result["sender_available"])
        self.assertEqual((result["generation_calls"], result["rerank_calls"], result["automatic_retries"]), (0, 0, 0))

    def test_nonliteral_duplicate_missing_or_boolean_policy_is_refused_not_executed(self):
        original = plan.POLICY_SOURCE.read_text()
        for content in (
            original.replace('EMBEDDING_MODEL = "text-embedding-v4"', 'EMBEDDING_MODEL = dangerous()'),
            original + "\nMAX_BATCH = 10\n",
            original.replace("DIMENSIONS = 1024", "DIMENSIONS = True"),
            original.replace("MAX_BATCH = 10", "OTHER_BATCH = 10"),
            original.replace("REQUEST_SECONDS = 20.0", "REQUEST_SECONDS = 21.0"),
            original.replace("MAX_RESPONSE_BYTES = 2 * 1024 * 1024", "MAX_RESPONSE_BYTES = 2 ** 100000"),
        ):
            source = self.folder / "policy.py"
            source.write_text(content)
            with self.assertRaises(ValueError):
                plan.provider_policy(source)

    def test_payload_shape_limits_and_independent_text_snapshot(self):
        policy = plan.provider_policy()
        texts = ["完整条件，但不添加结论"]
        result = plan.model_payload(texts, policy)
        self.assertEqual(set(result), {"model", "input", "dimensions", "encoding_format"})
        self.assertEqual(result["encoding_format"], "float")
        texts[0] = "修改调用方"
        self.assertNotEqual(texts, result["input"])
        for values in ([], [""] , [" "], [True], ["x"] * 11, ["中" * 3000], ["x" * 8001]):
            with self.assertRaises(ValueError):
                plan.model_payload(values, policy)

    def test_document_text_keeps_complete_summary_excerpt_but_no_source_url_or_labels(self):
        original = evaluation.previous._base_projection(self.base)
        result = self.build()
        expected = [plan.document_text(match, self.base) for match, _ in sorted(self.base.chunks, key=lambda row: row[0].chunk_id)]
        operations = [operation for operation in result["operations"] if operation["kind"] == "documents"]
        actual = [text for operation in operations for text in operation["payload"]["input"]]
        self.assertEqual(actual, expected)
        for match, _ in self.base.chunks:
            text = plan.document_text(match, self.base)
            self.assertIn(match.section.summary, text)
            self.assertIn(match.section.excerpt, text)
            self.assertNotIn(match.source.url, text)
        self.assertEqual(evaluation.previous._base_projection(self.base), original)

    def test_query_text_is_exact_original_without_case_id_labels_or_decomposition(self):
        result = self.build()
        operations = [operation for operation in result["operations"] if operation["kind"] == "queries"]
        actual = [text for operation in operations for text in operation["payload"]["input"]]
        self.assertEqual(actual, [case.question for case in self.dataset.cases])
        self.assertEqual(len(actual), 40)
        changed = copy.deepcopy(self.dataset.cases)
        for case in changed:
            case.id = "changed-id"
            case.rationale = "预期答案不得发送"
            case.unanswered = ["不能泄露的评审事项"]
        self.assertEqual(result, self.build(changed))

    def test_batch_operations_are_deterministic_with_content_and_request_binding(self):
        first, second = self.build(), self.build()
        self.assertEqual(first, second)
        self.assertEqual(first["proposed_physical_attempts"], math.ceil(len(self.base.chunks) / 10) + 4)
        self.assertEqual(len({op["operation_id"] for op in first["operations"]}), len(first["operations"]))
        for operation in first["operations"]:
            raw = plan.bounded_json(operation["payload"], 48000).encode()
            self.assertEqual((operation["payload_sha256"], operation["payload_bytes"]), (plan.sha256(raw), len(raw)))
            self.assertEqual(len(operation["items"]), len(operation["payload"]["input"]))
            for item, text in zip(operation["items"], operation["payload"]["input"], strict=True):
                self.assertEqual(item["text_sha256"], plan.sha256(text.encode()))
                self.assertNotIn("text", item)
                if operation["kind"] == "documents":
                    self.assertEqual(item["unit_hash"], self.base.unit_hashes[item["item_id"]])
                else:
                    self.assertEqual(len(item["request_hash"]), 64)

    def test_byte_budget_splits_without_truncating_or_dropping_inputs(self):
        policy = plan.provider_policy() | {"MAX_BATCH": 3, "MAX_INPUT_BYTES": 200}
        items = [{"item_id": str(i), "text": "中" * 22} for i in range(5)]
        operations = plan._operations("documents", items, policy)
        self.assertEqual([text for op in operations for text in op["payload"]["input"]], [item["text"] for item in items])
        self.assertGreater(len(operations), 2)
        self.assertTrue(all(op["payload_bytes"] <= 200 for op in operations))
        with self.assertRaises(ValueError):
            plan._operations("documents", [{"text": "中" * 100}], policy)

    def test_empty_reused_query_or_scope_mismatch_does_not_get_exported(self):
        with self.assertRaises(ValueError):
            self.build([])
        with self.assertRaises(ValueError):
            self.build([self.dataset.cases[0], self.dataset.cases[0]])
        changed = copy.deepcopy(self.dataset.cases)
        changed[0].venue_id = "beijing-museum"
        with self.assertRaises(ValueError):
            self.build(changed)

    def test_exclusive_private_output_recheck_and_tamper_refusal(self):
        expected = self.build()
        with patch.object(plan, "_prepared", return_value=expected):
            self.assertEqual(plan.prepare(self.receipt, self.output), expected)
            self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
            self.assertEqual(plan.verify_plan(self.receipt, self.output), expected)
            old = self.output.read_bytes()
            with self.assertRaises(ValueError):
                plan.prepare(self.receipt, self.output)
            self.assertEqual(self.output.read_bytes(), old)
            changed = json.loads(old)
            changed["authorized_attempts"] = 8
            self.output.write_text(json.dumps(changed))
            with self.assertRaises(ValueError):
                plan.verify_plan(self.receipt, self.output)
            self.output.write_text('{"duplicate":1,"duplicate":2}')
            with self.assertRaises(ValueError):
                plan.verify_plan(self.receipt, self.output)

    def test_freeze_failure_creates_no_plan_and_safe_cli_has_no_send_operation(self):
        output = io.StringIO()
        with patch.object(plan, "_prepared", side_effect=ValueError("private detail")), redirect_stdout(output):
            self.assertEqual(plan.main(["prepare", "--receipt", str(self.receipt), "--output", str(self.output)]), 1)
        self.assertFalse(self.output.exists())
        self.assertNotIn("private detail", output.getvalue())
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            plan.main(["send", "--receipt", str(self.receipt)])

    def test_numeric_and_boolean_equal_values_cannot_change_exact_payload_types(self):
        expected = self.build()
        with patch.object(plan, "_prepared", return_value=expected):
            for mutate in (
                lambda raw: raw.update(authorized_attempts=False),
                lambda raw: raw.update(actual_model_calls=0.0),
                lambda raw: raw["operations"][0]["payload"].update(dimensions=1024.0),
            ):
                changed = copy.deepcopy(expected)
                mutate(changed)
                self.output.write_text(json.dumps(changed))
                self.output.chmod(0o600)
                with self.assertRaises(ValueError):
                    plan.verify_plan(self.receipt, self.output)


if __name__ == "__main__":
    unittest.main()
