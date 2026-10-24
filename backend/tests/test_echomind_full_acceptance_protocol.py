"""Strict offline freeze checks; no credentials, model, AMap or user database."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools import echomind_full_acceptance_protocol as tool
from tools.echomind_travel_acceptance_plan import FIXED_SOURCES, SOURCE_TREES


class FullAcceptanceProtocolTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.root = self.folder / "repository"
        self.root.mkdir()
        (self.root / "backend/tests").mkdir(parents=True)
        for name in SOURCE_TREES:
            (self.root / name).mkdir(parents=True, exist_ok=True)
        for name in FIXED_SOURCES:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"controlled source only\n")
        self.plan = self.root / tool.PLAN
        self.plan.parent.mkdir(parents=True)
        self.plan.write_bytes((tool.ROOT / tool.PLAN).read_bytes())
        self.source = self.root / "backend/echomind_core/travel_package.py"
        (self.root / "backend/tests/controlled.py").write_bytes(b"# synthetic regression\n")
        for name in ("travel_package.py", "travel_domain.py", "provider.py", "embedding.py"):
            (self.source.parent / name).write_bytes(b"# controlled trusted source\n")
        corpus = {"venues": [{"id": "museum_people", "city_id": "310000"}], "documents": [
            {"id": "museum-west", "venue_id": "museum_people", "sections": [{"id": name}
                for name in ("entry", "group", "family", "photo")]},
            {"id": "people-2025", "venue_id": "museum_people", "sections": [{"id": "booking"}]}]}
        (self.root / "backend/knowledge/national-corpus-v2.json").write_bytes(tool.canonical(corpus))
        (self.root / "backend/knowledge/national-sources-v2.json").write_bytes(b"{}\n")
        self.evidence = self.folder / "private-evidence"
        self.evidence.mkdir(mode=0o700)
        self.old = self.evidence / "old-paused.jsonl"
        self.old.write_bytes(b"old immutable paused attempt\n")
        self.old.chmod(0o600)
        self.output = self.evidence / "protocol.json"
        self.options = {"root": self.root, "fixture_binding": {"owner_fixture": "a" * 64,
            "actual_wire_inputs": "b" * 64}, "prior_ledgers": {"previous_paused": self.old},
            "settings_summary": tool.trusted_settings("https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")}
        self.enterContext(patch.object(tool, "_commit", return_value="c" * 40))

    def freeze(self):
        return tool.freeze(self.output, **self.options)

    def verify(self):
        return tool.verify(self.output, **self.options)

    def mutate_manifest(self, callback):
        value = json.loads(self.plan.read_bytes())
        callback(value)
        self.plan.write_bytes(tool.canonical(value))

    def test_fixed_matrix_66_calls_and_distinct_local_vs_real_scope(self):
        manifest = tool.load_manifest(root=self.root)
        self.assertEqual([row["id"] for row in manifest["operations"]], [f"f{index:02}" for index in range(1, 9)])
        self.assertEqual(sum(tool.OPERATION_CAPS.values()), 66)
        self.assertEqual([row["id"] for row in manifest["operations"] if row["channel"] == "chrome"], ["f04", "f05", "f06"])
        self.assertEqual(manifest["fixtures"]["costs"], {"ticket": "", "meal_2026-11-01": "",
            "meal_2026-11-02": "", "accommodation": "", "other": ""})
        self.assertEqual(manifest["authorization"]["real_amap_calls"], 0)
        self.assertEqual(manifest["authorization"]["judge_calls"], 0)

    def test_freeze_verify_private_additive_and_no_sender(self):
        before = self.old.read_bytes()
        first = self.freeze()
        self.assertEqual(first, self.verify())
        self.assertEqual(first["max_model_sends"], 66)
        self.assertEqual(first["real_model_calls"], 0)
        self.assertFalse(first["sender_available"])
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.old.read_bytes(), before)
        self.assertNotIn(str(self.old), self.output.read_text())
        frozen = json.loads(self.output.read_bytes())
        self.assertIn("backend/echomind_core/travel_package.py", frozen["source_files"])
        self.assertIn("backend/tests/controlled.py", frozen["source_files"])
        self.assertIn("backend/knowledge/national-corpus-v2.json", frozen["material_files"])
        with self.assertRaisesRegex(tool.EvaluationError, "must_be_new"):
            self.freeze()

    def test_labels_expectations_and_ids_have_no_model_text_path(self):
        plan = tool.load_manifest(root=self.root)
        for operation in plan["operations"]:
            self.assertEqual(tool.model_input(operation), {"text": operation["text"]})
            altered = deepcopy(operation)
            altered["expected"] = ["PRIVATE_EXPECTATION_DO_NOT_SEND"]
            altered["blocking"] = ["PRIVATE_REVIEW_DO_NOT_SEND"]
            self.assertNotIn("PRIVATE", str(tool.model_input(altered)))
            self.assertEqual(set(tool.model_input(operation)), {"text"})
        with self.assertRaises(tool.EvaluationError):
            tool.model_input({"text": "question", "expected": ["answer"]})

    def test_actual_frontend_local_parser_does_not_intercept_any_evaluation_text(self):
        # Execute the actual production parser (not a duplicate Python regex).
        # Its validator import is unused for these no-match strings; a stub
        # that raises would reveal accidental matching/validator execution.
        node = shutil.which("node")
        typescript = tool.ROOT / "frontend/node_modules/typescript"
        if not node or not typescript.exists():
            self.skipTest("installed frontend Node/TypeScript required for actual parser check")
        script = r"""
