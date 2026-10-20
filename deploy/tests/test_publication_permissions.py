"""Real-file checks for publishing a private build to a read-only runtime.

No Linux service users, application services, external APIs or user artifacts
are accessed. Node, when used, inspects only a temporary controlled build.
"""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

DEPLOY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEPLOY))
import artifact_permissions as artifacts


class PublicationPermissions(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "frontend/.next"
        (self.root / "static/chunks").mkdir(parents=True)
        (self.root / "BUILD_ID").write_bytes(b"controlled-build")
        (self.root / "static/chunks/app.js").write_bytes(b"controlled static bytes")
        (self.root / "executable").write_bytes(b"#!/bin/sh\nexit 0\n")
        (self.root / "read-only").write_bytes(b"unchanged read-only bytes")
        (self.root / "internal-link").symlink_to("BUILD_ID")
        for item in [self.root, *self.root.rglob("*")]:
            if not item.is_symlink():
                item.chmod(0o700 if item.is_dir() else 0o600)
        (self.root / "executable").chmod(0o700)
        (self.root / "read-only").chmod(0o444)
        self.other = self.base / "unrelated"
        self.other.write_bytes(b"outside the publication scope")
        self.other.chmod(0o666)
        self.owner = os.getuid(), os.getgid()
        self.audit = self.base / "receipts"
        self.audit.mkdir(mode=0o700)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def plan(self, *, skip_cache: bool = False) -> list[artifacts.Entry]:
        return artifacts.scan_tree(self.root, self.owner, skip_cache=skip_cache)

    def publish(self, *, skip_cache: bool = False) -> int:
        return artifacts.publish_readable(self.root, self.owner,
                                          self.plan(skip_cache=skip_cache),
                                          skip_cache=skip_cache)

    def file_hashes(self) -> dict[str, str]:
        return {str(item.relative_to(self.base)): hashlib.sha256(item.read_bytes()).hexdigest()
                for item in self.base.rglob("*") if item.is_file() and not item.is_symlink()}

    def test_runtime_mode_is_minimal_read_traverse_and_preserves_execution(self) -> None:
        directory = self.plan()[0]
        regular = next(entry for entry in self.plan() if entry.path == "BUILD_ID")
        link = next(entry for entry in self.plan() if entry.kind == "symlink")
        for mode, expected in [(0o700, 0o755), (0o750, 0o755), (0o555, 0o555)]:
            with self.subTest(kind="directory", mode=mode):
                self.assertEqual(artifacts.runtime_mode(replace(directory, mode=mode)), expected)
        for mode, expected in [(0o600, 0o644), (0o640, 0o644), (0o444, 0o444),
                               (0o700, 0o755), (0o710, 0o755), (0o100, 0o555)]:
            with self.subTest(kind="file", mode=mode):
                self.assertEqual(artifacts.runtime_mode(replace(regular, mode=mode)), expected)
        self.assertEqual(artifacts.runtime_mode(link), link.mode)

    def test_publish_changes_only_modes_preserving_content_identity_and_links(self) -> None:
        before = self.plan()
        hashes = self.file_hashes()
        link = (self.root / "internal-link").lstat()
        unrelated = self.other.stat()
        changed = self.publish()
        after = self.plan()
        expected_changes = sum(entry.kind != "symlink" and
                               artifacts.runtime_mode(entry) != entry.mode for entry in before)
        self.assertEqual(changed, expected_changes)
        self.assertEqual(len(before), len(after))
        for old, new in zip(before, after):
            with self.subTest(path=old.path):
                self.assertEqual(new, replace(old, mode=artifacts.runtime_mode(old)))
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((self.root / "BUILD_ID").stat().st_mode), 0o644)
        self.assertEqual(stat.S_IMODE((self.root / "executable").stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((self.root / "read-only").stat().st_mode), 0o444)
        self.assertEqual((self.root / "internal-link").lstat(), link)
        self.assertEqual(os.readlink(self.root / "internal-link"), "BUILD_ID")
        self.assertEqual(self.file_hashes(), hashes)
        self.assertEqual(self.other.stat(), unrelated)

    def test_skipped_cache_modes_content_and_metadata_are_not_touched(self) -> None:
        cache = self.root / "cache"
        cache.mkdir(mode=0o700)
        data = cache / "private-cache"
        data.write_bytes(b"do not publish or inspect this cache")
        data.chmod(0o600)
        before = cache.stat(), data.stat(), data.read_bytes()
        plan = self.plan(skip_cache=True)
        self.assertFalse(any(entry.path == "cache" or entry.path.startswith("cache/") for entry in plan))
        artifacts.publish_readable(self.root, self.owner, plan, skip_cache=True)
        self.assertEqual((cache.stat(), data.stat(), data.read_bytes()), before)
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o755)

    def test_second_publication_is_noop(self) -> None:
        self.assertGreater(self.publish(), 0)
        before = self.plan()
        with patch.object(artifacts.os, "fchmod") as chmod:
            self.assertEqual(self.publish(), 0)
            chmod.assert_not_called()
        self.assertEqual(self.plan(), before)

    def test_writable_special_permissions_and_unexpected_owner_reject_before_chmod(self) -> None:
        plan = self.plan()
        build = self.root / "BUILD_ID"
        for mode in (0o622, 0o4755, 0o2755, 0o1755):
            with self.subTest(mode=mode):
                build.chmod(mode)
                with patch.object(artifacts.os, "fchmod") as chmod:
                    with self.assertRaises(artifacts.ArtifactError):
                        artifacts.publish_readable(self.root, self.owner, plan)
                    chmod.assert_not_called()
                self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        build.chmod(0o600)
        with patch.object(artifacts.os, "fchmod") as chmod:
            with self.assertRaisesRegex(artifacts.ArtifactError, "artifact_unexpected_owner"):
                artifacts.publish_readable(self.root, (self.owner[0] + 1, self.owner[1]), plan)
            chmod.assert_not_called()

    def test_hardlink_special_file_and_escaping_link_reject_complete_preflight(self) -> None:
        plan = self.plan()
        invalid = self.root / "z-invalid"
        cases = [("hardlink", lambda: os.link(self.root / "BUILD_ID", invalid)),
                 ("fifo", lambda: os.mkfifo(invalid)),
                 ("absolute", lambda: invalid.symlink_to(self.other)),
                 ("escape", lambda: invalid.symlink_to("../../unrelated")),
                 ("dangling", lambda: invalid.symlink_to("missing"))]
        for name, create in cases:
            with self.subTest(case=name):
                create()
                try:
                    with patch.object(artifacts.os, "fchmod") as chmod:
                        with self.assertRaises(artifacts.ArtifactError):
                            artifacts.publish_readable(self.root, self.owner, plan)
                        chmod.assert_not_called()
                    self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
                    self.assertEqual(stat.S_IMODE(self.other.stat().st_mode), 0o666)
                finally:
                    invalid.unlink()

    def test_stale_plan_is_rejected_before_permissions_change(self) -> None:
        plan = self.plan()
        (self.root / "BUILD_ID").write_bytes(b"changed after preflight")
        with patch.object(artifacts.os, "fchmod") as chmod:
            with self.assertRaises(artifacts.ArtifactError):
                artifacts.publish_readable(self.root, self.owner, plan)
            chmod.assert_not_called()
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)

    def test_swapped_parent_symlink_is_not_followed_during_mode_update(self) -> None:
        plan = self.plan()
        entry = next(item for item in plan if item.path == "static/chunks/app.js")
        outside = self.base / "outside"
        (outside / "chunks").mkdir(parents=True)
        target = outside / "chunks/app.js"
        target.write_bytes(b"must remain unrelated")
        target.chmod(0o600)
        (self.root / "static").rename(self.root / "old-static")
        (self.root / "static").symlink_to(outside, target_is_directory=True)
        descriptor = artifacts._root_fd(self.root)
        try:
            with self.assertRaises((OSError, artifacts.ArtifactError)):
                artifacts._chmod_entry(descriptor, entry, {item.path: item for item in plan}, mode=0o644)
        finally:
            os.close(descriptor)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        self.assertEqual(target.read_bytes(), b"must remain unrelated")

    def test_swapped_leaf_symlink_is_not_followed_during_mode_update(self) -> None:
        plan = self.plan()
        entry = next(item for item in plan if item.path == "BUILD_ID")
        (self.root / "BUILD_ID").unlink()
        (self.root / "BUILD_ID").symlink_to(self.other)
        descriptor = artifacts._root_fd(self.root)
        try:
            with self.assertRaises((OSError, artifacts.ArtifactError)):
                artifacts._chmod_entry(descriptor, entry, {item.path: item for item in plan}, mode=0o644)
        finally:
            os.close(descriptor)
        self.assertEqual(stat.S_IMODE(self.other.stat().st_mode), 0o666)

    def test_partial_fchmod_failure_preserves_private_manifest_and_stops(self) -> None:
        plan = self.plan()
        manifest = artifacts.save_publication_manifest(self.audit, {"state": "prepared", "entries": [artifacts.asdict(e) for e in plan]})
        original = artifacts.os.fchmod
        attempts = 0
        def fail_second(descriptor: int, mode: int) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 2:
                raise OSError("controlled permission failure")
            original(descriptor, mode)
        with patch.object(artifacts.os, "fchmod", side_effect=fail_second):
            with self.assertRaises(OSError):
                artifacts.publish_readable(self.root, self.owner, plan)
        self.assertEqual(attempts, 2)
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((self.root / "BUILD_ID").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(manifest.stat().st_mode), 0o600)
        self.assertEqual(json.loads(manifest.read_text())["state"], "prepared")
        self.assertEqual(json.loads(manifest.read_text())["entries"][0]["mode"], 0o700)

    def test_publication_manifest_is_private_exclusive_and_durably_recorded(self) -> None:
        with patch.object(artifacts, "uuid4") as identity:
            identity.return_value.hex = "a" * 32
            value = {"state": "prepared_not_completion_receipt", "safe_count": 2}
            manifest = artifacts.save_publication_manifest(self.audit, value)
            self.assertEqual(manifest.name, "release-permissions-" + "a" * 32 + ".json")
            self.assertEqual(json.loads(manifest.read_text()), value)
            self.assertEqual(stat.S_IMODE(manifest.stat().st_mode), 0o600)
            self.assertEqual((manifest.stat().st_uid, manifest.stat().st_gid), (os.geteuid(), os.getegid()))
            original = manifest.read_bytes()
            with self.assertRaises(FileExistsError):
                artifacts.save_publication_manifest(self.audit, {"must_not": "replace"})
            self.assertEqual(manifest.read_bytes(), original)

    def test_manifest_rejects_nonprivate_or_symlink_directory(self) -> None:
        self.audit.chmod(0o755)
        with self.assertRaisesRegex(artifacts.ArtifactError, "permission_manifest_directory_permissions"):
            artifacts.save_publication_manifest(self.audit, {"state": "prepared"})
        self.assertEqual(list(self.audit.iterdir()), [])
        self.audit.chmod(0o700)
        alias = self.base / "audit-alias"
        alias.symlink_to(self.audit, target_is_directory=True)
        with self.assertRaises(OSError):
            artifacts.save_publication_manifest(alias, {"state": "prepared"})
        self.assertEqual(list(self.audit.iterdir()), [])

    def prepare_node_build(self) -> Path:
        frontend = self.base / "frontend"
        # The production .next verifier correctly rejects any build symlink;
        # link-safety is exercised separately using dependency-like trees.
        (self.root / "internal-link").unlink()
        for path in ("src/lib", "public", "node_modules/next"):
            (frontend / path).mkdir(parents=True, exist_ok=True)
        endpoints_source = (DEPLOY.parent / "frontend/src/lib/api-endpoints.ts").read_text()
        endpoints = re.findall(r':\s*"(/api/[a-z/-]+)"', endpoints_source)
        rules = [{"source": path, "destination": "http://127.0.0.1:8000" + path[4:]} for path in endpoints]
        uuid_pattern = "[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
        rules += [
            {"source": "/api/health", "destination": "http://127.0.0.1:8000/health"},
            {"source": "/api/saved-trips/:id([0-9a-f]{32})", "destination": "http://127.0.0.1:8000/saved-trips/:id"},
            *[{"source": "/api/chat/conversations/:id(" + uuid_pattern + ")" + suffix,
               "destination": "http://127.0.0.1:8000/chat/conversations/:id" + suffix}
              for suffix in ("", "/turns", "/cancel", "/confirm", "/delete")],
            {"source": "/_AMapService/v3/config/district", "destination": "http://127.0.0.1:8000/_AMapService/v3/config/district"},
        ]
        for path, value in {
            "package.json": json.dumps({"dependencies": {"next": "16.3.8"}, "devDependencies": {"eslint-config-next": "16.3.8"}}),
            "package-lock.json": json.dumps({"packages": {"node_modules/next": {"version": "16.3.8"}}}),
            "next.config.ts": "// controlled fixture", "tsconfig.json": "{}",
            "src/lib/api-endpoints.ts": endpoints_source, "public/icon.svg": "<svg/>",
            "node_modules/next/package.json": '{"version":"16.3.8"}',
            ".next/routes-manifest.json": json.dumps({"rewrites": {"beforeFiles": rules, "afterFiles": [], "fallback": []}}),
        }.items():
            (frontend / path).write_text(value)
        (self.base / "deploy").mkdir()
        verifier = self.base / "deploy/verify-frontend.mjs"
        shutil.copyfile(DEPLOY / "verify-frontend.mjs", verifier)
        for root in (self.root, frontend / "node_modules"):
            for item in [root, *root.rglob("*")]:
                if not item.is_symlink():
                    item.chmod(0o700 if item.is_dir() else 0o600)
        return verifier

    def test_actual_node_inspect_build_fingerprints_do_not_change_when_publishing(self) -> None:
        node = shutil.which("node")
        self.assertIsNotNone(node, "publication bridge regression requires the installed Node runtime")
        verifier = self.prepare_node_build()
        frontend = self.base / "frontend"
        script = ("import {pathToFileURL} from 'node:url';"
                  "const {inspectBuild}=await import(pathToFileURL(process.argv[2]));"
                  "console.log(JSON.stringify(await inspectBuild(process.argv[3])));")
        def inspect() -> dict[str, object]:
            result = subprocess.run([node, "--input-type=module", "-e", script,
                                     "publication-fingerprint-test", str(verifier), str(frontend)],
                                    check=True, capture_output=True,
                                    text=True, timeout=10,
                                    env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C.UTF-8"})
            return json.loads(result.stdout)
        before = inspect()
        for root in (self.root, frontend / "node_modules"):
            plan = artifacts.scan_tree(root, self.owner)
            artifacts.publish_readable(root, self.owner, plan)
        self.assertEqual(inspect(), before)
        self.assertEqual(len(before), 9)
        self.assertEqual(stat.S_IMODE((self.root / "BUILD_ID").stat().st_mode), 0o644)


if __name__ == "__main__":
    unittest.main()
