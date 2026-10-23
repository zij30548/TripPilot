"""Original SkillManager reuse and frozen trusted travel-rule boundaries."""
from __future__ import annotations

import importlib.util
import io
import logging
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

if any(importlib.util.find_spec(name) is None for name in ("jsonschema", "prometheus_client")):
    raise unittest.SkipTest("requires the explicit echomind extra")

from echomind_core.execution import ExecutionError
from echomind_core.travel_skills import NAMES, ROOT, TravelSkillCatalog
from echomind_core.upstream.core.skill_loader import SkillManager


class TravelSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="travel-skills-")
        self.addCleanup(self.temp.cleanup)
        # macOS /var -> /private/var is not a hostile Skill-path symlink.
        self.root = Path(self.temp.name).resolve()
        self.catalog = TravelSkillCatalog(self.root)
        self.seed()

    def seed(self) -> None:
        for name in NAMES:
            path = self.root / name / "SKILL.md"
            path.parent.mkdir(exist_ok=True)
            if path.is_symlink():
                path.unlink()
            self.write(name, f"GLOBAL_RULE_{name}")

    def write(self, name: str, body: str, metadata: str = "") -> None:
        path = self.root / name / "SKILL.md"
        path.write_text(f"---\nname: {name}\n{metadata}---\n# {name}\n{body}\n", encoding="utf-8")
        path.chmod(0o600)

    def assert_rejected(self) -> None:
        with self.assertRaisesRegex(ExecutionError, r"^travel_skills(?:_changed)?$"):
            self.catalog.capture()

    def test_capture_and_prompt_reuse_preserved_load_parse_and_prompt(self) -> None:
        original_load, original_prompt = SkillManager.load, SkillManager.prompt_for
        with patch.object(SkillManager, "load", autospec=True, side_effect=original_load) as load_mock, \
                patch.object(SkillManager, "prompt_for", autospec=True, side_effect=original_prompt) as prompt_mock:
            frozen = self.catalog.capture()
            answer = frozen.prompt_for("ordinary user message", "travel_budget")
        self.assertEqual(load_mock.call_count, 1)
        self.assertGreaterEqual(prompt_mock.call_count, 2)
        self.assertIn("当前请求可用的 EchoMind Skills", answer)
        self.assertEqual([row["name"] for row in frozen.summary()["skills"]], list(NAMES))

    def test_all_three_rules_are_global_regardless_of_keyword_or_agent(self) -> None:
        frozen = self.catalog.capture()
        for message, role in (("", None), ("完全无关内容", "travel_planning"), ("budget", "arbitrary")):
            text = frozen.prompt_for(message, role)
            for name in NAMES:
                self.assertIn(f"GLOBAL_RULE_{name}", text)
            self.assertNotIn("...", text)
        self.assertEqual(len(frozen.hashes), 3)

    def test_hot_reload_changes_only_new_captures_and_their_version(self) -> None:
        previous = self.catalog.capture()
        text = previous.prompt_for("before")
        self.write(NAMES[0], "REVISED_TRUSTED_RULE")
        current = self.catalog.capture()
        self.assertNotEqual(previous.version, current.version)
        self.assertNotEqual(previous.hashes, current.hashes)
        self.assertEqual(previous.prompt_for("after"), text)
        self.assertIn("REVISED_TRUSTED_RULE", current.prompt_for("after"))
        self.assertNotIn("REVISED_TRUSTED_RULE", previous.prompt_for("after"))

    def test_single_rule_and_whole_prompt_limits_reject_instead_of_truncating(self) -> None:
        self.write(NAMES[0], "x" * 3001)
        self.assert_rejected()
        self.seed()
        for name in NAMES:
            self.write(name, "x" * 1800)
        self.assert_rejected()

    def test_raw_input_size_empty_and_missing_rules_fail_closed(self) -> None:
        path = self.root / NAMES[0] / "SKILL.md"
        for raw in (b"x" * 12001, b"", b"---\nname: empty\n---\n"):
            path.write_bytes(raw)
            self.assert_rejected()
        path.unlink()
        self.assert_rejected()

    def test_conditional_or_disabled_rule_cannot_weaken_global_safety(self) -> None:
        for metadata in ("keywords: special\n", "agents: travel_budget\n", "enabled: false\n"):
            self.seed()
            self.write(NAMES[0], "only conditional", metadata)
            self.assert_rejected()

    def test_unregistered_extra_files_including_extra_links_are_not_prompt_authority(self) -> None:
        extra = self.root / "arbitrary_rules.md"
        extra.write_text("DO_NOT_IMPORT_THIS", encoding="utf-8")
        outside = self.root / "outside_rule.txt"
        outside.write_text("DO_NOT_IMPORT_LINKED_RULE", encoding="utf-8")
        linked = self.root / "unregistered_link.md"
        linked.symlink_to(outside)
        frozen = self.catalog.capture()
        text = frozen.prompt_for("ordinary")
        self.assertNotIn("DO_NOT_IMPORT", text)
        self.assertEqual(len(frozen.hashes), 3)

    def test_registered_file_directory_and_root_links_are_rejected(self) -> None:
        path = self.root / NAMES[0] / "SKILL.md"
        original = path.read_bytes()
        outside = self.root / "outside.md"
        outside.write_bytes(original)
        path.unlink()
        path.symlink_to(outside)
        self.assert_rejected()
        path.unlink()
        path.write_bytes(original)
        moved = self.root / "other-directory"
        (self.root / NAMES[0]).rename(moved)
        (self.root / NAMES[0]).symlink_to(moved, target_is_directory=True)
        self.assert_rejected()
        other_root = self.root / "linked-root"
        other_root.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ExecutionError, "^travel_skills$"):
            TravelSkillCatalog(other_root).capture()

    def test_unsafe_permissions_and_nonregular_files_rejected_before_loading(self) -> None:
        path = self.root / NAMES[0] / "SKILL.md"
        for mode in (0o622, 0o644 | 0o4000, 0o644 | 0o2000):
            path.chmod(mode)
            self.assert_rejected()
        path.unlink()
        if hasattr(os, "mkfifo"):
            os.mkfifo(path, 0o600)
            self.assert_rejected()

    def test_source_changes_during_original_load_are_detected_without_retry(self) -> None:
        original_load = SkillManager.load

        def changed(manager):
            result = original_load(manager)
            self.write(NAMES[0], "changed while loading")
            return result

        with patch.object(SkillManager, "load", autospec=True, side_effect=changed) as loader:
            with self.assertRaisesRegex(ExecutionError, "^travel_skills_changed$"):
                self.catalog.capture()
        self.assertEqual(loader.call_count, 1)

    def test_second_hash_read_disappearance_or_oserror_has_fixed_safe_diagnostic(self) -> None:
        original_load, original_read = SkillManager.load, Path.read_bytes
        target = self.root / NAMES[0] / "SKILL.md"
        for mode in ("removed", "permission_error"):
            with self.subTest(mode=mode):
                self.seed()
                loaded = False

                def finish_load(manager):
                    nonlocal loaded
                    result = original_load(manager)
                    loaded = True
                    if mode == "removed":
                        target.unlink()
                    return result

                def read_bytes(path):
                    if loaded and path == target and mode == "permission_error":
                        raise PermissionError("PRIVATE_PATH_DO_NOT_LOG", str(target))
                    return original_read(path)

                with patch.object(SkillManager, "load", autospec=True, side_effect=finish_load) as loader, \
                        patch.object(Path, "read_bytes", autospec=True, side_effect=read_bytes):
                    with self.assertRaises(ExecutionError) as found:
                        self.catalog.capture()
                self.assertEqual(str(found.exception), "travel_skills_changed")
                self.assertNotIn(str(target), str(found.exception))
                self.assertNotIn("PRIVATE_PATH_DO_NOT_LOG", str(found.exception))
                self.assertTrue(found.exception.__suppress_context__)
                self.assertEqual(loader.call_count, 1)

    def test_logging_quarantine_does_not_emit_user_text_or_private_file_paths(self) -> None:
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        root = logging.getLogger()
        original_level = root.level
        root.setLevel(logging.DEBUG)
        root.addHandler(handler)
        self.addCleanup(root.removeHandler, handler)
        self.addCleanup(root.setLevel, original_level)
        frozen = self.catalog.capture()
        frozen.prompt_for("PRIVATE_USER_MESSAGE_DO_NOT_LOG", "travel_budget")
        output = stream.getvalue()
        self.assertNotIn("PRIVATE_USER_MESSAGE_DO_NOT_LOG", output)
        self.assertNotIn(str(self.root), output)
        self.assertNotIn("GLOBAL_RULE", output)

    def test_current_repository_catalog_is_small_and_read_only(self) -> None:
        frozen = TravelSkillCatalog(ROOT).capture()
        self.assertEqual(len(frozen.hashes), 3)
        self.assertLessEqual(len(frozen.prompt_for("", "travel_planning")), 5000)
        summary = frozen.summary()
        self.assertEqual(set(summary), {"version", "skills"})
        self.assertNotIn(str(ROOT), str(summary))


if __name__ == "__main__":
    unittest.main()
