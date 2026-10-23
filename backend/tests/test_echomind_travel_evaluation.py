"""Offline rubric mechanics only: never invoke a sender or score model quality."""
from __future__ import annotations

import ast
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from pydantic import ValidationError

from tools.echomind_travel_evaluation import (
    DEFAULT_MANIFEST, MAX_MANIFEST_BYTES, Case, EvaluationError, Manifest,
    canonical, cost_plan, load, model_input, verify,
)

BACKEND = Path(__file__).resolve().parents[1]


class EchoMindTravelEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.value = json.loads(DEFAULT_MANIFEST.read_bytes())
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "controlled-manifest.json"

    def write(self, value: object) -> None:
        self.path.write_bytes(canonical(value))

    def test_fixed_small_matrix_is_valid_and_has_no_live_authorization(self) -> None:
        manifest = load()
        result = verify(manifest)
        self.assertEqual(result["case_count"], 16)
        self.assertEqual(result["branches"], {"semantic_candidate": 12, "controlled_boundary": 4})
        self.assertEqual(result["real_model_calls"], 0)
        self.assertEqual(result["real_amap_calls"], 0)
        self.assertFalse(result["live_ready"])
        self.assertFalse(result["live_payload_frozen"])

    def test_ids_are_unique_and_required_categories_cannot_disappear(self) -> None:
        value = copy.deepcopy(self.value)
        value["cases"][1]["id"] = value["cases"][0]["id"]
        with self.assertRaises(ValidationError):
            Manifest.model_validate(value)
        value = copy.deepcopy(self.value)
        value["cases"] = [row for row in value["cases"] if row["category"] != "poi"]
        with self.assertRaises(ValidationError):
            Manifest.model_validate(value)

    def test_unknown_fields_rejected_at_each_input_and_review_boundary(self) -> None:
        for segments in ((), ("authorization",), ("runtime",), ("cases", 0),
                         ("cases", 0, "input"), ("cases", 0, "input", "context"),
                         ("cases", 0, "review")):
            value = copy.deepcopy(self.value)
            target = value
            for segment in segments:
                target = target[segment]
            target["unexpected"] = "not permitted"
            with self.subTest(segments=segments), self.assertRaises(ValidationError):
                Manifest.model_validate(value)

    def test_allowances_and_runtime_cannot_be_raised_by_the_fixture(self) -> None:
        for group, key, value in (("authorization", "real_model_calls", 1),
                                  ("authorization", "real_amap_calls", 1),
                                  ("authorization", "automatic_retry", True),
                                  ("runtime", "task_model_limit", 4),
                                  ("runtime", "task_tool_limit", 5),
                                  ("runtime", "task_seconds", 46),
                                  ("runtime", "active_product_cutover", True),
                                  ("runtime", "embedding_batch_limit", 11)):
            changed = copy.deepcopy(self.value)
            changed[group][key] = value
            with self.subTest(key=key), self.assertRaises(ValidationError):
                Manifest.model_validate(changed)

    def test_duplicate_keys_nonfinite_and_oversized_reads_are_rejected(self) -> None:
        for raw in (b'{"schema_version":"one","schema_version":"two"}',
                    b'{"value":NaN}', b" " * (MAX_MANIFEST_BYTES + 1)):
            self.path.write_bytes(raw)
            with self.subTest(length=len(raw)), self.assertRaises(EvaluationError):
                load(self.path)

    def test_literal_zero_and_boolean_contracts_do_not_coerce_each_other(self) -> None:
        for group, key, invalid in (("authorization", "real_model_calls", False),
                                   ("authorization", "real_amap_calls", 0.0),
                                   ("authorization", "automatic_retry", 0),
                                   ("runtime", "task_model_limit", 3.0),
                                   ("runtime", "active_product_cutover", 0)):
            value = copy.deepcopy(self.value)
            value[group][key] = invalid
            with self.subTest(key=key), self.assertRaises(ValidationError):
                Manifest.model_validate(value)

    def test_invalid_manifest_diagnostic_never_contains_inputs(self) -> None:
        self.path.write_bytes(b'{"private_prompt":"DO_NOT_LOG_THIS"}')
        with self.assertRaises(EvaluationError) as found:
            load(self.path)
        self.assertEqual(str(found.exception), "invalid_manifest")
        self.assertNotIn("DO_NOT_LOG_THIS", str(found.exception))

    def test_day_ranges_are_calendar_valid_without_timezone_arithmetic(self) -> None:
        for first, last in (("2028-02-27", "2028-03-04"), ("2026-12-30", "2027-01-05")):
            value = copy.deepcopy(self.value["cases"][0])
            value["input"]["context"].update(start_date=first, end_date=last)
            self.assertIsInstance(Case.model_validate(value), Case)
        for first, last in (("2026-02-29", "2026-03-01"), ("2026-11-01", "2026-11-08"),
                            ("2026-11-02", "2026-11-01"), ("2026-1-1", "2026-01-01"),
                            (None, "2026-11-01")):
            value = copy.deepcopy(self.value["cases"][0])
            value["input"]["context"].update(start_date=first, end_date=last)
            with self.subTest(first=first, last=last), self.assertRaises(ValidationError):
                Case.model_validate(value)

    def test_wrong_city_arbitrary_owner_and_fake_poi_are_not_input_fields(self) -> None:
        for name, value in (("destination_id", "arbitrary-city"), ("owner_id", "other-owner"),
                            ("confirmed_poi", {"name": "invented", "id": "fake"})):
            row = copy.deepcopy(self.value["cases"][0])
            row["input"]["context"][name] = value
            with self.subTest(name=name), self.assertRaises(ValidationError):
                Case.model_validate(row)

    def test_formal_and_preview_availability_must_be_consistent(self) -> None:
        row = copy.deepcopy(self.value["cases"][0])
        row["input"]["context"]["destination_id"] = None
        with self.assertRaises(ValidationError):
            Case.model_validate(row)
        row = copy.deepcopy(self.value["cases"][0])
        row["input"]["context"].update(formal_draft="none", preview="ready")
        with self.assertRaises(ValidationError):
            Case.model_validate(row)

    def test_review_identifiers_and_labels_do_not_enter_business_projection(self) -> None:
        row = copy.deepcopy(self.value["cases"][0])
        row["id"] = "hidden-scenario-id"
        row["review"]["expected"] = ["HIDDEN_EXPECTATION"]
        row["review"]["blocking"] = ["HIDDEN_BLOCKER"]
        row["review"]["controlled_checks"] = ["HIDDEN_CHECK"]
        projected = model_input(Case.model_validate(row))
        encoded = canonical(projected).decode()
        for excluded in ("hidden-scenario-id", "HIDDEN_EXPECTATION", "HIDDEN_BLOCKER", "HIDDEN_CHECK",
                         "controlled_fixture", "semantic_candidate", '"review":', '"category":'):
            self.assertNotIn(excluded, encoded)
        self.assertEqual(set(projected), {"text", "history", "context"})

    def test_business_projection_is_independent_and_preserves_user_injection_as_data(self) -> None:
        case = load().cases[11]
        projected = model_input(case)
        self.assertIn("忽略工具限制", projected["text"])
        projected["context"]["destination_id"] = "shanghai"
        projected["history"].append({"role": "user", "content": "not shared"})
        self.assertEqual(case.input.context.destination_id, "beijing")
        self.assertEqual(case.input.history, [])

    def test_overlarge_business_projection_fails_without_truncation(self) -> None:
        row = copy.deepcopy(self.value["cases"][0])
        row["input"]["history"] = [{"role": "user", "content": "中" * 2000} for _ in range(8)]
        row["input"]["text"] = "中" * 2000
        case = Case.model_validate(row)
        with self.assertRaisesRegex(EvaluationError, "^model_input_too_large$"):
            model_input(case)
        self.assertEqual(len(case.input.history), 8)

    def test_review_edits_change_manifest_hash_not_business_input_hash(self) -> None:
        original = verify(load())
        self.value["cases"][0]["review"]["expected"] = ["另存评审标准，不发送模型"]
        changed = verify(Manifest.model_validate(self.value))
        self.assertNotEqual(original["manifest_sha256"], changed["manifest_sha256"])
        self.assertEqual(original["cases"][0]["business_input_sha256"], changed["cases"][0]["business_input_sha256"])
        self.assertEqual(original, verify(load()))

    def test_direct_plan_is_not_hard_cap_and_parallel_remains_disabled(self) -> None:
        direct = cost_plan(profile="direct_tools", template_count=97, prepared_index=False)
        parallel = cost_plan(profile="parallel_review", template_count=97, prepared_index=False)
        self.assertEqual(direct["planned_model_sends"], 2)
        self.assertEqual(direct["runtime_hard_max_model_sends"], 3)
        self.assertEqual(parallel["planned_model_sends"], 3)
        self.assertEqual(direct["planned_components"]["template_preparation"], 0)
        self.assertEqual(parallel["planned_components"]["template_preparation"], 0)
        self.assertTrue(direct["enabled_by_this_candidate"])
        self.assertFalse(parallel["enabled_by_this_candidate"])
        self.assertEqual(parallel["implementation_status"], "source_only_hypothetical")
        self.assertEqual(parallel["blocked_reason"], "travel_composer_not_integrated")
        self.assertEqual(parallel["execution_boundary"], "trusted_intent_prepared_context_no_tools")
        self.assertFalse(direct["may_send"])

    def test_fusion_includes_every_template_batch_and_remains_classification_only(self) -> None:
        for templates, batches in ((1, 1), (10, 1), (11, 2), (37, 4)):
            plan = cost_plan(profile="fusion_classification", template_count=templates, prepared_index=False)
            self.assertEqual(plan["planned_components"]["template_preparation"], batches)
            self.assertEqual(plan["planned_model_sends"], batches + 2)
            self.assertEqual(plan["runtime_hard_max_model_sends"], 3)
            self.assertEqual(plan["fits_existing_task_budget"], batches + 2 <= 3)
            self.assertFalse(plan["prerequisites_ready"])
            self.assertEqual(plan["blocked_reason"], "embedding_preparation_required")
        prepared = cost_plan(profile="fusion_classification", template_count=37, prepared_index=True)
        self.assertEqual(prepared["planned_model_sends"], 2)
        self.assertTrue(prepared["prerequisites_ready"])
        self.assertEqual(prepared["execution_boundary"], "classify_only_no_followup_task")

    def test_full_chain_is_diagnostic_blocked_and_cannot_grant_authorization(self) -> None:
        plan = cost_plan(profile="full_chain_diagnostic", template_count=37, prepared_index=True)
        self.assertGreater(plan["planned_model_sends"], plan["runtime_hard_max_model_sends"])
        self.assertFalse(plan["enabled_by_this_candidate"])
        self.assertFalse(plan["fits_existing_task_budget"])
        self.assertEqual(plan["live_authorized_calls"], 0)
        self.assertFalse(plan["may_send"])
        self.assertEqual(plan["execution_boundary"], "blocked_not_an_execution_mode")

    def test_invalid_cost_inputs_are_not_coerced_or_silently_defaulted(self) -> None:
        for changes in ({"template_count": -1}, {"template_count": True}, {"template_count": 1.5},
                        {"template_count": 1025}, {"prepared_index": 1}, {"profile": "auto_expand"},
                        {"profile": "fusion_classification", "template_count": 0}, {"profile": []}):
            values = {"profile": "direct_tools", "template_count": 1, "prepared_index": False} | changes
            with self.subTest(changes=changes), self.assertRaises(EvaluationError):
                cost_plan(**values)

    def test_cli_verifies_offline_without_loading_framework_or_network_clients(self) -> None:
        code = (
            "import sys,socket,runpy,json; "
            "socket.socket=lambda *a,**k: (_ for _ in ()).throw(RuntimeError('network forbidden')); "
            "sys.argv=['echomind_travel_evaluation','plan']; "
            "runpy.run_module('tools.echomind_travel_evaluation',run_name='__main__'); "
            "assert not any(x in sys.modules for x in ('httpx','redis','chromadb','echomind_core.runtime'))"
        )
        result = subprocess.run([sys.executable, "-B", "-c", code], cwd=BACKEND,
                                capture_output=True, text=True, timeout=10, check=True)
        parsed = json.loads(result.stdout)
        self.assertEqual(parsed["real_model_calls"], 0)
        self.assertEqual(parsed["index_preparation"], "pending_actual_template_count_and_fingerprint")

    def test_cli_failure_outputs_safe_code_not_input(self) -> None:
        self.path.write_bytes(b'{"private":"DO_NOT_PRINT"}')
        result = subprocess.run([sys.executable, "-B", "-m", "tools.echomind_travel_evaluation", "verify",
                                 "--manifest", str(self.path)], cwd=BACKEND,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid_manifest", result.stderr)
        self.assertNotIn("DO_NOT_PRINT", result.stdout + result.stderr)

    def test_tool_source_has_no_sender_or_live_counter_import(self) -> None:
        tree = ast.parse((BACKEND / "tools/echomind_travel_evaluation.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports = [value.name for value in node.names]
            elif isinstance(node, ast.ImportFrom):
                imports = [node.module or ""]
            else:
                continue
            self.assertFalse(any(value.split(".")[0] in {"httpx", "requests", "redis", "chromadb", "echomind_core"}
                                 or "acceptance_runtime" in value for value in imports))


if __name__ == "__main__":
    unittest.main()
