import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

DEPLOY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEPLOY))
import release
import config


class DeploymentPreparation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()

    def tearDown(self): self.temp.cleanup()

    def ready_release(self, commit):
        target = self.root / "releases" / commit
        (target / "backend").mkdir(parents=True)
        (target / "backend/uv.lock").write_text("controlled-lock")
        (target / "frontend-reuse.json").write_text("controlled-frontend-receipt")
        frontend_hash = release.digest(target / "frontend-reuse.json")
        for name, proof in {
            "release.json": {"commit": commit, "frontend_receipt_sha256": frontend_hash, "python_venv_copied": False},
            "backend-ready.json": {"uv_lock_sha256": release.digest(target / "backend/uv.lock")},
            "frontend-ready.json": {"frontend_receipt_sha256": frontend_hash, "native_and_static_smoke": True},
        }.items():
            (target / name).write_text(json.dumps(proof))
        return target

    def test_fixed_release_and_full_commit_only(self):
        for value in ("main", "4e00985", "../current", "A" * 40):
            with self.assertRaises(release.Refused): release.checked_release(value)
        self.assertEqual(release.checked_release("a" * 40), Path("/opt/trippilot/releases") / ("a" * 40))

    def test_management_steps_are_serialized_and_lock_is_reusable_after_close(self):
        path = self.root / "lock"
        first = release.deployment_lock(path)
        try:
            with self.assertRaises(release.Refused): release.deployment_lock(path)
        finally: os.close(first)
        os.close(release.deployment_lock(path))
        path.chmod(0o644)
        with self.assertRaises(release.Refused): release.deployment_lock(path)

    def test_existing_directory_and_file_are_never_repaired_or_overwritten(self):
        target = self.root / "new"
        uid, gid = os.getuid(), os.getgid()
        release.ensure_directory(target, uid, gid, 0o700)
        release.ensure_directory(target, uid, gid, 0o700)
        target.chmod(0o755)
        with self.assertRaises(release.Refused): release.ensure_directory(target, uid, gid, 0o700)
        source, dest = self.root / "source", self.root / "dest"
        source.write_text("new")
        dest.write_text("original")
        with self.assertRaises(release.Refused): release.install_new_or_identical(source, dest, 0o600)
        self.assertEqual(dest.read_text(), "original")

    def test_release_excludes_private_and_platform_data(self):
        for path in ("backend/.env", "frontend/.env.local", "backend/data/identity.sqlite3", "backend/app/__pycache__/x.pyc", "frontend/.next/server/x", "frontend/node_modules/x", "../../etc/passwd"):
            self.assertTrue(release.excluded(path), path)
        for path in ("backend/main.py", "shared/destinations.json", "deploy/env/backend.env.example", "frontend/public/favicon.ico"):
            self.assertFalse(release.excluded(path), path)

    def test_artifact_copy_excludes_cache_preserves_safe_links_rejects_escape(self):
        source = self.root / "source"
        source.mkdir()
        (source / "cache").mkdir()
        (source / "cache/private").write_text("not copied")
        (source / "file").write_text("artifact")
        (source / "inside").symlink_to("file")
        with patch.object(release, "artifact_owner", return_value=(os.getuid(), os.getgid())):
            release.safe_copy_tree(source, self.root / "copy", skip_cache=True)
        self.assertFalse((self.root / "copy/cache").exists())
        self.assertEqual((self.root / "copy/inside").read_text(), "artifact")
        (source / "escape").symlink_to("../outside")
        with patch.object(release, "artifact_owner", return_value=(os.getuid(), os.getgid())):
            with self.assertRaises(release.Refused): release.safe_copy_tree(source, self.root / "refused")

    def test_stage_checks_both_artifact_trees_before_creating_a_release(self):
        commit, old_commit = "a" * 40, "b" * 40
        source, releases = self.root / "checkout", self.root / "releases"
        for name in ("frontend/node_modules", "frontend/.next/static"):
            (source / name).mkdir(parents=True, mode=0o755)
        module = source / "frontend/node_modules/module.js"
        chunk = source / "frontend/.next/static/chunk.js"
        module.write_bytes(b"unchanged dependency")
        chunk.write_bytes(b"unchanged build")
        module.chmod(0o644)
        chunk.chmod(0o644)
        old = releases / old_commit
        old.mkdir(parents=True)
        old_failure = old / "failure-marker"
        old_failure.write_bytes(b"preserve failed release")
        receipt = self.root / "receipt.json"
        receipt.write_text(json.dumps({"commit": commit}))
        old_receipt = self.root / "old-receipt.json"
        old_receipt.write_bytes(b"preserve old receipt")
        receipt_bytes = receipt.read_bytes()
        original_files = {module: module.read_bytes(), chunk: chunk.read_bytes()}

        def controlled_run(argv, **kwargs):
            if "rev-parse" in argv: return commit
            if "diff" in argv: return ""
            return ""  # Successful existing verify-source; permissions are independently checked.

        with patch.object(release, "SOURCE", source), patch.object(release, "RELEASES", releases), patch.object(release, "run", side_effect=controlled_run) as runner, patch.object(release, "artifact_owner", return_value=(os.getuid(), os.getgid())), patch("config.check_files", return_value={"frontend": {}}), patch.object(release, "archive_commit") as archive:
            for tree in (source / "frontend/node_modules", source / "frontend/.next"):
                for defect in ("writable_root", "writable_file", "escaping_link"):
                    with self.subTest(tree=tree.name, defect=defect):
                        bad = tree / "unsafe"
                        if defect == "writable_root":
                            tree.chmod(0o775)
                        elif defect == "writable_file":
                            bad.write_bytes(b"unchanged unsafe file")
                            bad.chmod(0o666)
                        else:
                            outside = self.root / "outside"
                            outside.write_bytes(b"never traverse")
                            bad.symlink_to(outside)
                        try:
                            with self.assertRaises(release.Refused): release.stage(commit, receipt)
                            self.assertFalse((releases / commit).exists())
                            archive.assert_not_called()
                            self.assertEqual(old_failure.read_bytes(), b"preserve failed release")
                            self.assertEqual(old_receipt.read_bytes(), b"preserve old receipt")
                            self.assertEqual(receipt.read_bytes(), receipt_bytes)
                            for path, body in original_files.items(): self.assertEqual(path.read_bytes(), body)
                            self.assertTrue(any("verify-source" in call.args[0] for call in runner.call_args_list))
                        finally:
                            tree.chmod(0o755)
                            if bad.is_symlink() or bad.exists(): bad.unlink()
                            runner.reset_mock()

    def test_stage_rejects_hardlinked_artifact_before_creating_release(self):
        commit = "a" * 40
        source, releases = self.root / "checkout", self.root / "releases"
        for name in ("frontend/node_modules", "frontend/.next"):
            (source / name).mkdir(parents=True, mode=0o755)
        original = source / "frontend/.next/original"
        original.write_bytes(b"do not modify hardlinked data")
        original.chmod(0o644)
        os.link(original, source / "frontend/.next/linked")
        receipt = self.root / "receipt.json"
        receipt.write_text(json.dumps({"commit": commit}))
        def controlled_run(argv, **kwargs): return commit if "rev-parse" in argv else ""
        with patch.object(release, "SOURCE", source), patch.object(release, "RELEASES", releases), patch.object(release, "run", side_effect=controlled_run), patch.object(release, "artifact_owner", return_value=(os.getuid(), os.getgid())), patch("config.check_files", return_value={"frontend": {}}), patch.object(release, "archive_commit") as archive:
            with self.assertRaises(release.Refused): release.stage(commit, receipt)
            self.assertFalse((releases / commit).exists())
            archive.assert_not_called()
            self.assertEqual(original.read_bytes(), b"do not modify hardlinked data")

    def test_stage_existing_or_wrong_receipt_stops_before_mutation(self):
        commit = "a" * 40
        with patch.object(release, "RELEASES", self.root):
            (self.root / commit).mkdir()
            with patch.object(release, "run") as run:
                with self.assertRaises(release.Refused): release.stage(commit, self.root / "missing.json")
                run.assert_not_called()

    def test_extraction_failure_reaps_only_its_archive_producer(self):
        with patch.object(release.subprocess, "Popen") as popen, patch.object(release.tarfile, "open", side_effect=OSError("controlled extraction failure")):
            process = popen.return_value.__enter__.return_value
            process.poll.return_value = None
            with self.assertRaises(OSError): release.archive_commit("a" * 40, self.root)
            process.kill.assert_called_once_with()
            process.wait.assert_called_once_with(timeout=5)
        self.assertFalse((self.root / "current").exists())

    def test_clean_subprocess_environment_has_no_inherited_secrets_or_home(self):
        with patch.dict(os.environ, {"QWEN_API_KEY": "private-must-not-leak", "AMAP_WEB_KEY": "private"}):
            env = release.clean_environment()
        self.assertNotIn("QWEN_API_KEY", env)
        self.assertNotIn("AMAP_WEB_KEY", env)
        self.assertNotIn("/home/admin", json.dumps(env))
        self.assertEqual(env["PATH"], "/usr/local/bin:/usr/bin:/bin")

    def identity_helper(self, fail_action=None):
        helper = self.root / "sbin/runuser"
        helper.parent.mkdir(exist_ok=True)
        records = self.root / "identity-records.jsonl"
        records.write_text("")
        helper.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            f"with open({str(records)!r}, 'a') as stream:\n"
            "    stream.write(json.dumps({'argv': sys.argv[1:], 'path': os.environ['PATH'], "
            "'home': os.environ['HOME'], 'cwd': os.getcwd()}) + '\\n')\n"
            f"raise SystemExit(7 if {fail_action!r} in sys.argv else 0)\n"
        )
        helper.chmod(0o755)
        return helper, records

    def test_absolute_identity_helper_executes_outside_restricted_path(self):
        helper, records = self.identity_helper()
        empty_bin = self.root / "empty-bin"
        empty_bin.mkdir()
        env = release.clean_environment() | {"PATH": str(empty_bin)}
        with self.assertRaises(FileNotFoundError): release.run(["runuser", "controlled"], env=env)
        self.assertEqual(records.read_text(), "")
        release.run([str(helper), "controlled"], env=env)
        self.assertEqual(json.loads(records.read_text())["argv"], ["controlled"])

    def test_frontend_verification_dispatches_both_checks_via_fixed_identity_helper(self):
        commit = "a" * 40
        target = self.root / "releases" / commit
        target.mkdir(parents=True)
        receipt = target / "frontend-reuse.json"
        receipt.write_bytes(b"unchanged staged receipt")
        helper, records = self.identity_helper()
        real_run = release.run

        def controlled_run(argv, **kwargs):
            self.assertEqual(argv[0], "/usr/sbin/runuser")
            return real_run([str(helper), *argv[1:]], **kwargs)

        with patch.object(release, "RELEASES", self.root / "releases"), patch("config.check_files", return_value={"frontend": {"TRIPPILOT_BACKEND_ORIGIN": "http://127.0.0.1:8000"}}), patch.object(release, "run", side_effect=controlled_run):
            release.frontend_verification(commit)
        entries = [json.loads(line) for line in records.read_text().splitlines()]
        self.assertEqual(len(entries), 2)
        for entry, action in zip(entries, ("verify", "smoke")):
            expected = ["-u", "trippilot-web", "--", "/usr/bin/node", str(target / "deploy/verify-frontend.mjs"), action,
                        "--frontend", str(target / "frontend"), "--receipt", str(receipt)]
            if action == "verify": expected.append("--native")
            self.assertEqual(entry["argv"], expected)
            self.assertEqual(entry["path"], release.clean_environment()["PATH"])
            self.assertEqual(entry["home"], "/nonexistent")
        self.assertEqual(receipt.read_bytes(), b"unchanged staged receipt")
        self.assertEqual(json.loads((target / "frontend-ready.json").read_text()),
                         {"frontend_receipt_sha256": release.digest(receipt), "native_and_static_smoke": True})

    def test_frontend_subprocess_failure_never_publishes_readiness(self):
        commit = "a" * 40
        target = self.root / "releases" / commit
        target.mkdir(parents=True)
        receipt = target / "frontend-reuse.json"
        receipt.write_bytes(b"preserve original receipt")
        real_run = release.run
        for failure in ("verify", "smoke"):
            with self.subTest(failure=failure):
                helper, records = self.identity_helper(failure)

                def controlled_run(argv, **kwargs):
                    self.assertEqual(argv[0], "/usr/sbin/runuser")
                    return real_run([str(helper), *argv[1:]], **kwargs)

                with patch.object(release, "RELEASES", self.root / "releases"), patch("config.check_files", return_value={"frontend": {}}), patch.object(release, "run", side_effect=controlled_run):
                    with self.assertRaises(subprocess.CalledProcessError): release.frontend_verification(commit)
                self.assertFalse((target / "frontend-ready.json").exists())
                self.assertEqual(receipt.read_bytes(), b"preserve original receipt")
                self.assertEqual(len(records.read_text().splitlines()), 1 if failure == "verify" else 2)

    def test_video_check_uses_fixed_identity_helper_and_failure_does_not_enable_video(self):
        commit = "a" * 40
        target = self.root / "releases" / commit
        (target / "backend").mkdir(parents=True)
        (target / "backend-ready.json").write_text("{}")
        real_run = release.run
        for failure in ("synthetic", None):
            with self.subTest(failure=failure):
                helper, records = self.identity_helper(failure)

                def controlled_run(argv, **kwargs):
                    if argv == ["systemctl", "daemon-reload"]: return ""  # Never operate the test host's services.
                    self.assertEqual(argv[0], "/usr/sbin/runuser")
                    return real_run([str(helper), *argv[1:]], **kwargs)

                with patch.object(release, "RELEASES", self.root / "releases"), patch.object(release, "run", side_effect=controlled_run), patch.object(release, "ensure_directory") as directory, patch.object(release, "install_new_or_identical") as install:
                    if failure:
                        with self.assertRaises(subprocess.CalledProcessError): release.enable_video(commit)
                        self.assertFalse((target / "video-ready.json").exists())
                        directory.assert_not_called()
                        install.assert_not_called()
                    else:
                        release.enable_video(commit)
                        install.assert_called_once()
                        self.assertEqual(json.loads((target / "video-ready.json").read_text()),
                                         {"synthetic_production_decoder_passed": True, "commit": commit})
                entry = json.loads(records.read_text())
                self.assertEqual(entry["argv"], ["-u", "trippilot-api", "--", str(target / "backend/.venv/bin/python"), "-B", str(target / "deploy/check-video.py"), "synthetic"])
                self.assertEqual(entry["path"], release.clean_environment()["PATH"])
                self.assertEqual(entry["home"], "/nonexistent")
                self.assertEqual(entry["cwd"], str(target / "backend"))

    def test_backend_import_failure_does_not_publish_readiness_or_repair_partial_venv(self):
        commit = "a" * 40
        target = self.root / "releases" / commit
        (target / "backend").mkdir(parents=True)
        (target / "release.json").write_text("{}")
        (target / "backend/uv.lock").write_text("controlled-lock")
        interpreter = self.root / "python/cpython-3.13.16-linux-x86_64-gnu/bin/python3.13"
        interpreter.parent.mkdir(parents=True)
        interpreter.touch()
        def simulated_run(argv, **kwargs):
            if "sync" in argv:
                directory = target / "backend/.venv/bin"
                directory.mkdir(parents=True)
                (directory / "python").symlink_to(interpreter)
                return ""
            raise subprocess.CalledProcessError(1, "controlled import failure")
        with patch.object(release, "BASE", self.root), patch.object(release, "RELEASES", self.root / "releases"), patch.object(release, "run", side_effect=simulated_run) as runner:
            with self.assertRaises(subprocess.SubprocessError): release.backend_dependencies(commit)
            self.assertFalse((target / "backend-ready.json").exists())
            runner.reset_mock()
            with self.assertRaises(release.Refused): release.backend_dependencies(commit)
            runner.assert_not_called()

    def test_linux_guard_cannot_mutate_on_mac(self):
        with patch.object(release.platform, "system", return_value="Darwin"):
            with self.assertRaises(release.Refused): release.root()

    def test_root_git_uses_only_one_fixed_trusted_checkout_not_global_allow(self):
        self.assertEqual(release.git_command("rev-parse", "HEAD"), ["git", "-c", "safe.directory=/home/admin/apps/trippilot", "rev-parse", "HEAD"])

    def test_site_pointer_switch_is_explicit_idempotent_and_rolls_back_failed_syntax(self):
        for directory in ("sites-available", "sites-enabled", "certificates"):
            (self.root / directory).mkdir()
        def install(source, target, *args):
            if target.exists(): self.assertEqual(source.read_bytes(), target.read_bytes())
            else: shutil.copyfile(source, target)
        enabled = self.root / "sites-enabled/trippilot"
        with patch.object(release, "NGINX", self.root), patch.object(release, "CERTIFICATES", self.root / "certificates"), patch.object(release, "install_new_or_identical", side_effect=install), patch.object(release, "run") as runner:
            release.site(False)
            original = os.readlink(enabled)
            release.site(False)
            self.assertEqual(os.readlink(enabled), original)
            with self.assertRaises(release.Refused): release.site(True)
            for name in ("fullchain.pem", "privkey.pem"): (self.root / "certificates" / name).touch()
            runner.side_effect = subprocess.CalledProcessError(1, "nginx")
            with self.assertRaises(subprocess.SubprocessError): release.site(True)
            self.assertEqual(os.readlink(enabled), original)
            runner.side_effect = None
            release.site(True)
            self.assertTrue(os.readlink(enabled).endswith("trippilot-https"))
            with self.assertRaises(release.Refused): release.site(False)

    def test_real_git_archive_staging_excludes_env_data_bytecode_and_preserves_final_modes(self):
        source = self.root / "checkout"
        source.mkdir()
        for directory in ("frontend/src", "frontend/node_modules", "frontend/.next/cache", "frontend/.next/static", "backend/data", "backend/app/__pycache__", "deploy"):
            (source / directory).mkdir(parents=True, exist_ok=True)
        for name, body in {"frontend/src/app.ts": "source", "frontend/.env.local": "never-copy", "backend/.env": "never-copy", "backend/data/old.db": "never-copy", "backend/app/__pycache__/old.pyc": "never-copy", "frontend/node_modules/module.js": "module", "frontend/.next/cache/old": "cache", "frontend/.next/static/chunk.js": "build"}.items():
            (source / name).write_text(body)
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        subprocess.run(["git", "-C", str(source), "add", "."], check=True)
        subprocess.run(["git", "-C", str(source), "-c", "user.name=ControlledTest", "-c", "user.email=controlled@example.invalid", "commit", "-qm", "fixture"], check=True)
        commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
        releases = self.root / "releases"
        releases.mkdir()
        receipt = self.root / "receipt.json"
        receipt.write_text(json.dumps({"commit": commit}))
        original_run = release.run
        def controlled_run(argv, **kwargs):
            if argv[0] == "/usr/bin/node": return ""  # Linux verifier separately tested by Node fixtures.
            return original_run(argv, **kwargs)
        fake_user = type("Account", (), {"pw_uid": os.getuid(), "pw_gid": os.getgid()})()
        with patch.object(release, "SOURCE", source), patch.object(release, "RELEASES", releases), patch.object(release, "run", side_effect=controlled_run), patch.object(release.pwd, "getpwnam", return_value=fake_user), patch("config.check_files", return_value={"frontend": {}}), patch.object(release.os, "fchown"):
            release.stage(commit, receipt)
            target = releases / commit
            self.assertTrue((target / "frontend/node_modules/module.js").exists())
            self.assertFalse((target / "frontend/.env.local").exists())
            self.assertFalse((target / "backend/.env").exists())
            self.assertFalse((target / "backend/data/old.db").exists())
            self.assertFalse((target / "backend/app/__pycache__/old.pyc").exists())
            self.assertEqual(list((target / "frontend/.next/cache").iterdir()), [])
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE((target / "frontend/src").stat().st_mode), 0o755)
            with self.assertRaises(release.Refused): release.stage(commit, receipt)

    def test_select_requires_inactive_services_and_expected_old_identity(self):
        commit = "a" * 40
        releases = self.root / "releases"
        target = self.ready_release(commit)
        inactive = subprocess.CompletedProcess([], 0, "LoadState=loaded\nActiveState=inactive")
        active = subprocess.CompletedProcess([], 0, "LoadState=loaded\nActiveState=active")
        with patch.object(release, "BASE", self.root), patch.object(release, "RELEASES", releases), patch.object(release, "admin") as admin, patch.object(release.subprocess, "run", return_value=active) as systemctl:
            with self.assertRaises(release.Refused): release.select(commit, "none")
            self.assertFalse((self.root / "current").is_symlink())
            systemctl.return_value = inactive
            release.select(commit, "none")
            self.assertEqual((self.root / "current").resolve(), target)
            with self.assertRaises(release.Refused): release.select(commit, "none")
            release.select(commit, commit)
            self.assertEqual((self.root / "current").resolve(), target)
            self.assertTrue(admin.called)

    def test_select_rejects_incomplete_changed_or_stale_verification_receipts(self):
        commit = "a" * 40
        target = self.ready_release(commit)
        with patch.object(release, "BASE", self.root), patch.object(release, "RELEASES", self.root / "releases"), patch.object(release, "admin") as admin, patch.object(release.subprocess, "run") as systemctl:
            for name in ("release.json", "backend-ready.json", "frontend-ready.json"):
                path = target / name
                original = path.read_text()
                for invalid in ("{}", "{", " " * 65537):
                    path.write_text(invalid)
                    with self.assertRaises((release.Refused, ValueError)): release.select(commit, "none")
                    self.assertFalse((self.root / "current").is_symlink())
                path.write_text(original)
            (target / "backend/uv.lock").write_text("changed-lock")
            with self.assertRaises(release.Refused): release.select(commit, "none")
            admin.assert_not_called()
            systemctl.assert_not_called()


