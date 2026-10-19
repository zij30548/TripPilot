import contextlib
import hashlib
import io
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
import release


class DependencyPermissions(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "frontend/node_modules"
        (self.root / "package/bin").mkdir(parents=True)
        (self.root / ".bin").mkdir()
        (self.root / "package/bin/cli").write_bytes(b"executable bytes unchanged")
        (self.root / "package/data").write_bytes(b"ordinary bytes unchanged")
        (self.root / "keep").write_bytes(b"already read-only")
        self.other = self.base / "unrelated"
        self.other.write_bytes(b"outside fixed scope")
        self.other.chmod(0o666)
        (self.root / ".bin/cli").symlink_to("../package/bin/cli")
        self.audit = self.base / "receipts"
        self.audit.mkdir(mode=0o700)
        self.owner = os.getuid(), os.getgid()
        self.root.chmod(0o775)
        (self.root / "package").chmod(0o777)
        (self.root / "package/bin").chmod(0o751)
        (self.root / "package/bin/cli").chmod(0o775)
        (self.root / "package/data").chmod(0o664)
        (self.root / "keep").chmod(0o444)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def fingerprint(self) -> dict[str, object]:
        return {str(p.relative_to(self.base)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.base.rglob("*")
                if p.is_file() and not p.is_symlink() and self.audit not in p.parents}

    def repair(self, **updates):
        plan = artifacts.scan_tree(self.root, self.owner, allow_writable=True)
        return artifacts.remove_write_bits(self.root, self.owner, plan, self.audit,
                                           updates.get("fingerprint", self.fingerprint), commit="a" * 40)

    def prepare_node_build(self) -> None:
        frontend = self.base / "frontend"
        for path in ("src/lib", "public", "node_modules/next", ".next/static/chunks"):
            (frontend / path).mkdir(parents=True, exist_ok=True)
        source = (DEPLOY.parent / "frontend/src/lib/api-endpoints.ts").read_text()
        endpoints = re.findall(r':\s*"(/api/[a-z/-]+)"', source)
        rules = [{"source": path, "destination": "http://127.0.0.1:8000" + path[4:]} for path in endpoints]
        rules.extend([
            {"source": "/api/health", "destination": "http://127.0.0.1:8000/health"},
            {"source": "/api/saved-trips/:id([0-9a-f]{32})", "destination": "http://127.0.0.1:8000/saved-trips/:id"},
            {"source": "/_AMapService/v3/config/district", "destination": "http://127.0.0.1:8000/_AMapService/v3/config/district"},
        ])
        for path, value in {
            "package.json": json.dumps({"dependencies": {"next": "16.3.8"}, "devDependencies": {"eslint-config-next": "16.3.8"}}),
            "package-lock.json": json.dumps({"packages": {"node_modules/next": {"version": "16.3.8"}}}),
            "next.config.ts": "// controlled fixture", "tsconfig.json": "{}",
            "src/lib/api-endpoints.ts": source, "public/icon.svg": "<svg/>",
            "node_modules/next/package.json": '{"version":"16.3.8"}',
            ".next/BUILD_ID": "controlled-build", ".next/static/chunks/app.js": "controlled static bytes",
            ".next/routes-manifest.json": json.dumps({"rewrites": {"beforeFiles": rules, "afterFiles": [], "fallback": []}}),
        }.items():
            (frontend / path).write_text(value)
        (self.base / "deploy").mkdir()
        shutil.copyfile(DEPLOY / "verify-frontend.mjs", self.base / "deploy/verify-frontend.mjs")

    def real_node(self, argv, *, capture=False, **kwargs):
        self.assertEqual(argv[0], "/usr/bin/node")
        self.assertTrue(capture)
        node = shutil.which("node")
        self.assertIsNotNone(node, "deployment bridge regression requires the installed Node runtime")
        # Map only the Linux executable path for this test host. Execute the
        # actual caller script and actual verifier, never fabricate its JSON.
        result = subprocess.run([node, *argv[1:]], env=release.clean_environment(),
                                check=True, capture_output=True, text=True, timeout=10)
        self.assertNotIn("STOP:", result.stderr)
        return result.stdout.strip()

    def test_real_python_node_import_bridge_fingerprints_before_and_after_repair(self) -> None:
        self.prepare_node_build()
        with patch.object(release, "SOURCE", self.base), patch.object(release, "run", side_effect=self.real_node), contextlib.redirect_stdout(io.StringIO()):
            expected = release.dependency_fingerprint()
            self.assertEqual(set(expected), {"next_version", "build_id", "backend_origin", "package_lock_sha256",
                                            "source_sha256", "build_sha256", "static_sha256",
                                            "node_modules_sha256", "declared_public_key_sha256"})
            manifest, changed = self.repair(fingerprint=release.dependency_fingerprint)
        self.assertEqual(changed, 4)
        self.assertEqual(json.loads(manifest.read_text())["content_before"], expected)
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o755)

    def test_real_fingerprint_failure_precedes_manifest_and_chmod(self) -> None:
        self.prepare_node_build()
        (self.base / "frontend/.next/BUILD_ID").write_text("invalid/build")
        before = artifacts.scan_tree(self.root, self.owner, allow_writable=True)
        with patch.object(release, "SOURCE", self.base), patch.object(release, "run", side_effect=self.real_node), patch.object(artifacts, "_chmod_entry") as chmod:
            with self.assertRaises(subprocess.CalledProcessError) as error:
                self.repair(fingerprint=release.dependency_fingerprint)
            self.assertIn("build_id_invalid", error.exception.stderr)
            self.assertNotIn("absolute_paths_required", error.exception.stderr)
            chmod.assert_not_called()
        self.assertEqual(list(self.audit.iterdir()), [])
        self.assertEqual(artifacts.scan_tree(self.root, self.owner, allow_writable=True), before)

    def test_apply_only_removes_022_and_preserves_owner_execution_content_and_links(self) -> None:
        before = artifacts.scan_tree(self.root, self.owner, allow_writable=True)
        before_bytes = self.fingerprint()
        link = (self.root / ".bin/cli").lstat()
        with contextlib.redirect_stdout(io.StringIO()):
            manifest, changed = self.repair()
        self.assertEqual(changed, 4)
        after = artifacts.scan_tree(self.root, self.owner)
        self.assertEqual([e.path for e in before], [e.path for e in after])
        for old, new in zip(before, after):
            with self.subTest(path=old.path):
                self.assertEqual(new.mode, old.mode if old.kind == "symlink" else old.mode & ~0o022)
                self.assertEqual((old.uid, old.gid, old.inode, old.device),
                                 (new.uid, new.gid, new.inode, new.device))
        self.assertEqual(self.fingerprint(), before_bytes)
        self.assertEqual(stat.S_IMODE(self.other.stat().st_mode), 0o666)
        self.assertEqual((self.root / ".bin/cli").lstat(), link)
        self.assertEqual(os.readlink(self.root / ".bin/cli"), "../package/bin/cli")
        self.assertEqual(stat.S_IMODE(manifest.stat().st_mode), 0o600)
        self.assertEqual(manifest.stat().st_uid, os.geteuid())
        payload = json.loads(manifest.read_text())
        self.assertEqual(payload["entries"], [artifacts.asdict(e) for e in before])
        self.assertEqual(payload["content_before"], before_bytes)
        self.assertEqual(payload["state"], "prepared_not_completion_receipt")
        self.assertNotIn("executable bytes", manifest.read_text())

    def test_default_check_reports_work_without_mutation_or_manifest(self) -> None:
        before = artifacts.scan_tree(self.root, self.owner, allow_writable=True)
        with patch.object(release, "SOURCE", self.base), patch.object(release, "artifact_owner", return_value=self.owner), patch.object(release, "remove_write_bits") as mutate, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(release.Refused, "dependency_permissions_need_explicit_apply"):
                release.dependency_permissions()
            mutate.assert_not_called()
        self.assertEqual(artifacts.scan_tree(self.root, self.owner, allow_writable=True), before)
        self.assertEqual(list(self.audit.iterdir()), [])

    def test_full_preflight_refuses_links_hardlinks_special_files_modes_and_owner(self) -> None:
        bad = self.root / "z-late-invalid"
        cases = [
            ("absolute", lambda: bad.symlink_to(self.root / "keep")),
            ("escaping", lambda: bad.symlink_to("../../unrelated")),
            ("dangling", lambda: bad.symlink_to("missing")),
            ("cycle", lambda: bad.symlink_to("z-late-invalid")),
            ("hardlink", lambda: os.link(self.root / "keep", bad)),
            ("fifo", lambda: os.mkfifo(bad)),
        ]
        for name, create in cases:
            with self.subTest(name=name):
                create()
                try:
                    with patch.object(artifacts.os, "fchmod") as chmod:
                        with self.assertRaises(artifacts.ArtifactError): self.repair()
                        chmod.assert_not_called()
                    self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o775)
                    self.assertEqual(list(self.audit.iterdir()), [])
                finally:
                    bad.unlink()
        for mode in (0o4755, 0o2755, 0o1755):
            with self.subTest(mode=mode):
                (self.root / "package/data").chmod(mode)
                with self.assertRaisesRegex(artifacts.ArtifactError, "artifact_special_permissions"):
                    self.repair()
        (self.root / "package/data").chmod(0o664)
        with self.assertRaisesRegex(artifacts.ArtifactError, "artifact_unexpected_owner"):
            artifacts.scan_tree(self.root, (self.owner[0] + 1, self.owner[1]), allow_writable=True)

    def test_partial_failure_leaves_original_manifest_and_converged_modes(self) -> None:
        original = artifacts._chmod_entry
        calls = 0
        def fail_second(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("controlled chmod failure")
            return original(*args)
        with patch.object(artifacts, "_chmod_entry", side_effect=fail_second), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(OSError): self.repair()
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((self.root / "package").stat().st_mode), 0o777)
        manifests = list(self.audit.iterdir())
        self.assertEqual(len(manifests), 1)
        self.assertEqual(json.loads(manifests[0].read_text())["entries"][0]["mode"], 0o775)
        self.assertEqual(stat.S_IMODE(manifests[0].stat().st_mode), 0o600)

    def test_audit_write_failure_prevents_every_permission_change(self) -> None:
        before = artifacts.scan_tree(self.root, self.owner, allow_writable=True)
        with patch.object(artifacts.os, "fsync", side_effect=OSError("controlled audit failure")):
            with self.assertRaises(OSError): self.repair()
        self.assertEqual(artifacts.scan_tree(self.root, self.owner, allow_writable=True), before)
        self.assertEqual(len(list(self.audit.iterdir())), 1)

    def test_changed_file_or_swapped_parent_link_is_not_chmod_followed(self) -> None:
        plan = artifacts.scan_tree(self.root, self.owner, allow_writable=True)
        entry = next(e for e in plan if e.path == "package/bin/cli")
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "cli").write_bytes(b"unrelated")
        (outside / "cli").chmod(0o666)
        parent = self.root / "package/bin"
        parent.rename(self.root / "package/old-bin")
        parent.symlink_to(outside, target_is_directory=True)
        fd = artifacts._root_fd(self.root)
        try:
            with self.assertRaises((OSError, artifacts.ArtifactError)):
                artifacts._chmod_entry(fd, entry, {e.path: e for e in plan})
        finally:
            os.close(fd)
        self.assertEqual(stat.S_IMODE((outside / "cli").stat().st_mode), 0o666)

    def test_changed_content_detected_and_manifest_retained(self) -> None:
        calls = 0
        def different_after():
            nonlocal calls
            calls += 1
            return {"controlled": calls}
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(artifacts.ArtifactError, "artifact_content_changed_after_permission_update"):
                self.repair(fingerprint=different_after)
        self.assertEqual(len(list(self.audit.iterdir())), 1)
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o755)

    def test_stale_plan_and_nonprivate_audit_directory_fail_before_changes(self) -> None:
        plan = artifacts.scan_tree(self.root, self.owner, allow_writable=True)
        (self.root / "package/data").write_bytes(b"new input before apply")
        with self.assertRaisesRegex(artifacts.ArtifactError, "artifact_changed_before_permission_update"):
            artifacts.remove_write_bits(self.root, self.owner, plan, self.audit, self.fingerprint, commit="a" * 40)
        self.audit.chmod(0o755)
        with self.assertRaisesRegex(artifacts.ArtifactError, "permission_manifest_directory_permissions"):
            self.repair()
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o775)
        self.assertEqual(list(self.audit.iterdir()), [])

    def test_symlink_root_and_symlink_ancestor_are_never_opened(self) -> None:
        link = self.base / "alias"
        link.symlink_to(self.root, target_is_directory=True)
        for target in (link, link / "package"):
            with self.subTest(target=target), self.assertRaises(OSError):
                artifacts.scan_tree(target, self.owner, allow_writable=True)

    def test_second_apply_is_noop_and_preserves_the_first_manifest(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            manifest, _ = self.repair()
            original = manifest.read_bytes()
            with patch.object(release, "SOURCE", self.base), patch.object(release, "artifact_owner", return_value=self.owner), patch.object(release, "remove_write_bits") as mutate:
                release.dependency_permissions(apply=True)
                mutate.assert_not_called()
        self.assertEqual(manifest.read_bytes(), original)
        self.assertEqual(list(self.audit.iterdir()), [manifest])


if __name__ == "__main__": unittest.main()