const fs = require('node:fs');
const ts = require(process.argv[1]);
const source = fs.readFileSync(process.argv[2], 'utf8');
const out = ts.transpileModule(source, {compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
const moduleValue = {exports:{}};
new Function('require','module','exports',out)((name)=>({isDialogueAction(){throw Error('unexpected local validator');}}), moduleValue, moduleValue.exports);
const rows = JSON.parse(fs.readFileSync(process.argv[3], 'utf8')).operations;
const result = rows.map(row=>({id:row.id, action:moduleValue.exports.localDialogueAction(row.text)}));
process.stdout.write(JSON.stringify(result));
"""
        result = subprocess.run([node, "-e", script, str(typescript),
            str(tool.ROOT / "frontend/src/lib/dialogue-confirmation.ts"), str(self.plan)],
            capture_output=True, text=True, check=False, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertTrue(all(row["action"] is None for row in json.loads(result.stdout)))

    def test_operation_order_caps_channels_and_tool_changes_rejected(self):
        original = self.plan.read_bytes()
        mutations = (
            lambda p: p["operations"].reverse(),
            lambda p: p["operations"][0].update(model_send_cap=7),
            lambda p: p["operations"][0].update(model_send_cap=True),
            lambda p: p["operations"][0].update(channel="chrome"),
            lambda p: p["operations"][0].update(allowed_tools=["arbitrary_tool"]),
            lambda p: p["preparation"][0].update(input_count=11),
            lambda p: p["operations"][0].update(extra="not allowed"),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.plan.write_bytes(original)
                self.mutate_manifest(mutation)
                with self.assertRaises(tool.EvaluationError):
                    self.freeze()
                self.assertFalse(self.output.exists())

    def test_authorization_or_runtime_expansion_rejected_before_output(self):
        original = self.plan.read_bytes()
        mutations = (
            lambda p: p["authorization"].update(batch_model_send_cap=67),
            lambda p: p["authorization"].update(real_amap_calls=True),
            lambda p: p["authorization"].update(automatic_retry=True),
            lambda p: p["runtime"].update(model="another-model"),
            lambda p: p["runtime"]["tiers"]["single"].update(models=7),
            lambda p: p["runtime"]["generation_parameters"]["roles"].update(temperature=.2),
            lambda p: p["fixtures"]["costs"].update(ticket="0"),
        )
        for mutation in mutations:
            self.plan.write_bytes(original)
            self.mutate_manifest(mutation)
            with self.subTest(mutation=mutation), self.assertRaises(tool.EvaluationError):
                self.freeze()
            self.assertFalse(self.output.exists())

    def test_postfreeze_source_manifest_fixture_settings_and_code_drift_rejected(self):
        self.freeze()
        for path in (self.source, self.plan, self.root / "backend/knowledge/national-sources-v2.json",
                     self.root / "backend/tests/controlled.py"):
            original = path.read_bytes()
            path.write_bytes(original + b" \n")
            with self.subTest(path=path), self.assertRaisesRegex(tool.EvaluationError, "frozen_inputs_changed"):
                self.verify()
            path.write_bytes(original)
        for changed in (self.options | {"fixture_binding": {"owner_fixture": "d" * 64}},
                        self.options | {"settings_summary": self.options["settings_summary"] | {"endpoint_sha256": "e" * 64}},
                        self.options | {"commit": "d" * 40}):
            with self.assertRaises(tool.EvaluationError):
                tool.verify(self.output, **changed)
        with patch.object(tool, "_commit", return_value="f" * 40), self.assertRaises(tool.EvaluationError):
            self.verify()

    def test_added_deleted_source_and_old_ledger_bytes_or_path_cannot_drift(self):
        self.freeze()
        extra = self.source.parent / "new_runtime.py"
        extra.write_bytes(b"# changed closure\n")
        with self.assertRaisesRegex(tool.EvaluationError, "frozen_inputs_changed"):
            self.verify()
        extra.unlink()
        original = self.old.read_bytes()
        self.old.write_bytes(original + b"new attempt\n")
        with self.assertRaisesRegex(tool.EvaluationError, "frozen_inputs_changed"):
            self.verify()
        self.old.write_bytes(original)
        replacement = self.evidence / "replacement.jsonl"
        replacement.write_bytes(original)
        with self.assertRaises(tool.EvaluationError):
            tool.verify(self.output, **(self.options | {"prior_ledgers": {"previous_paused": replacement}}))
        raw = self.source.read_bytes()
        self.source.unlink()
        with self.assertRaises(tool.EvaluationError):
            self.verify()
        self.source.write_bytes(raw)

    def test_known_scope_units_cannot_be_substituted_or_silently_reduced(self):
        path = self.root / "backend/knowledge/national-corpus-v2.json"
        for changed in ({"venues": [{"id": "museum_people", "city_id": "110000"}], "documents": []},
                        {"venues": [{"id": "museum_people", "city_id": "310000"}], "documents": []}):
            path.write_bytes(tool.canonical(changed))
            with self.assertRaisesRegex(tool.EvaluationError, "reviewed_scope_changed"):
                self.freeze()
            self.assertFalse(self.output.exists())

    def test_endpoint_matrix_no_arbitrary_address_or_url_metadata(self):
        for host in ("dashscope.aliyuncs.com", "workspace-7.cn-beijing.maas.aliyuncs.com"):
            value = tool.trusted_settings(f"https://{host}/compatible-mode/v1/chat/completions")
            self.assertEqual(value["model"], tool.MODEL)
            self.assertNotIn(host, str(value))
        for endpoint in ("https://evil.test/compatible-mode/v1/chat/completions",
                         "http://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                         "https://dashscope.aliyuncs.com:443/compatible-mode/v1/chat/completions",
                         "https://user@dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                         "https://a.us-east-1.maas.aliyuncs.com/compatible-mode/v1/chat/completions",
                         "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions?x=1",
                         "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions#x",
                         "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"):
            with self.subTest(endpoint=endpoint), self.assertRaises(tool.EvaluationError):
                tool.trusted_settings(endpoint)

    def test_duplicate_json_nonfinite_extra_protocol_and_private_paths_rejected(self):
        self.plan.write_bytes(b'{"schema_version":"x","schema_version":"y"}')
        with self.assertRaises(tool.EvaluationError):
            self.freeze()
        self.plan.write_bytes(b'{"value":NaN}')
        with self.assertRaises(tool.EvaluationError):
            self.freeze()
        self.plan.write_bytes((tool.ROOT / tool.PLAN).read_bytes())
        self.freeze()
        original = self.output.read_bytes()
        value = json.loads(original)
        value["extra"] = "unreviewed"
        self.output.write_bytes(tool.canonical(value))
        with self.assertRaises(tool.EvaluationError):
            self.verify()
        self.output.write_bytes(original)
        self.output.chmod(0o644)
        with self.assertRaisesRegex(tool.EvaluationError, "private_protocol_required"):
            self.verify()
        self.output.chmod(0o600)
        linked = self.evidence / "linked.json"
        linked.symlink_to(self.output)
        with self.assertRaises(tool.EvaluationError):
            tool.freeze(linked, **self.options)
        self.evidence.chmod(0o755)
        with self.assertRaises(tool.EvaluationError):
            tool.freeze(self.evidence / "other.json", **self.options)

    def test_ledger_binding_is_verified_identity_not_a_quota_or_sender(self):
        self.freeze()
        bound = tool.ledger_binding(self.output, **self.options)
        self.assertEqual(set(bound), {"grant_sha256", "protocol_sha256", "code_freeze_sha256", "prior_ledger_hashes"})
        self.assertEqual(bound["protocol_sha256"], self.verify()["protocol_sha256"])
        self.assertEqual(bound["code_freeze_sha256"], tool.digest(tool.canonical(json.loads(self.output.read_bytes())["source_files"])))
        explicit = tool.ledger_binding(self.output, grant_sha256="a" * 64, **self.options)
        self.assertEqual(explicit["grant_sha256"], "a" * 64)
        self.old.write_bytes(b"changed paused counter")
        with self.assertRaises(tool.EvaluationError):
            tool.ledger_binding(self.output, **self.options)


if __name__ == "__main__":
    unittest.main()
