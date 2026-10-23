"""Synthetic source transitions only; no model, business API or private store."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tests.test_echomind_travel_acceptance_plan import fixture
from tools.echomind_travel_acceptance_plan import (
    FIXED_SOURCES, SOURCE_TREES, canonical, digest, source_hashes,
)
from tools.echomind_travel_trial_prepare import (
    MODULES, TESTS, EvaluationError, prepare, transition, verify_preparation,
)

BACKEND = Path(__file__).resolve().parents[1]


class ControlledPreparationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.root = self.folder / "repository"
        self.root.mkdir()
        for path in SOURCE_TREES:
            (self.root / path).mkdir(parents=True, exist_ok=True)
        for relative in FIXED_SOURCES:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("controlled source\n")
        self.plan = self.root / "backend/evaluations/plan.json"
        self.plan.parent.mkdir(parents=True)
        old = self.plan.parent / "echomind-travel-v1.json"
        old.write_bytes(b"{}\n")
        self.plan.write_bytes(canonical(fixture()))
        self.parent = self.folder / "parent.json"
        self.parent.write_bytes(canonical({
            "schema_version": "echomind-travel-offline-freeze-v1", "baseline_commit": "a" * 40,
            "preparation": "offline_plan_only",
            "authorization": {"real_model_calls": 0, "real_amap_calls": 0, "automatic_retry": False},
            "manifest_path": "backend/evaluations/plan.json", "manifest_sha256": digest(self.plan.read_bytes()),
            "source_files": source_hashes(self.root), "real_model_calls": 0, "real_amap_calls": 0,
            "live_ready": False, "full_payload_frozen": False,
        }))
        self.parent_hash = digest(self.parent.read_bytes())
        for relative in (*MODULES, *TESTS):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# controlled acceptance-only addition\n")
        self.capture = self.folder / "capture.json"
        self.capture_value = {"schema_version": "echomind-travel-initial-capture-v1",
                              "mode": "controlled_capture", "operation_id": "m01",
                              "payload_sha256": "a" * 64, "real_model_calls": 0,
                              "real_amap_calls": 0, "upstream_sends": 0,
                              "captured_request_count": 1, "full_live_payload_frozen": False}
        self.capture.write_bytes(canonical(self.capture_value))
        self.evidence_root = self.folder / "old-evidence"
        self.evidence_root.mkdir()
        self.old = self.evidence_root / "old-calls.jsonl"
        self.old.write_bytes(b"old controlled ledger, do not mutate\n")
        self.protected = self.folder / "protected.json"
        self.protected.write_bytes(canonical({"old_ledgers": {self.old.name: digest(self.old.read_bytes())}}))
        self.output = self.folder / "execution.json"

    def options(self) -> dict:
        return {"root": self.root, "expected_parent": self.parent_hash, "evidence_root": self.evidence_root}

    def create(self) -> dict:
        return prepare(self.parent, self.output, self.capture, self.protected, **self.options())

    def verify(self) -> dict:
        return verify_preparation(self.parent, self.output, self.capture, self.protected, **self.options())

    def test_exact_additions_leave_parent_immutable_and_never_grant_calls(self) -> None:
        before = self.parent.read_bytes(), self.old.read_bytes()
        result = self.create()
        self.assertTrue(result["verified"])
        self.assertEqual(result["added_source_files"], list(MODULES))
        self.assertEqual(result["mode"], "controlled_only")
        self.assertEqual(result["real_authorized_calls"], 0)
        self.assertFalse(result["live_ready"])
        self.assertFalse(result["full_live_payload_frozen"])
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        self.assertEqual(before, (self.parent.read_bytes(), self.old.read_bytes()))
        self.assertEqual(result, self.verify())

    def test_old_business_source_changed_is_not_an_allowed_continuation(self) -> None:
        (self.root / "backend/main.py").write_text("# changed business\n")
        with self.assertRaisesRegex(EvaluationError, "frozen_business_source_changed"):
            self.create()
        self.assertFalse(self.output.exists())

    def test_unlisted_addition_or_missing_expected_tool_is_rejected(self) -> None:
        extra = self.root / "backend/tools/unknown.py"
        extra.write_text("# unrelated\n")
        with self.assertRaisesRegex(EvaluationError, "unexpected_acceptance_source_set"):
            self.create()
        extra.unlink()
        (self.root / MODULES[0]).unlink()
        with self.assertRaisesRegex(EvaluationError, "unexpected_acceptance_source_set"):
            self.create()
        self.assertFalse(self.output.exists())

    def test_parent_or_frozen_plan_edits_are_not_silently_rehashed(self) -> None:
        original = self.parent.read_bytes()
        self.parent.write_bytes(original + b" ")
        with self.assertRaisesRegex(EvaluationError, "parent_protocol_changed"):
            self.create()
        self.parent.write_bytes(original)
        self.plan.write_bytes(self.plan.read_bytes() + b" ")
        with self.assertRaisesRegex(EvaluationError, "frozen_plan_changed"):
            self.create()

    def test_effective_agent_timeout_is_frozen_without_overwriting_configuration(self) -> None:
        for raw in ("20", "NaN", "PRIVATE_VALUE_DO_NOT_LOG"):
            with patch.dict("os.environ", {"ECHOMIND_LLM_TIMEOUT_S": raw}):
                with self.assertRaisesRegex(EvaluationError, "nondefault_agent_timeout_not_frozen") as found:
                    self.create()
                self.assertNotIn(raw, str(found.exception))
                self.assertFalse(self.output.exists())
        self.assertEqual(self.create()["real_model_calls"], 0)

    def test_execution_snapshot_rejects_added_tool_or_test_changes(self) -> None:
        self.create()
        for relative in (MODULES[0], TESTS[0]):
            path = self.root / relative
            original = path.read_bytes()
            path.write_bytes(original + b"# drift\n")
            with self.subTest(relative=relative), self.assertRaisesRegex(EvaluationError, "preparation_changed"):
                self.verify()
            path.write_bytes(original)
        self.assertTrue(self.verify()["verified"])

    def test_no_overwrite_or_fake_live_readiness(self) -> None:
        self.create()
        original = self.output.read_bytes()
        with self.assertRaisesRegex(EvaluationError, "preparation_already_exists"):
            self.create()
        self.assertEqual(self.output.read_bytes(), original)
        value = json.loads(original)
        value["live_ready"] = True
        self.output.write_bytes(canonical(value))
        with self.assertRaisesRegex(EvaluationError, "preparation_changed"):
            self.verify()

    def test_capture_must_be_controlled_zero_call_and_single_initial_request(self) -> None:
        for key, invalid in (("mode", "live"), ("real_model_calls", 1),
                             ("real_model_calls", False), ("captured_request_count", 2),
                             ("upstream_sends", 1), ("full_live_payload_frozen", True),
                             ("payload_sha256", "not a hash")):
            value = copy.deepcopy(self.capture_value)
            value[key] = invalid
            self.capture.write_bytes(canonical(value))
            with self.subTest(key=key), self.assertRaises(EvaluationError):
                self.create()
            self.assertFalse(self.output.exists())

    def test_old_ledger_drift_or_missing_old_references_block_preparation(self) -> None:
        self.old.write_bytes(self.old.read_bytes() + b"changed")
        with self.assertRaisesRegex(EvaluationError, "historical_ledger_changed"):
            self.create()
        self.protected.write_bytes(canonical({"old_ledgers": {}}))
        with self.assertRaisesRegex(EvaluationError, "historical_ledger_references_required"):
            self.create()

    def test_path_escape_nonfinite_duplicate_keys_and_links_fail_closed(self) -> None:
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}'):
            self.capture.write_bytes(raw)
            with self.assertRaises(EvaluationError):
                self.create()
        self.capture.write_bytes(canonical(self.capture_value))
        self.protected.write_bytes(canonical({"old_ledgers": {"../secret": "a" * 64}}))
        with self.assertRaisesRegex(EvaluationError, "unsafe_relative_path"):
            self.create()
        self.protected.write_bytes(canonical({"old_ledgers": {self.old.name: digest(self.old.read_bytes())}}))
        self.output.symlink_to(self.folder / "other.json")
        with self.assertRaises(EvaluationError):
            self.create()

    def test_cli_failure_has_no_network_no_secret_output_or_live_switch(self) -> None:
        script = """
import socket,sys
def deny(*a,**k): raise AssertionError('network forbidden')
socket.socket=deny
from tools import echomind_travel_trial_prepare as tool
tool.main()
"""
        bad = self.folder / "bad-parent.json"
        bad.write_text('{"private":"DO_NOT_PRINT_THIS"}')
        result = subprocess.run([sys.executable, "-B", "-c", script, "transition", "--parent", str(bad)],
                                cwd=BACKEND, text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertIn("parent_protocol_changed", result.stderr)
        self.assertNotIn("DO_NOT_PRINT_THIS", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
