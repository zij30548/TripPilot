"""Offline controller tests: real temporary files, no root or server operations."""
from contextlib import ExitStack, redirect_stdout
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
from types import SimpleNamespace
import unittest
from unittest.mock import patch


DEPLOY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEPLOY))
import artifact_permissions
import release


class ReleaseReadability(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.commit = "a" * 40
        self.base = self.root / "opt/trippilot"
        self.target = self.base / "releases" / self.commit
        self.frontend = self.target / "frontend"
        self.dependencies = self.frontend / "node_modules"
        self.build = self.frontend / ".next"
        self.cache = self.build / "cache"
        for path in (self.dependencies, self.cache, self.base / "receipts"):
            path.mkdir(parents=True)
            path.chmod(0o700)
        self.target.chmod(0o755)
        self.frontend.chmod(0o755)
        self.build.chmod(0o700)
        for path, content, mode in (
            (self.dependencies / "module.js", b"controlled module bytes", 0o600),
            (self.dependencies / "executable", b"controlled executable bytes", 0o700),
            (self.build / "BUILD_ID", b"controlled-build", 0o600),
            (self.build / "server.js", b"controlled build bytes", 0o600),
            (self.cache / "private-cache", b"never publish cache", 0o600),
        ):
            path.write_bytes(content)
            path.chmod(mode)
        (self.dependencies / "internal-link").symlink_to("module.js")
        self.fingerprint = {
            "next_version": "controlled-version", "build_id": "controlled-build",
            "backend_origin": "http://127.0.0.1:8000",
            **{key: str(index) * 64 for index, key in enumerate((
                "package_lock_sha256", "source_sha256", "build_sha256",
                "static_sha256", "node_modules_sha256", "declared_public_key_sha256",
            ), 1)},
        }
        self.receipt = {
            "schema_version": "frontend-release-v1", "commit": self.commit,
            "platform": "linux", "architecture": "x64",
            "provenance": "operator-attested-existing-build", **self.fingerprint,
        }
        self.write_metadata()
        self.untouched = [self.target / "frontend-reuse.json", self.target / "release.json",
                          self.cache, self.cache / "private-cache"]
        for relative in ("source/frontend/.next/BUILD_ID", "outside/private.txt",
                         "opt/trippilot/releases/" + "b" * 40 + "/old-failure",
                         "opt/trippilot/receipts/old-receipt.json",
                         "opt/trippilot/releases/" + self.commit + "/backend-ready.json",
                         "opt/trippilot/releases/" + self.commit + "/frontend-ready.json"):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"unrelated contents must remain unchanged")
            path.chmod(0o600)
            self.untouched.append(path)
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(release, "BASE", self.base))
        self.stack.enter_context(patch.object(release, "RELEASES", self.base / "releases"))
        self.stack.enter_context(patch.object(release, "SOURCE", self.root / "source"))
        # Production requires root-owned trees/web cache; local tests map both
        # identities to their own uid/gid and never chown or invoke runuser.
        self.stack.enter_context(patch.object(release.pwd, "getpwnam", return_value=SimpleNamespace(
            pw_uid=os.geteuid(), pw_gid=os.getegid())))
        self.config = self.stack.enter_context(patch("config.check_files", return_value={
            "frontend": {"TRIPPILOT_BACKEND_ORIGIN": "http://127.0.0.1:8000"},
        }))
        self.actual_fingerprint = release.frontend_fingerprint
        self.hashes = self.stack.enter_context(patch.object(release, "frontend_fingerprint",
                                                        side_effect=lambda *a, **k: dict(self.fingerprint)))
        self.runner = self.stack.enter_context(patch.object(release, "run", side_effect=AssertionError(
            "controller must not inspect HEAD or execute a service in these tests")))

    def tearDown(self) -> None:
        self.stack.close()
        self.temp.cleanup()

    def write_metadata(self) -> None:
        path = self.target / "frontend-reuse.json"
        path.write_text(json.dumps(self.receipt))
        path.chmod(0o600)
        proof = self.target / "release.json"
        proof.write_text(json.dumps({
            "commit": self.commit, "frontend_receipt_sha256": release.digest(path),
            "python_venv_copied": False,
        }))
        proof.chmod(0o600)

    @staticmethod
    def snapshot(paths: list[Path]) -> dict[Path, tuple[object, ...]]:
        result = {}
        for path in paths:
            info = path.lstat()
            body = os.readlink(path) if path.is_symlink() else path.read_bytes() if path.is_file() else None
            result[path] = (stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid, info.st_ino, body)
        return result

    def scope_paths(self) -> list[Path]:
        return [self.dependencies, self.build, *self.dependencies.rglob("*"), *self.build.rglob("*")]

    def manifests(self) -> list[Path]:
        return list((self.base / "receipts").glob("release-permissions-*.json"))

    def prepare_real_verifier(self) -> Path:
        for relative in ("src/lib", "public", "node_modules/next", ".next/static/chunks"):
            (self.frontend / relative).mkdir(parents=True, exist_ok=True)
        endpoints_source = (DEPLOY.parent / "frontend/src/lib/api-endpoints.ts").read_text()
        endpoints = re.findall(r':\s*"(/api/[a-z/-]+)"', endpoints_source)
        rules = [{"source": path, "destination": "http://127.0.0.1:8000" + path[4:]}
                 for path in endpoints]
        uuid_pattern = "[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
        rules.extend([
            {"source": "/api/health", "destination": "http://127.0.0.1:8000/health"},
            {"source": "/api/saved-trips/:id([0-9a-f]{32})",
             "destination": "http://127.0.0.1:8000/saved-trips/:id"},
            *[{"source": "/api/chat/conversations/:id(" + uuid_pattern + ")" + suffix,
               "destination": "http://127.0.0.1:8000/chat/conversations/:id" + suffix}
              for suffix in ("", "/turns", "/cancel", "/confirm", "/delete")],
            {"source": "/_AMapService/v3/config/district",
             "destination": "http://127.0.0.1:8000/_AMapService/v3/config/district"},
        ])
        for relative, value in {
            "package.json": json.dumps({"dependencies": {"next": "16.3.8"},
                                        "devDependencies": {"eslint-config-next": "16.3.8"}}),
            "package-lock.json": json.dumps({"packages": {"node_modules/next": {"version": "16.3.8"}}}),
            "next.config.ts": "// controlled temporary fixture", "tsconfig.json": "{}",
            "src/lib/api-endpoints.ts": endpoints_source, "public/icon.svg": "<svg/>",
            "node_modules/next/package.json": '{"version":"16.3.8"}',
            ".next/static/chunks/app.js": "controlled static bytes",
            ".next/routes-manifest.json": json.dumps({"rewrites": {"beforeFiles": rules,
                                                                    "afterFiles": [], "fallback": []}}),
        }.items():
            (self.frontend / relative).write_text(value)
        (self.target / "deploy").mkdir()
        verifier = self.target / "deploy/verify-frontend.mjs"
        shutil.copyfile(DEPLOY / "verify-frontend.mjs", verifier)
        for scope in (self.dependencies, self.build):
            for path in [scope, *scope.rglob("*")]:
                if not path.is_symlink() and self.cache not in (path, *path.parents):
                    path.chmod(0o700 if path.is_dir() or path.name == "executable" else 0o600)
        return verifier

    def real_node(self, argv, *, capture=False, env=None, **kwargs):
        self.assertEqual(argv[0], "/usr/bin/node")
        self.assertTrue(capture)
        self.assertFalse(kwargs)
        node = shutil.which("node")
        self.assertIsNotNone(node, "the installed Node runtime is required for the real controller bridge")
        # Only Linux's executable location is mapped; actual Python caller,
        # imported target verifier and emitted fingerprints all execute as-is.
        result = subprocess.run([node, *argv[1:]], check=True, capture_output=True,
                                text=True, timeout=10, env=env)
        self.assertNotIn("STOP:", result.stderr)
        return result.stdout.strip()

    def test_real_python_node_controller_repair_preserves_actual_nine_field_fingerprint(self) -> None:
        verifier = self.prepare_real_verifier()
        self.runner.side_effect = self.real_node
        self.hashes.side_effect = self.actual_fingerprint
        expected = self.actual_fingerprint(verifier, self.frontend, env=release.clean_environment())
        self.assertEqual(set(expected), {"next_version", "build_id", "backend_origin", "package_lock_sha256",
                                         "source_sha256", "build_sha256", "static_sha256",
                                         "node_modules_sha256", "declared_public_key_sha256"})
        self.receipt.update(expected)
        self.write_metadata()
        outside = self.snapshot(self.untouched)
        release.release_permissions(self.commit, apply=True)
        actual = self.actual_fingerprint(verifier, self.frontend, env=release.clean_environment())
        self.assertEqual(actual, expected)
        self.assertEqual(json.loads(self.manifests()[0].read_text())["content_before"], expected)
        self.assertEqual(stat.S_IMODE((self.build / "BUILD_ID").stat().st_mode), 0o644)
        self.assertEqual(stat.S_IMODE(self.build.stat().st_mode), 0o755)
        self.assertEqual(self.snapshot(self.untouched), outside)
        self.assertEqual(self.runner.call_count, 4)

    def test_real_verifier_failure_does_not_create_audit_or_publish_permissions(self) -> None:
        self.prepare_real_verifier()
        self.runner.side_effect = self.real_node
        self.hashes.side_effect = self.actual_fingerprint
        (self.build / "BUILD_ID").write_text("invalid/build")
        before = self.snapshot(self.scope_paths() + self.untouched)
        with self.assertRaises(subprocess.CalledProcessError) as error:
            release.release_permissions(self.commit, apply=True)
        self.assertIn("build_id_invalid", error.exception.stderr)
        self.assertEqual(self.snapshot(list(before)), before)
        self.assertEqual(self.manifests(), [])

    def test_default_is_read_only_and_requires_explicit_apply(self) -> None:
        before = self.snapshot(self.scope_paths() + self.untouched)
        with self.assertRaisesRegex(release.Refused, "release_permissions_need_explicit_apply"):
            release.release_permissions(self.commit)
        self.assertEqual(self.snapshot(list(before)), before)
        self.assertEqual(self.manifests(), [])
        self.hashes.assert_not_called()
        self.config.assert_not_called()

    def test_explicit_apply_publishes_both_scopes_and_preserves_all_other_bytes(self) -> None:
        outside = self.snapshot(self.untouched)
        paths = self.scope_paths()
        before = self.snapshot(paths)
        original_publish = release.publish_readable
        observed = []

        def publish(*args, **kwargs):
            manifests = self.manifests()
            self.assertEqual(len(manifests), 1)  # The durable audit precedes either chmod scope.
            self.assertEqual(stat.S_IMODE(manifests[0].stat().st_mode), 0o600)
            value = json.loads(manifests[0].read_text())
            self.assertEqual(value["state"], "prepared_not_completion_receipt")
            self.assertEqual(value["commit"], self.commit)
            self.assertEqual(value["content_before"], self.fingerprint)
            self.assertEqual([item["path"] for item in value["scopes"]],
                             ["frontend/node_modules", "frontend/.next"])
            self.assertFalse(any(entry["path"].startswith("cache")
                                 for scope in value["scopes"] for entry in scope["entries"]))
            if not observed:
                self.assertEqual(self.snapshot(paths), before)
            observed.append(args[0])
            return original_publish(*args, **kwargs)

        with patch.object(release, "publish_readable", side_effect=publish):
            release.release_permissions(self.commit, apply=True)
        self.assertEqual(observed, [self.dependencies, self.build])
        self.assertEqual(stat.S_IMODE(self.dependencies.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(self.build.stat().st_mode), 0o755)
        for path in (self.build / "BUILD_ID", self.build / "server.js", self.dependencies / "module.js"):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
        self.assertEqual(stat.S_IMODE((self.dependencies / "executable").stat().st_mode), 0o755)
        for path, info in before.items():
            current = self.snapshot([path])[path]
            self.assertEqual(current[1:], info[1:], str(path))  # Owner, inode, body/link unchanged.
        self.assertEqual(self.snapshot(self.untouched), outside)
        self.assertEqual(self.hashes.call_count, 2)
        for call in self.hashes.call_args_list:
            self.assertEqual(call.args, (self.target / "deploy/verify-frontend.mjs", self.frontend))
            self.assertEqual(call.kwargs["env"]["TRIPPILOT_BACKEND_ORIGIN"], "http://127.0.0.1:8000")
        self.runner.assert_not_called()  # Explicit target, unrelated checkout HEAD is irrelevant.

    def test_second_check_and_apply_are_noops_without_more_manifests(self) -> None:
        release.release_permissions(self.commit, apply=True)
        paths = self.scope_paths() + self.untouched + self.manifests()
        before = self.snapshot(paths)
        release.release_permissions(self.commit)
        release.release_permissions(self.commit, apply=True)
        self.assertEqual(len(self.manifests()), 1)
        self.assertEqual(self.snapshot(paths), before)

    def test_either_scope_failure_precedes_manifest_and_any_chmod(self) -> None:
        for scope in (self.dependencies, self.build):
            for defect in ("shared_write", "special_mode", "escaping_link", "hardlink", "fifo"):
                with self.subTest(scope=scope.name, defect=defect):
                    bad = scope / "unsafe"
                    if defect == "escaping_link": bad.symlink_to(self.root / "outside")
                    elif defect == "hardlink": os.link(self.root / "outside/private.txt", bad)
                    elif defect == "fifo": os.mkfifo(bad, 0o600)
                    else:
                        bad.write_bytes(b"controlled invalid artifact")
                        bad.chmod(0o666 if defect == "shared_write" else 0o4600)
                    paths = [path for path in self.scope_paths() if path != bad] + self.untouched
                    before = self.snapshot(paths)
                    try:
                        with self.assertRaises(artifact_permissions.ArtifactError):
                            release.release_permissions(self.commit, apply=True)
                        self.assertEqual(self.snapshot(paths), before)
                        self.assertEqual(self.manifests(), [])
                        self.hashes.assert_not_called()
                    finally:
                        bad.unlink()

    def test_unexpected_release_owner_is_rejected_without_mutation(self) -> None:
        before = self.snapshot(self.scope_paths() + self.untouched)
        with patch.object(release.os, "geteuid", return_value=os.geteuid() + 1):
            with self.assertRaisesRegex(release.Refused, "release_directory_permissions"):
                release.release_permissions(self.commit, apply=True)
        self.assertEqual(self.snapshot(list(before)), before)
        self.assertEqual(self.manifests(), [])

    def test_scope_owner_mismatch_is_rejected_before_publication(self) -> None:
        original = release.scan_tree

        def wrong_owner(path, owner, **kwargs):
            return original(path, (owner[0] + 1, owner[1]), **kwargs)

        before = self.snapshot(self.scope_paths())
        with patch.object(release, "scan_tree", side_effect=wrong_owner):
            with self.assertRaisesRegex(artifact_permissions.ArtifactError, "artifact_unexpected_owner"):
                release.release_permissions(self.commit, apply=True)
        self.assertEqual(self.snapshot(list(before)), before)
        self.assertEqual(self.manifests(), [])

    def test_receipt_binding_and_version_mismatches_never_change_permissions(self) -> None:
        for defect in ("release_commit", "receipt_hash", "receipt_commit", "schema", "architecture"):
            with self.subTest(defect=defect):
                self.receipt["commit"] = self.commit
                self.receipt["schema_version"] = "frontend-release-v1"
                self.receipt["architecture"] = "x64"
                if defect == "receipt_commit": self.receipt["commit"] = "b" * 40
                if defect == "schema": self.receipt["schema_version"] = "unsupported"
                if defect == "architecture": self.receipt["architecture"] = "arm64"
                self.write_metadata()
                if defect in {"release_commit", "receipt_hash"}:
                    path = self.target / "release.json"
                    value = json.loads(path.read_text())
                    value["commit" if defect == "release_commit" else "frontend_receipt_sha256"] = "c" * 40
                    path.write_text(json.dumps(value))
                before = self.snapshot(self.scope_paths() + self.untouched)
                with self.assertRaises(release.Refused): release.release_permissions(self.commit, apply=True)
                self.assertEqual(self.snapshot(list(before)), before)
                self.assertEqual(self.manifests(), [])
                self.hashes.assert_not_called()

    def test_fingerprint_mismatch_precedes_manifest_and_changes(self) -> None:
        self.hashes.side_effect = lambda *a, **k: self.fingerprint | {"build_sha256": "f" * 64}
        before = self.snapshot(self.scope_paths() + self.untouched)
        with self.assertRaisesRegex(release.Refused, "release_artifact_fingerprint_mismatch"):
            release.release_permissions(self.commit, apply=True)
        self.assertEqual(self.snapshot(list(before)), before)
        self.assertEqual(self.manifests(), [])

    def test_private_cache_must_remain_exact_private_directory(self) -> None:
        self.cache.chmod(0o755)
        before = self.snapshot(self.scope_paths() + self.untouched)
        with self.assertRaisesRegex(release.Refused, "private_web_cache_permissions"):
            release.release_permissions(self.commit, apply=True)
        self.assertEqual(self.snapshot(list(before)), before)
        self.assertEqual(self.manifests(), [])
        self.hashes.assert_not_called()

    def test_private_cache_wrong_service_identity_is_rejected(self) -> None:
        with patch.object(release.pwd, "getpwnam", return_value=SimpleNamespace(
                pw_uid=os.geteuid() + 1, pw_gid=os.getegid())):
            with self.assertRaisesRegex(release.Refused, "private_web_cache_permissions"):
                release.release_permissions(self.commit, apply=True)
        self.assertEqual(stat.S_IMODE(self.build.stat().st_mode), 0o700)
        self.assertEqual(self.manifests(), [])

    def test_cache_symlink_is_rejected_even_when_resolving_to_private_directory(self) -> None:
        external = self.root / "outside-cache"
        self.cache.rename(external)
        self.cache.symlink_to(external, target_is_directory=True)
        before = self.snapshot([external, external / "private-cache", self.cache, self.build])
        with self.assertRaisesRegex(release.Refused, "private_web_cache_permissions"):
            release.release_permissions(self.commit, apply=True)
        self.assertEqual(self.snapshot(list(before)), before)
        self.assertEqual(self.manifests(), [])

    def test_private_manifest_failure_does_not_change_any_mode(self) -> None:
        before = self.snapshot(self.scope_paths() + self.untouched)
        with patch.object(release, "save_publication_manifest", side_effect=OSError("controlled manifest failure")):
            with self.assertRaises(OSError): release.release_permissions(self.commit, apply=True)
        self.assertEqual(self.snapshot(list(before)), before)
        self.assertEqual(self.manifests(), [])

    def test_partial_failure_preserves_private_prepared_manifest_and_never_claims_success(self) -> None:
        original = release.publish_readable

        def fail_second(path, *args, **kwargs):
            if path == self.build: raise OSError("controlled second-scope failure")
            return original(path, *args, **kwargs)

        original_build = self.snapshot([self.build, self.build / "BUILD_ID"])
        outside = self.snapshot(self.untouched)
        output = io.StringIO()
        with redirect_stdout(output), patch.object(release, "publish_readable", side_effect=fail_second):
            with self.assertRaises(OSError): release.release_permissions(self.commit, apply=True)
        self.assertEqual(stat.S_IMODE(self.dependencies.stat().st_mode), 0o755)
        self.assertEqual(self.snapshot(list(original_build)), original_build)
        self.assertEqual(self.snapshot(self.untouched), outside)
        self.assertEqual(len(self.manifests()), 1)
        self.assertEqual(json.loads(self.manifests()[0].read_text())["state"], "prepared_not_completion_receipt")
        self.assertIn("saved privately", output.getvalue())
        self.assertNotIn("permissions applied:", output.getvalue())
        self.assertEqual(self.hashes.call_count, 1)

    def test_cli_forwards_only_explicit_commit_and_apply_flag(self) -> None:
        for enabled in (False, True):
            with self.subTest(apply=enabled):
                argv = ["release.py", "release-permissions", "--commit", self.commit]
                if enabled: argv.append("--apply")
                descriptor = os.open(self.root / "test-lock", os.O_CREAT | os.O_RDWR, 0o600)
                with patch.object(sys, "argv", argv), patch.object(release.os, "umask"), patch.object(release, "root"), patch.object(
                    release, "deployment_lock", return_value=descriptor), patch.object(
                    release, "release_permissions") as command:
                    self.assertEqual(release.main(), 0)
                command.assert_called_once_with(self.commit, enabled)


if __name__ == "__main__":
    unittest.main()
