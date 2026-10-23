"""Offline additive freeze/authorization checks; no credential or live API."""
from __future__ import annotations

import copy
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from tools import echomind_travel_live_protocol as tool
from tools.echomind_travel_acceptance_plan import FIXED_SOURCES, SOURCE_TREES, canonical, source_hashes


class LiveProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.root = self.folder / "repository"
        self.root.mkdir()
        for name in SOURCE_TREES:
            (self.root / name).mkdir(parents=True, exist_ok=True)
        for name in FIXED_SOURCES:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("original controlled source\n")
        self.plan = self.root / tool.PLAN
        self.plan.parent.mkdir(parents=True)
        self.plan.write_bytes(b'{"preparation":"offline"}\n')
        old_sources = source_hashes(self.root)
        old_tests = {}
        for name in ("capture", "ledger", "prepare", "transport"):
            path = self.root / f"backend/tests/test_echomind_travel_trial_{name}.py"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"# old controlled tests\n")
            old_tests[path.relative_to(self.root).as_posix()] = tool.digest(path.read_bytes())
        self.evidence = self.folder / "evidence"
        self.evidence.mkdir(mode=0o700)
        self.old = self.evidence / "old-ledger.jsonl"
        self.old.write_bytes(b"historical stopped allowance\n")
        ledgers = {self.old.name: tool.digest(self.old.read_bytes())}
        self.protected = self.evidence / "protected.json"
        self.protected.write_bytes(canonical({"old_ledgers": ledgers}))
        self.parent = self.evidence / "parent.json"
        self.parent.write_bytes(canonical({"mode": "controlled_only", "parent_protocol_sha256": "a" * 64,
            "plan_manifest_sha256": tool.digest(self.plan.read_bytes()), "source_files": old_sources,
            "test_files": old_tests, "real_authorized_calls": 0, "real_model_calls": 0,
            "real_amap_calls": 0, "live_ready": False, "full_live_payload_frozen": False,
            "prior_ledgers_sha256": tool.digest(canonical(ledgers))}))
        for name in (*tool.MODULES, *tool.TESTS):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"# new acceptance-only source\n")
        for name, value in {"ROOT": self.root, "EVIDENCE_ROOT": self.evidence,
                            "PARENT_PROTOCOL_SHA256": "a" * 64,
                            "PARENT_EXECUTION_SHA256": tool.digest(self.parent.read_bytes()),
                            "PLAN_SHA256": tool.digest(self.plan.read_bytes()),
                            "BASE_SOURCE_COUNT": len(old_sources), "OLD_LEDGER_COUNT": 1}.items():
            self.enterContext(patch.object(tool, name, value))
        self.enterContext(patch.object(tool, "_commit", return_value="b" * 40))
        self.output = self.evidence / "live-protocol.json"
        self.options = {"settings_summary": tool.trusted_settings(
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"),
            "fixture_binding": {"workspace_sha256": "c" * 64}, "protected_path": self.protected,
            "parent_execution": self.parent, "fixture_summary": {"private_input": "NOT_PERSISTED"}}

    def freeze(self):
        return tool.freeze(self.output, **self.options)

    def verify(self):
        return tool.verify(self.output, **self.options)

    def test_freeze_is_additive_explicit_and_private_not_sending(self):
        before = self.parent.read_bytes(), self.old.read_bytes()
        result = self.freeze()
        self.assertTrue(result["verified"])
        self.assertEqual(result["max_model_sends"], 21)
        self.assertEqual(result["real_model_calls"], 0)
        self.assertEqual(result, self.verify())
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("NOT_PERSISTED", self.output.read_text())
        self.assertEqual(before, (self.parent.read_bytes(), self.old.read_bytes()))
        value = tool._document(self.output)
        self.assertEqual(value["authorization"]["user_statement"], "可以，那就开始做真实验收吧")
        self.assertFalse(value["product_cutover"])

    def test_zero_or_expanded_or_bool_authorization_is_rejected(self):
        for key, value in (("max_model_sends", 0), ("max_model_sends", 22), ("real_amap_calls", True),
                           ("user_statement", "historical authorization")):
            auth = copy.deepcopy(tool.AUTHORIZATION)
            auth[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(tool.EvaluationError, "authorization"):
                tool.freeze(self.output, **self.options, authorization=auth)
            self.assertFalse(self.output.exists())

    def test_parent_or_plan_drift_stops_without_creating_new_protocol(self):
        for path, message in ((self.parent, "parent_execution_changed"), (self.plan, "frozen_plan_changed")):
            raw = path.read_bytes()
            path.write_bytes(raw + b" ")
            with self.subTest(path=path.name), self.assertRaisesRegex(tool.EvaluationError, message):
                self.freeze()
            path.write_bytes(raw)
            self.assertFalse(self.output.exists())

    def test_old_sources_and_tests_cannot_be_rehashed_into_new_acceptance(self):
        for path, message in ((self.root / "backend/main.py", "frozen_source_changed"),
                              (self.root / "backend/tests/test_echomind_travel_trial_capture.py", "frozen_test_changed")):
            raw = path.read_bytes()
            path.write_bytes(raw + b"# change\n")
            with self.subTest(path=path.name), self.assertRaisesRegex(tool.EvaluationError, message):
                self.freeze()
            path.write_bytes(raw)
            self.assertFalse(self.output.exists())

    def test_unknown_addition_missing_module_and_postfreeze_new_code_drift(self):
        extra = self.root / "backend/tools/unreviewed.py"
        extra.write_bytes(b"# extra")
        with self.assertRaisesRegex(tool.EvaluationError, "unexpected_source_additions"):
            self.freeze()
        extra.unlink()
        path = self.root / tool.MODULES[2]
        raw = path.read_bytes()
        path.unlink()
        with self.assertRaisesRegex(tool.EvaluationError, "unexpected_source_additions"):
            self.freeze()
        path.write_bytes(raw)
        self.freeze()
        for name in (tool.MODULES[1], tool.TESTS[1]):
            path = self.root / name
            raw = path.read_bytes()
            path.write_bytes(raw + b"# drift")
            with self.subTest(name=name), self.assertRaisesRegex(tool.EvaluationError, "protocol_or_configuration_changed"):
                self.verify()
            path.write_bytes(raw)

    def test_settings_parameters_binding_and_fixture_snapshot_drift(self):
        self.freeze()
        for key, value in (("max_tokens", 1024), ("temperature", 0), ("stream", True),
                           ("agent_call_seconds", 20), ("endpoint_sha256", "d" * 64)):
            settings = self.options["settings_summary"] | {key: value}
            with self.subTest(key=key), self.assertRaises(tool.EvaluationError):
                tool.verify(self.output, **(self.options | {"settings_summary": settings}))
        with self.assertRaises(tool.EvaluationError):
            tool.verify(self.output, **(self.options | {"fixture_binding": {"workspace_sha256": "d" * 64}}))
        with self.assertRaises(tool.EvaluationError):
            tool.verify(self.output, **(self.options | {"fixture_summary": {"changed": True}}))
        with self.assertRaises(tool.EvaluationError):
            tool.freeze(self.evidence / "bad.json", **(self.options | {"fixture_binding": {"secret": "not a hash"}}))

    def test_old_ledger_drift_and_reference_substitution_refused(self):
        self.freeze()
        self.old.write_bytes(self.old.read_bytes() + b"new bytes")
        with self.assertRaisesRegex(tool.EvaluationError, "historical_ledger_changed"):
            self.verify()
        self.protected.write_bytes(canonical({"old_ledgers": {"replacement.jsonl": "e" * 64}}))
        with self.assertRaisesRegex(tool.EvaluationError, "historical_ledger_set_changed"):
            self.verify()

    def test_overwrite_symlink_unsafe_directory_and_invalid_json_refused(self):
        self.freeze()
        old = self.output.read_bytes()
        with self.assertRaisesRegex(tool.EvaluationError, "protocol_must_be_new"):
            self.freeze()
        self.assertEqual(old, self.output.read_bytes())
        other = self.evidence / "linked.json"
        other.symlink_to(self.output)
        with self.assertRaises(tool.EvaluationError):
            tool.freeze(other, **self.options)
        self.output.write_bytes(b'{"a":1,"a":2}')
        with self.assertRaises(tool.EvaluationError):
            self.verify()
        self.evidence.chmod(0o755)
        with self.assertRaises(tool.EvaluationError):
            tool.freeze(self.evidence / "unsafe.json", **self.options)

    def test_official_endpoint_matrix_hashes_only_no_url_fallback(self):
        for host in ("dashscope.aliyuncs.com", "workspace-7.cn-beijing.maas.aliyuncs.com"):
            endpoint = f"https://{host}/compatible-mode/v1/chat/completions"
            value = tool.trusted_settings(endpoint)
            self.assertEqual(value["endpoint_sha256"], tool.digest(endpoint.encode()))
            self.assertNotIn(host, str(value))
        for endpoint in ("http://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                         "https://workspace.cn-shanghai.maas.aliyuncs.com/compatible-mode/v1/chat/completions",
                         "https://dashscope.aliyuncs.com:443/compatible-mode/v1/chat/completions",
                         "https://u:p@dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                         "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions?token=x",
                         "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions#fragment",
                         "https://dashscope.aliyuncs.com.evil.test/compatible-mode/v1/chat/completions",
                         "https://dashscope.aliyuncs.com/compatible-mode/v1", None):
            with self.subTest(endpoint=endpoint), self.assertRaises(tool.EvaluationError):
                tool.trusted_settings(endpoint)

    def test_verify_rejects_private_receipt_permission_or_link_drift(self):
        self.freeze()
        self.output.chmod(0o644)
        with self.assertRaisesRegex(tool.EvaluationError, "private_external_output_required"):
            self.verify()
        self.output.chmod(0o600)
        linked = self.evidence / "hardlink.json"
        linked.hardlink_to(self.output)
        with self.assertRaisesRegex(tool.EvaluationError, "private_external_output_required"):
            self.verify()

    def test_full_sixteen_historical_paths_project_to_valid_stable_journal_labels(self):
        self.freeze()
        value = tool._document(self.output)
        paths = (
            "7a-1/batch-20261014-v1/calls.jsonl", "media-1/calls.jsonl",
            "media-1/fixed-validation-20261016/calls.jsonl", "media-1/conservative-validation/calls.jsonl",
            "echomind-migration/live-20261022-v1/gate/calls.jsonl", "6d-2/calls.jsonl",
            "6d-2/plus-full-regression/batch-20261014-v1/calls.jsonl", "6d-2/ab-trial/calls.jsonl",
            "6d-2/plus-gap-validation/calls.jsonl", "6d-2/b-candidate-validation/calls.jsonl",
            "6d-2/fix/calls.jsonl", "6d-2/model-selection/calls.jsonl",
            "release-1b/batch-20261018/attempts.jsonl", "release-1b/batch-20261018/repair-c02/attempts.jsonl",
            "chat-acceptance/batch-20261021-v1/calls.jsonl", "media-2b/batch-20261016/calls.jsonl",
        )
        value["prior_ledger_hashes"] = {path: "e" * 64 for path in paths}
        before = canonical(value)
        sha = tool.digest(before + b"\n")
        from tools.echomind_travel_trial_ledger import _binding
        with patch.object(tool, "OLD_LEDGER_COUNT", 16):
            result = tool.ledger_binding(value, sha)
            self.assertEqual(len(result["prior_ledger_hashes"]), 16)
            self.assertTrue(all(len(key) == 47 and "/" not in key for key in result["prior_ledger_hashes"]))
            self.assertEqual(result["execution_sha256"], sha)
            self.assertEqual(result["grant_sha256"], tool.digest(canonical(tool.AUTHORIZATION)))
            self.assertEqual(_binding({key: result[key] for key in (
                "plan_protocol_sha256", "execution_sha256", "prior_ledger_hashes")})["prior_ledger_hashes"],
                result["prior_ledger_hashes"])
            reordered = copy.deepcopy(value)
            reordered["prior_ledger_hashes"] = dict(reversed(tuple(value["prior_ledger_hashes"].items())))
            self.assertEqual(tool.ledger_binding(reordered, sha), result)
            renamed = copy.deepcopy(value)
            renamed["prior_ledger_hashes"]["media-2b/different/calls.jsonl"] = renamed["prior_ledger_hashes"].pop(paths[-1])
            changed = tool.ledger_binding(renamed, tool.digest(canonical(renamed) + b"\n"))
            self.assertNotEqual(changed["prior_ledger_hashes"], result["prior_ledger_hashes"])
        self.assertEqual(canonical(value), before)

    def test_ledger_projection_rejects_wrong_hash_grant_parent_paths_and_values(self):
        self.freeze()
        value = tool._document(self.output)
        with self.assertRaisesRegex(tool.EvaluationError, "invalid_ledger_binding_projection"):
            tool.ledger_binding(value, "f" * 64)
        for key, invalid in (("authorization", {"max_model_sends": 22}),
                             ("parent_protocol_sha256", "e" * 64),
                             ("prior_ledger_hashes", {}),
                             ("prior_ledger_hashes", {"calls.jsonl": "bad"})):
            changed = value | {key: invalid}
            with self.subTest(key=key), self.assertRaises(tool.EvaluationError):
                tool.ledger_binding(changed, tool.digest(canonical(changed) + b"\n"))
        changed = value | {"prior_ledger_hashes": {"../calls.jsonl": "e" * 64}}
        with self.assertRaises(tool.EvaluationError):
            tool.ledger_binding(changed, tool.digest(canonical(changed) + b"\n"))
        self.assertEqual(tool.ledger_binding(value, tool.digest(canonical(value) + b"\n"))["execution_sha256"],
                         tool.digest(self.output.read_bytes()))


if __name__ == "__main__":
    unittest.main()
