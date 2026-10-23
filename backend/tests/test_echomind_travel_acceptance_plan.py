"""Offline preparation only; synthetic roots, no providers/services/credentials."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from tools.echomind_travel_acceptance_plan import (
    FIXED_SOURCES, PRIOR_MANIFEST, SOURCE_TREES, EvaluationError, Manifest,
    canonical, digest, freeze, load, model_input, source_hashes, verify,
)

BACKEND = Path(__file__).resolve().parents[1]


def fixture() -> dict:
    groups = ("clarification", "requirements", "day", "budget", "preview")
    return {
        "schema_version": "echomind-travel-acceptance-plan-v1", "baseline_commit": "a" * 40,
        "preparation": "offline_plan_only",
        "authorization": {"real_model_calls": 0, "real_amap_calls": 0, "automatic_retry": False},
        "runtime": {"model": "qwen3.7-flash-2026-07-15", "mode": "direct_tools",
                    "max_tokens": 900, "temperature": 0.1, "stream": False,
                    "enable_thinking": False, "parallel_tool_calls": False,
                    "task_model_limit": 3, "task_tool_limit": 4, "task_seconds": 45,
                    "active_product_cutover": False},
        "prior_manifest": {"path": PRIOR_MANIFEST, "sha256": digest(b"{}\n")},
        "fixtures": [{"id": "synthetic", "specification": ["Synthetic prerequisite, not live state."]}],
        "operations": [{"id": f"op{index:02}", "group": groups[index % len(groups)],
                        "branch": "native_candidate" if index < 7 else "local_confirmation",
                        "fixture_id": "synthetic", "depends_on": [f"op{index-1:02}"] if index else [],
                        "text": f"User message {index}", "expected": ["HIDDEN_REVIEW"],
                        "blocking": ["HIDDEN_BLOCKER"],
                        "allowed_tools": ["inspect_trip"] if index < 7 else [],
                        "expected_action": None if index < 7 else "confirm_requirements"} for index in range(14)],
        "limits": {"planned_native_tasks": 7, "planned_local_submissions": 7,
                   "recommended_model_send_cap": 21, "expected_model_sends": 14, "real_amap_calls": 0},
        "live_prerequisites": ["New explicit authorization and actual bound payload required."],
    }


class AcceptancePlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve()
        self.root = self.directory / "repository"
        self.root.mkdir()
        for relative in SOURCE_TREES:
            (self.root / relative).mkdir(parents=True, exist_ok=True)
        for relative in FIXED_SOURCES:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic source\n")
        self.prior = self.root / PRIOR_MANIFEST
        self.prior.parent.mkdir(parents=True, exist_ok=True)
        self.prior.write_bytes(b"{}\n")
        self.path = self.root / "backend/evaluations/plan.json"
        self.value = fixture()
        self.write()
        self.protocol = self.directory / "protocol.json"

    def write(self) -> None:
        self.path.write_bytes(canonical(self.value))

    def test_fixed_matrix_has_zero_authority_and_separate_costs(self) -> None:
        result = verify(self.path, root=self.root)
        self.assertEqual(result["operation_count"], 14)
        self.assertEqual(result["limits"]["expected_model_sends"], 14)
        self.assertEqual(result["limits"]["recommended_model_send_cap"], 21)
        self.assertFalse(result["live_ready"])
        self.assertFalse(result["full_payload_frozen"])
        self.assertEqual((result["real_model_calls"], result["real_amap_calls"]), (0, 0))

    def test_projection_contains_only_unmodified_business_text(self) -> None:
        manifest = load(self.path)
        operation = manifest.operations[0]
        result = model_input(operation)
        self.assertEqual(result, {"text": operation.text})
        raw = canonical(result).decode()
        for hidden in ("HIDDEN_REVIEW", "HIDDEN_BLOCKER", "synthetic", operation.id,
                       "expected_action", "allowed_tools", "fixture_id"):
            self.assertNotIn(hidden, raw)
        result["text"] = "Changed caller copy"
        self.assertEqual(operation.text, "User message 0")
        with self.assertRaises(EvaluationError):
            model_input({"text": "not a validated operation"})

    def test_authorization_cannot_be_raised_or_coerced(self) -> None:
        for key, value in (("real_model_calls", 1), ("real_amap_calls", 1),
                           ("automatic_retry", True), ("real_model_calls", False),
                           ("real_amap_calls", 0.0), ("automatic_retry", 0)):
            changed = copy.deepcopy(self.value)
            changed["authorization"][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValidationError):
                Manifest.model_validate(changed)

    def test_runtime_and_limits_exact_types_and_values_are_not_relaxed(self) -> None:
        for group, key, value in (("runtime", "max_tokens", 2048),
                                  ("runtime", "task_model_limit", 3.0),
                                  ("runtime", "stream", 0), ("runtime", "temperature", 1),
                                  ("limits", "planned_native_tasks", 8),
                                  ("limits", "real_amap_calls", False),
                                  ("limits", "recommended_model_send_cap", 20)):
            changed = copy.deepcopy(self.value)
            changed[group][key] = value
            with self.subTest(key=key), self.assertRaises(ValidationError):
                Manifest.model_validate(changed)

    def test_invalid_identity_dependency_branch_and_matrix_fail(self) -> None:
        mutations = (
            lambda v: v["operations"][1].update(id="op00"),
            lambda v: v["operations"][0].update(depends_on=["op01"]),
            lambda v: v["operations"][0].update(depends_on=["op00"]),
            lambda v: v["operations"][1].update(depends_on=["op00", "op00"]),
            lambda v: v["operations"][0].update(fixture_id="missing"),
            lambda v: v["operations"][0].update(allowed_tools=[]),
            lambda v: v["operations"][8].update(allowed_tools=["inspect_trip"]),
            lambda v: v["operations"][0].update(allowed_tools=["arbitrary_valid_name"]),
            lambda v: v["operations"][8].update(expected_action=None),
            lambda v: v["operations"].pop(),
            lambda v: v["fixtures"].append(v["fixtures"][0]),
        )
        for number, change in enumerate(mutations):
            value = copy.deepcopy(self.value)
            change(value)
            with self.subTest(number=number), self.assertRaises(ValidationError):
                Manifest.model_validate(value)

    def test_unknown_fields_duplicates_nonfinite_and_oversized_input_fail_safely(self) -> None:
        for segments in ((), ("authorization",), ("runtime",), ("limits",),
                         ("fixtures", 0), ("operations", 0)):
            value = copy.deepcopy(self.value)
            target = value
            for segment in segments:
                target = target[segment]
            target["PRIVATE_FIELD_DO_NOT_LOG"] = "PRIVATE_VALUE_DO_NOT_LOG"
            self.path.write_bytes(canonical(value))
            with self.subTest(segments=segments), self.assertRaises(EvaluationError) as found:
                load(self.path)
            self.assertNotIn("PRIVATE", str(found.exception))
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b" " * (128 * 1024 + 1)):
            self.path.write_bytes(raw)
            with self.assertRaises(EvaluationError):
                load(self.path)

    def test_freeze_is_exclusive_private_and_recheck_is_read_only(self) -> None:
        before = self.path.read_bytes()
        result = freeze(self.protocol, self.path, root=self.root)
        self.assertFalse(result["protocol_verified"])
        self.assertEqual(stat.S_IMODE(self.protocol.stat().st_mode), 0o600)
        original = self.protocol.read_bytes()
        checked = verify(self.path, root=self.root, protocol_path=self.protocol)
        self.assertTrue(checked["protocol_verified"])
        self.assertEqual(checked["protocol_sha256"], digest(original))
        self.assertEqual(self.path.read_bytes(), before)
        with self.assertRaisesRegex(EvaluationError, "^protocol_already_exists$"):
            freeze(self.protocol, self.path, root=self.root)
        self.assertEqual(self.protocol.read_bytes(), original)

    def test_source_add_change_and_delete_invalidate_frozen_closure(self) -> None:
        freeze(self.protocol, self.path, root=self.root)
        target = self.root / "backend/tools/new_source.py"
        target.write_text("# changed closure\n")
        with self.assertRaisesRegex(EvaluationError, "frozen_manifest_or_source_changed"):
            verify(self.path, root=self.root, protocol_path=self.protocol)
        target.unlink()
        fixed = self.root / FIXED_SOURCES[0]
        original = fixed.read_bytes()
        fixed.write_bytes(original + b"changed\n")
        with self.assertRaises(EvaluationError):
            verify(self.path, root=self.root, protocol_path=self.protocol)
        fixed.write_bytes(original)
        fixed.unlink()
        with self.assertRaises(EvaluationError):
            verify(self.path, root=self.root, protocol_path=self.protocol)

    def test_manifest_review_and_prior_manifest_changes_are_detected(self) -> None:
        freeze(self.protocol, self.path, root=self.root)
        self.value["operations"][0]["expected"] = ["Different review"]
        self.write()
        with self.assertRaisesRegex(EvaluationError, "frozen_manifest_or_source_changed"):
            verify(self.path, root=self.root, protocol_path=self.protocol)
        self.value = fixture()
        self.write()
        self.prior.write_bytes(b"{\"changed\":true}")
        with self.assertRaisesRegex(EvaluationError, "prior_manifest_changed"):
            verify(self.path, root=self.root, protocol_path=self.protocol)

    def test_protocol_paths_and_authorization_tampering_are_rejected(self) -> None:
        freeze(self.protocol, self.path, root=self.root)
        original = json.loads(self.protocol.read_bytes())
        for name in ("../secret", "/absolute", "backend/../secret", "backend//secret", "backend\\secret"):
            changed = copy.deepcopy(original)
            changed["source_files"][name] = "a" * 64
            self.protocol.write_bytes(canonical(changed))
            with self.subTest(name=name), self.assertRaises(EvaluationError):
                verify(self.path, root=self.root, protocol_path=self.protocol)
        for key, value in (("real_model_calls", 1), ("real_amap_calls", False), ("live_ready", 0),
                           ("full_payload_frozen", True)):
            changed = copy.deepcopy(original)
            changed[key] = value
            self.protocol.write_bytes(canonical(changed))
            with self.subTest(key=key), self.assertRaises(EvaluationError):
                verify(self.path, root=self.root, protocol_path=self.protocol)

    def test_symlinks_hardlinks_and_output_escape_are_refused(self) -> None:
        target = self.root / "backend/tools/linked.py"
        target.symlink_to(self.path)
        with self.assertRaisesRegex(EvaluationError, "source_symlink"):
            source_hashes(self.root)
        target.unlink()
        os.link(self.path, target)
        with self.assertRaises(EvaluationError):
            source_hashes(self.root)
        target.unlink()
        output = self.directory / "linked-output"
        output.symlink_to(self.protocol)
        for path in (output, Path("relative-protocol.json"), self.directory / ".." / "escape.json"):
            with self.subTest(path=str(path)), self.assertRaises(EvaluationError):
                freeze(path, self.path, root=self.root)
        with self.assertRaisesRegex(EvaluationError, "manifest_outside_repository"):
            verify(self.directory / "outside.json", root=self.root)

    def test_size_limits_are_fail_closed_without_truncation(self) -> None:
        with patch("tools.echomind_travel_acceptance_plan.MAX_FILE", 2), self.assertRaises(EvaluationError):
            source_hashes(self.root)
        with patch("tools.echomind_travel_acceptance_plan.MAX_TOTAL", 2), self.assertRaises(EvaluationError):
            source_hashes(self.root)
        self.assertFalse(self.protocol.exists())

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO requires POSIX")
    def test_fifo_source_is_rejected_without_blocking_open(self) -> None:
        path = self.root / "backend/tools/not_regular.py"
        os.mkfifo(path)
        script = """