class EnvironmentTemplates(unittest.TestCase):
    def values(self):
        return (config.parse((DEPLOY / "env/backend.env.example").read_text(), config.BACKEND),
                config.parse((DEPLOY / "env/frontend.env.example").read_text(), config.FRONTEND))

    def test_templates_parse_exact_https_origin_and_preserve_json_quotes(self):
        back, front = self.values()
        config.validate(back, front)
        self.assertEqual(json.loads(back["AUTH_ALLOWED_ORIGINS"]), ["https://trip.zijinglab.com"])
        self.assertEqual(len(json.loads(back["BUSINESS_DAILY_LIMITS"])), 8)
        self.assertTrue(set(front).isdisjoint({"QWEN_API_KEY", "AMAP_WEB_KEY", "AMAP_JS_SECURITY_CODE"}))

    def test_not_shell_dotenv_or_unbounded_configuration(self):
        for text in ("export X=value", "X=a\nX=b", 'X="value"', "X=$(whoami)", "X=value #comment", "X='a\\nb'", "LD_PRELOAD=private"):
            with self.assertRaises(config.ConfigError): config.parse(text, frozenset({"X"}))
        with self.assertRaises(config.ConfigError): config.parse("#" * 20000, frozenset())

    def test_secure_paths_origins_models_and_duplicate_limits_fail_closed(self):
        for key, bad in (("AUTH_COOKIE_SECURE", "false"), ("AUTH_ALLOWED_ORIGINS", '["http://trip.zijinglab.com"]'),
                         ("TRIPPILOT_DATABASE_PATH", "/tmp/new.sqlite3"), ("QWEN_MODEL", "different-model"),
                         ("BUSINESS_DAILY_LIMITS", '{"amap_poi":{},"amap_poi":{}}')):
            back, front = self.values()
            back[key] = bad
            with self.assertRaises((config.ConfigError, ValueError)): config.validate(back, front)


if __name__ == "__main__": unittest.main()
