"""Server-schema-only diagnostics; all values and unknown keys are adversarial."""
from __future__ import annotations

from copy import deepcopy
import json
import math
import unittest

from core.llm_utils import ARGUMENT_DIAGNOSTIC_VERSION, RuntimeFailure, tool_argument_diagnostics, validate_arguments
from core.schemas.dialogue import GROUPS, group_schema


def obj(fields):
    return {"type": "object", "properties": fields, "required": list(fields), "additionalProperties": False}


class ToolArgumentDiagnosticTests(unittest.TestCase):
    def rejected(self, value, schema):
        value_before, schema_before = deepcopy(value), deepcopy(schema)
        with self.assertRaises(RuntimeFailure) as captured:
            validate_arguments(value, schema)
        self.assertEqual(captured.exception.code, "tool_arguments")
        self.assertEqual(str(captured.exception), "tool_arguments")
        self.assertEqual(schema, schema_before)
        if not (type(value) is float and math.isnan(value)):
            self.assertEqual(value, value_before)
        details = captured.exception.details
        self.assertEqual(set(details), {"schema_version", "errors", "truncated"})
        self.assertEqual(details["schema_version"], ARGUMENT_DIAGNOSTIC_VERSION)
        self.assertIs(type(details["truncated"]), bool)
        self.assertTrue(1 <= len(details["errors"]) <= 4)
        for row in details["errors"]:
            self.assertLessEqual(len(row["path"]), 12)
            self.assertTrue(set(row) <= {"path", "reason", "count", "limit", "expected_type", "actual_type"})
        return details

    def test_runtime_failure_original_code_trace_and_text_remain_compatible(self):
        error = RuntimeFailure("context_changed")
        self.assertEqual((str(error), error.args, error.code, error.trace, error.details),
                         ("context_changed", ("context_changed",), "context_changed", {}, {}))

    def test_nested_null_type_identifies_schema_path_without_value(self):
        details = self.rejected({"requirements": {"start_date": None}},
            obj({"requirements": obj({"start_date": {"type": "string", "format": "date"}})}))
        self.assertEqual(details["errors"], [{"path": ["requirements", "start_date"],
            "reason": "type_mismatch", "expected_type": "string", "actual_type": "null"}])

    def test_missing_fields_are_known_schema_paths_and_capped_at_four(self):
        schema = obj({f"field_{index}": {"type": "integer"} for index in range(10)})
        details = self.rejected({}, schema)
        self.assertEqual([row["path"] for row in details["errors"]], [[f"field_{i}"] for i in range(4)])
        self.assertTrue(all(row["reason"] == "missing_required" for row in details["errors"]))
        self.assertTrue(details["truncated"])

    def test_unknown_names_and_input_values_never_appear_in_diagnostics(self):
        secret_key = "sk-private-unknown-field-name-DO-NOT-LOG"
        secret_value = "PRIVATE BODY OR PASSWORD MUST NOT APPEAR"
        details = self.rejected({"count": secret_value, secret_key: secret_value},
            obj({"count": {"type": "integer"}}))
        raw = json.dumps(details)
        self.assertNotIn(secret_key, raw)
        self.assertNotIn(secret_value, raw)
        self.assertEqual(details["errors"], [{"path": [], "reason": "additional_fields", "count": 1}])

    def test_enum_const_pattern_and_date_do_not_reveal_candidates_or_input(self):
        cases = [({"type": "string", "enum": ["PRIVATE_ENUM"]}, "PRIVATE_ACTUAL", "enum_mismatch"),
            ({"type": "string", "const": "PRIVATE_CONST"}, "PRIVATE_ACTUAL", "const_mismatch"),
            ({"type": "string", "pattern": "PRIVATE_PATTERN"}, "PRIVATE_ACTUAL", "pattern_mismatch"),
            ({"type": "string", "format": "date"}, "2026-99-99", "date_format")]
        for rule, value, reason in cases:
            with self.subTest(reason=reason):
                details = self.rejected({"known": value}, obj({"known": rule}))
                self.assertEqual(details["errors"], [{"path": ["known"], "reason": reason}])
                self.assertNotIn(value, json.dumps(details))
                self.assertNotIn("PRIVATE_", json.dumps(details))

    def test_lengths_report_counts_not_body(self):
        text = self.rejected("private-long-value", {"type": "string", "maxLength": 3})
        self.assertEqual(text["errors"], [{"path": [], "reason": "string_length", "count": 18}])
        rows = self.rejected(["private"] * 3, {"type": "array", "maxItems": 2, "items": {"type": "string"}})
        self.assertEqual(rows["errors"], [{"path": [], "reason": "array_length", "count": 3}])
        many = self.rejected({str(i): i for i in range(33)},
            {"type": "object", "properties": {str(i): {"type": "integer"} for i in range(33)},
             "required": [], "additionalProperties": False})
        self.assertEqual(many["errors"], [{"path": [], "reason": "object_size", "count": 33, "limit": 32}])

    def test_array_paths_are_bounded_positions_and_known_fields(self):
        details = self.rejected({"places": [{"minutes": "private"}]},
            obj({"places": {"type": "array", "items": obj({"minutes": {"type": "integer"}})}}))
        self.assertEqual(details["errors"][0]["path"], ["places", 0, "minutes"])
        self.assertNotIn("private", json.dumps(details))

    def test_anyof_prefers_deep_nested_problem_over_root_type_mismatch(self):
        details = self.rejected({"requirements": {"start_date": None}}, {"anyOf": [
            {"type": "null"}, obj({"requirements": obj({"start_date": {"type": "string"}})})]})
        self.assertEqual(details["errors"][0]["path"], ["requirements", "start_date"])
        self.assertEqual(details["errors"][0]["reason"], "type_mismatch")

    def test_anyof_prefers_matching_action_discriminator_not_an_unrelated_variant(self):
        schema = {"anyOf": [obj({"action": {"type": "string", "const": "other"},
            "unrelated": obj({"deeper": {"type": "string"}})}),
            obj({"action": {"type": "string", "const": "chosen"}, "count": {"type": "integer"}})]}
        details = self.rejected({"action": "chosen", "count": "private"}, schema)
        self.assertEqual(details["errors"][0]["path"], ["count"])
        self.assertEqual(details["errors"][0]["reason"], "type_mismatch")

    def test_anyof_diagnostics_do_not_change_original_schema_failure_priority(self):
        with self.assertRaises(RuntimeFailure) as captured:
            validate_arguments("allowed later", {"anyOf": [{"unsupported": True}, {"type": "string"}]})
        self.assertEqual(captured.exception.code, "tool_schema")
        self.assertEqual(captured.exception.details, {})

    def test_strict_types_null_and_numeric_boundaries_still_reject(self):
        for value, schema in [(True, {"type": "integer"}), (1., {"type": "integer"}),
                (1, {"type": "boolean"}), (None, {"type": "string"}),
                (float("inf"), {"type": "number"}), (float("nan"), {"type": "number"}),
                (101, {"type": "integer", "maximum": 100})]:
            with self.subTest(kind=type(value).__name__):
                self.rejected(value, schema)
        validate_arguments(None, {"anyOf": [{"type": "integer"}, {"type": "null"}]})
        validate_arguments(0, {"type": "integer", "minimum": 0})
        validate_arguments("2028-02-29", {"type": "string", "format": "date"})

    def test_unsafe_schema_property_names_are_not_copied(self):
        unsafe = "known-but-not-log-safe\\nPRIVATE"
        details = self.rejected({unsafe: "secret"}, obj({unsafe: {"type": "integer"}}))
        self.assertNotIn(unsafe, json.dumps(details))
        self.assertNotIn("PRIVATE", json.dumps(details))
        self.assertEqual(details["errors"][0]["path"], [])
        self.assertTrue(details["truncated"])

    def test_many_item_errors_and_observation_nodes_are_bounded(self):
        details = self.rejected(["private"] * 1000,
            {"type": "array", "maxItems": 1000, "items": {"type": "integer"}})
        self.assertEqual(len(details["errors"]), 4)
        self.assertTrue(details["truncated"])
        long_valid_prefix = [1] * 1000 + ["private"]
        details = self.rejected(long_valid_prefix,
            {"type": "array", "maxItems": 1001, "items": {"type": "integer"}})
        self.assertTrue(details["truncated"])
        self.assertLess(len(json.dumps(details)), 1500)

    def test_depth_guard_remains_tool_schema_not_a_relaxed_acceptance(self):
        schema, value = {"type": "integer"}, 1
        for _ in range(14):
            schema, value = obj({"nested": schema}), {"nested": value}
        with self.assertRaises(RuntimeFailure) as captured:
            validate_arguments(value, schema)
        self.assertEqual(captured.exception.code, "tool_schema")
        self.assertEqual(captured.exception.details, {})

    def test_current_dialogue_registry_still_validates_and_reports_only_registered_fields(self):
        schema = group_schema(GROUPS["travel_schedule"][1])
        validate_arguments({"action": "read_schedule", "date": None, "day": 2}, schema)
        details = self.rejected({"action": "read_schedule", "date": "private-date", "day": None}, schema)
        self.assertEqual(details["errors"][0]["path"], ["date"])
        self.assertNotIn("private-date", json.dumps(details))

    def test_projection_rejects_unsealed_unknown_mutated_or_wrong_code_details(self):
        unknown = RuntimeFailure("tool_arguments")
        unknown.details = {"schema_version": ARGUMENT_DIAGNOSTIC_VERSION,
            "errors": [{"path": ["unknown_secret_name"], "reason": "missing_required"}], "truncated": False}
        self.assertIsNone(tool_argument_diagnostics(unknown))
        with self.assertRaises(RuntimeFailure) as captured:
            validate_arguments({"count": None}, obj({"count": {"type": "integer"}}))
        original = tool_argument_diagnostics(captured.exception)
        self.assertEqual(original["errors"][0]["path"], ["count"])
        original["errors"][0]["path"].append("independent-return-copy")
        self.assertEqual(tool_argument_diagnostics(captured.exception)["errors"][0]["path"], ["count"])
        captured.exception.details["errors"][0]["path"] = ["unknown_secret_name"]
        self.assertIsNone(tool_argument_diagnostics(captured.exception))
        captured.exception.code = "tool_failed"
        self.assertIsNone(tool_argument_diagnostics(captured.exception))


if __name__ == "__main__":
    unittest.main()