from pathlib import Path
import sys
from tools.echomind_travel_acceptance_plan import EvaluationError, source_hashes
try:
    source_hashes(Path(sys.argv[1]))
except EvaluationError as error:
    print(str(error))
else:
    raise AssertionError('FIFO must not be accepted')
"""
        result = subprocess.run([sys.executable, "-B", "-c", script, str(self.root)], cwd=BACKEND,
                                capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "unsafe_or_oversized_file")

    def test_entrypoint_and_knowledge_corpus_belong_to_source_closure(self) -> None:
        corpus = self.root / "backend/knowledge/corpus.json"
        corpus.write_text('{"synthetic":"only"}')
        hashes = source_hashes(self.root)
        self.assertIn("backend/main.py", hashes)
        self.assertIn("backend/knowledge/corpus.json", hashes)
        freeze(self.protocol, self.path, root=self.root)
        corpus.write_text('{"synthetic":"changed"}')
        with self.assertRaisesRegex(EvaluationError, "frozen_manifest_or_source_changed"):
            verify(self.path, root=self.root, protocol_path=self.protocol)

    def test_cli_verify_and_freeze_remain_offline_with_socket_creation_denied(self) -> None:
        # The root override is Python-only test injection; CLI exposes no root,
        # URL, model, allow-real or sender option.
        script = """
import socket, sys
from pathlib import Path
def deny(*args, **kwargs):
    raise AssertionError('network forbidden')
socket.socket = deny
socket.create_connection = deny
from tools import echomind_travel_acceptance_plan as tool
real_verify, real_freeze = tool.verify, tool.freeze
root = Path(sys.argv.pop(1))
def checked_verify(manifest, **kw):
    kw['root'] = root
    return real_verify(manifest, **kw)
tool.verify = checked_verify
tool.freeze = lambda output, manifest: real_freeze(output, manifest, root=root)
tool.main()
"""
        for command in (("verify", "--manifest", str(self.path)),
                        ("freeze", "--manifest", str(self.path), "--output", str(self.protocol)),
                        ("verify", "--manifest", str(self.path), "--protocol", str(self.protocol))):
            result = subprocess.run([sys.executable, "-B", "-c", script, str(self.root), *command],
                                    cwd=BACKEND, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            self.assertEqual((output["real_model_calls"], output["real_amap_calls"]), (0, 0))
            self.assertFalse(output["live_ready"])


if __name__ == "__main__":
    unittest.main()
