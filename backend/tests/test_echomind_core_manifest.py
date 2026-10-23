"""Offline provenance checks: no imported runtime, dependency service or model."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest

from tools.echomind_core_manifest import (
    EXCLUDED_TESTS,
    ManifestError,
    build_manifest,
    source_inventory,
    transform,
    verify,
)


BACKEND = Path(__file__).resolve().parents[1]
ORIGINAL = BACKEND.parent / "EchoMind"
DELIVERED = BACKEND / "echomind_core" / "upstream"
MANIFEST = BACKEND / "echomind_core" / "SOURCE.json"


class EchoMindCoreManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.delivered = root / "upstream"
        shutil.copytree(DELIVERED, self.delivered)
        self.manifest_path = root / "SOURCE.json"
        self.manifest_path.write_bytes(MANIFEST.read_bytes())

    def mutate_manifest(self, callback) -> None:
        data = json.loads(self.manifest_path.read_bytes())
        callback(data)
        self.manifest_path.write_text(json.dumps(data), encoding="utf-8")

    def assert_invalid(self, reason: str) -> None:
        with self.assertRaisesRegex(ManifestError, f"^{reason}$"):
            verify(self.delivered, self.manifest_path)

    def test_delivery_verifies_without_the_uploaded_repository(self) -> None:
        result = verify(self.delivered, self.manifest_path)
        self.assertEqual(result["imported_files"], 36)
        self.assertEqual(result["generated_namespaces"], 11)
        self.assertEqual(result["source_reconstruction"], "reference_absent_delivered_hashes_verified")

    @unittest.skipUnless(ORIGINAL.is_dir(), "Optional ignored reference is absent from this checkout")
    def test_full_original_reconstructs_the_delivery_exactly(self) -> None:
        result = verify(self.delivered, self.manifest_path, ORIGINAL)
        self.assertEqual(result["source_reconstruction"], "verified")
        self.assertEqual(json.loads(MANIFEST.read_bytes()), build_manifest(ORIGINAL))

    def test_algorithm_change_is_rejected(self) -> None:
        path = self.delivered / "agents" / "agent_orchestrator.py"
        path.write_bytes(path.read_bytes().replace(b"128", b"127", 1) + b"\n# unauthorized edit\n")
        self.assert_invalid("delivered_hash_mismatch")

    def test_extra_and_missing_files_are_rejected(self) -> None:
        extra = self.delivered / "core" / "unexpected.py"
        extra.write_text("pass\n", encoding="utf-8")
        self.assert_invalid("delivered_inventory_mismatch")
        extra.unlink()
        (self.delivered / "core" / "redaction.py").unlink()
        self.assert_invalid("missing_or_escaping_file")

    def test_file_symlink_and_directory_symlink_are_rejected(self) -> None:
        path = self.delivered / "core" / "redaction.py"
        path.unlink()
        path.symlink_to(DELIVERED / "core" / "redaction.py")
        self.assert_invalid("symlink_forbidden")
        path.unlink()
        path.write_bytes((DELIVERED / "core" / "redaction.py").read_bytes())
        (self.delivered / "extra").symlink_to(DELIVERED / "core", target_is_directory=True)
        self.assert_invalid("symlink_forbidden")

    def test_path_escape_absolute_and_noncanonical_paths_are_rejected(self) -> None:
        for invalid in ("../outside.py", "/tmp/outside.py", "core/../redaction.py", "core//redaction.py", "core\\redaction.py", "core/invalid\x00.py"):
            with self.subTest(invalid=invalid):
                self.manifest_path.write_bytes(MANIFEST.read_bytes())
                self.mutate_manifest(lambda data: data["files"][0].update(path=invalid))
                self.assert_invalid("invalid_manifest_path")

    def test_duplicate_paths_extra_fields_and_allowlist_changes_are_rejected(self) -> None:
        self.mutate_manifest(lambda data: data["files"].append(data["files"][0]))
        self.assert_invalid("duplicate_manifest_path")
        self.manifest_path.write_bytes(MANIFEST.read_bytes())
        self.mutate_manifest(lambda data: data.update(ignored=True))
        self.assert_invalid("invalid_manifest_fields")
        self.manifest_path.write_bytes(MANIFEST.read_bytes())
        self.mutate_manifest(lambda data: data["transform_allowlist"].append("rewrite_algorithms"))
        self.assert_invalid("transformation_allowlist_changed")

    def test_duplicate_json_keys_are_rejected(self) -> None:
        self.manifest_path.write_text('{"schema_version":"v1","schema_version":"v2"}', encoding="utf-8")
        self.assert_invalid("duplicate_manifest_key")

    def test_only_import_identity_changes_are_allowed(self) -> None:
        original = (
            b"from anthropic import AsyncAnthropic\n"
            b"from core.intent_recognizer import IntentRecognizer\n"
            b"    from agents.tools import AgentToolSpec\n"
            b"model = 'claude-3-5-sonnet-20241022'\n"
            b"message = 'from core.intent_recognizer import private'\n"
            b"URL = 'https://api.siliconflow.cn/v1/embeddings'\n"
        )
        delivered, applied = transform(original, is_test=False)
        self.assertEqual(delivered, (
            b"from echomind_core.provider import QwenMessagesClient as AsyncAnthropic\n"
            b"from echomind_core.upstream.core.intent_recognizer import IntentRecognizer\n"
            b"    from echomind_core.upstream.agents.tools import AgentToolSpec\n"
            b"model = 'claude-3-5-sonnet-20241022'\n"
            b"message = 'from core.intent_recognizer import private'\n"
            b"URL = 'https://api.siliconflow.cn/v1/embeddings'\n"
        ))
        self.assertEqual(applied, ["rebase_internal_from_imports", "bind_anthropic_messages_to_qwen_adapter"])

    def test_test_helper_and_importlib_rebasing_is_test_only(self) -> None:
        original = b'from test_agent_orchestrator import make_request\nmodule = "tooling.knowledge_base"\n'
        self.assertEqual(transform(original, is_test=False), (original, []))
        delivered, applied = transform(original, is_test=True)
        self.assertEqual(delivered, b'from echomind_core.upstream.tests.test_agent_orchestrator import make_request\nmodule = "echomind_core.upstream.tooling.knowledge_base"\n')
        self.assertEqual(applied, ["rebase_upstream_test_helper_imports", "rebase_two_test_importlib_module_names"])

    @unittest.skipUnless(ORIGINAL.is_dir(), "Optional ignored reference is absent from this checkout")
    def test_complete_core_skills_and_portable_tests_not_api_environment_data(self) -> None:
        inventory = source_inventory(ORIGINAL)
        original_core = sorted(path.relative_to(ORIGINAL).as_posix() for directory in ("agents", "core", "tooling", "memory", "monitor", "evaluation") for path in (ORIGINAL / directory).rglob("*.py"))
        self.assertEqual([path for path in inventory if path.split("/")[0] in {"agents", "core", "tooling", "memory", "monitor", "evaluation"}], original_core)
        self.assertTrue(all(path not in inventory for path in EXCLUDED_TESTS))
        self.assertEqual(len([path for path in inventory if path.startswith("tests/test_")]), 11)
        self.assertFalse(any(path.startswith(("api/", "data/", ".env")) for path in inventory))


if __name__ == "__main__":
    unittest.main()
