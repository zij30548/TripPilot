"""Synthetic private databases only; no live files, systemd or external calls."""
from __future__ import annotations

import ast
from contextlib import closing
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "deploy"))
SPEC = importlib.util.spec_from_file_location("release_backup", ROOT / "deploy" / "backup.py")
assert SPEC is not None and SPEC.loader is not None
backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup)


class BackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="trippilot-backup-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.live = self.root / "synthetic-live"
        self.backups = self.root / "private-backups"
        self.live.mkdir(mode=0o700)
        self.backups.mkdir(mode=0o700)
        self.batch = self.backups / "batch-one"
        self.stopped = Mock()
        self.make_pair(self.live)

    def make_pair(self, directory: Path) -> None:
        identity = directory / "identity.sqlite3"
        backup.write_new(identity, b"")
        with closing(sqlite3.connect(identity)) as db:
            for table, columns in backup.IDENTITY_COLUMNS.items():
                definition: list[str] = []
                for column in sorted(columns):
                    nullable = table == "saved_trips" and column == "owner_id"
                    primary = column == ("key" if table == "storage_metadata" else "id")
                    field = column + " TEXT" + ("" if nullable else " NOT NULL") + (" PRIMARY KEY" if primary else "")
                    if (table, column) in {("accounts", "username"), ("auth_sessions", "token_hash")}:
                        field += " UNIQUE"
                    if (table, column) in {("saved_trips", "owner_id"), ("auth_sessions", "user_id")}:
                        field += " REFERENCES accounts(id)"
                    definition.append(field)
                if table == "saved_trips":
                    definition.append("UNIQUE(owner_id,operation_id)")
                db.execute(f"CREATE TABLE {table} ({','.join(definition)})")
            db.execute("INSERT INTO storage_metadata VALUES ('version','identity-v1')")
            db.execute("INSERT INTO accounts (id,username,password_hash,disabled,created_at) VALUES ('owner','synthetic','synthetic-not-a-password',0,100)")
            db.execute("INSERT INTO auth_sessions (id,token_hash,user_id,csrf_token,expires_at,created_at) VALUES ('session','synthetic-hash','owner','synthetic-token',1000,100)")
            db.execute("INSERT INTO login_buckets VALUES ('bucket',4,99)")
            values = tuple(None if column == "owner_id" else "synthetic" for column in sorted(backup.IDENTITY_COLUMNS["saved_trips"]))
            db.execute("INSERT INTO saved_trips VALUES (" + ",".join("?" for _ in values) + ")", values)
            db.commit()
        usage = directory / "usage.sqlite3"
        backup.write_new(usage, b"")
        with closing(sqlite3.connect(usage)) as db:
            for sql in backup.USAGE_SQL.values():
                db.execute(sql)
            db.executemany("INSERT INTO usage_metadata VALUES (?,?)", (("version", "business-usage-v1"), ("identity", "a" * 32)))
            db.execute("PRAGMA user_version=1")
            for number, state in enumerate(("reserved", "dispatching", "received", "failed", "cancelled")):
                db.execute("INSERT INTO usage_attempts VALUES (?,?,?,?,?,?)", (f"{number:032x}", "b" * 32, "qwen_text", 100 + number, state, None if number == 0 else 110 + number))
            db.commit()
        backup.write_new(directory / "usage.sqlite3.identity", ("a" * 32 + "\n").encode("ascii"))

    def create_backup(self) -> None:
        backup.backup_pair(self.live, self.batch, ensure_stopped=self.stopped)

    def test_backup_is_consistent_private_and_does_not_change_source(self) -> None:
        before = {name: backup.sha256(self.live / name) for name in backup.FILES}
        self.create_backup()
        manifest = backup.verify_backup(self.batch)
        self.assertEqual({name: backup.sha256(self.live / name) for name in backup.FILES}, before)
        self.assertEqual(self.stopped.call_count, 2)
        self.assertEqual(manifest["summary"]["usage"]["attempt_count"], 5)
        self.assertEqual(manifest["summary"]["usage"]["max_created_at"], 104)
        self.assertEqual(manifest["summary"]["usage"]["max_dispatched_at"], 114)
        self.assertEqual(self.batch.stat().st_mode & 0o777, 0o700)
        for path in self.batch.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        # No password/session values are in the manifest or safe summaries.
        text = (self.batch / "manifest.json").read_text()
        self.assertNotIn("synthetic-hash", text)
        self.assertNotIn("synthetic-token", text)
        self.assertNotIn("synthetic-not-a-password", text)

    def test_repeat_never_overwrites_existing_batch(self) -> None:
        self.create_backup()
        before = backup.sha256(self.batch / "manifest.json")
        with self.assertRaises(FileExistsError):
            self.create_backup()
        self.assertEqual(backup.sha256(self.batch / "manifest.json"), before)

    def test_not_stopped_never_creates_destination(self) -> None:
        self.stopped.side_effect = backup.Refused("stopped")
        with self.assertRaises(backup.Refused):
            self.create_backup()
        self.assertFalse(self.batch.exists())

    def test_source_mutation_fails_closed_and_leaves_private_partial(self) -> None:
        def change_on_second_check() -> None:
            if self.stopped.call_count == 2:
                with closing(sqlite3.connect(self.live / "identity.sqlite3")) as db:
                    db.execute("UPDATE accounts SET username='changed'")
                    db.commit()
        self.stopped.side_effect = change_on_second_check
        with self.assertRaisesRegex(backup.Refused, "source_changed"):
            self.create_backup()
        self.assertTrue(self.batch.is_dir())
        self.assertFalse((self.batch / "manifest.json").exists())

    def test_committed_wal_data_is_included(self) -> None:
        with closing(sqlite3.connect(self.live / "identity.sqlite3")) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("UPDATE accounts SET username='committed-in-wal'")
            db.commit()
            self.create_backup()
        with closing(sqlite3.connect(self.batch / "identity.sqlite3")) as copied:
            self.assertEqual(copied.execute("SELECT username FROM accounts").fetchone()[0], "committed-in-wal")

    def test_recovery_revokes_sessions_preserves_throttle_archives_and_usage(self) -> None:
        self.create_backup()
        destination = self.backups / "isolated-drill"
        backup.restore_drill(self.batch, destination)
        self.assertEqual(backup.inspect_pair(destination)["identity"]["sessions"], 0)
        self.assertEqual(backup.inspect_pair(self.live)["identity"]["sessions"], 1)
        for name in backup.FILES[1:]:
            self.assertEqual(backup.sha256(destination / name), backup.sha256(self.batch / name))
        with closing(sqlite3.connect(destination / "identity.sqlite3")) as restored:
            self.assertEqual(restored.execute("SELECT COUNT(*) FROM accounts").fetchone()[0], 1)
            self.assertEqual(restored.execute("SELECT COUNT(*) FROM saved_trips").fetchone()[0], 1)
            self.assertEqual(restored.execute("SELECT COUNT(*) FROM login_buckets").fetchone()[0], 1)
        receipt = json.loads((destination / "drill.json").read_text())
        self.assertFalse(receipt["production_replacement_allowed"])
        with self.assertRaises(FileExistsError):
            backup.restore_drill(self.batch, destination)

    def test_live_recovery_and_source_overlap_refused(self) -> None:
        self.create_backup()
        with self.assertRaises(backup.Refused):
            backup.restore_drill(self.batch, backup.LIVE / "danger")
        with self.assertRaises(backup.Refused):
            backup.restore_drill(self.batch, self.batch / "nested")

    def test_symlink_file_directory_and_sidecar_refused(self) -> None:
        (self.backups / "linked").symlink_to(self.live, target_is_directory=True)
        with self.assertRaises(backup.Refused):
            backup.inspect_pair(self.backups / "linked")
        anchor = self.live / "usage.sqlite3.identity"
        anchor.rename(self.live / "real-anchor")
        anchor.symlink_to(self.live / "real-anchor")
        with self.assertRaises(backup.Refused):
            self.create_backup()
        anchor.unlink()
        (self.live / "real-anchor").rename(anchor)
        (self.live / "identity.sqlite3-wal").symlink_to(anchor)
        with self.assertRaises(backup.Refused):
            self.create_backup()

    def test_world_readable_or_hardlinked_database_refused(self) -> None:
        path = self.live / "usage.sqlite3"
        path.chmod(0o640)
        with self.assertRaises(backup.Refused):
            self.create_backup()
        path.chmod(0o600)
        os.link(path, self.live / "linked.sqlite3")
        with self.assertRaises(backup.Refused):
            self.create_backup()

    def test_corrupt_source_and_wrong_anchor_refused_without_destination(self) -> None:
        anchor = self.live / "usage.sqlite3.identity"
        anchor.write_text("c" * 32 + "\n")
        with self.assertRaises(backup.Refused):
            self.create_backup()
        anchor.write_text("a" * 32 + "\n")
        (self.live / "identity.sqlite3").write_bytes(b"not sqlite")
        with self.assertRaises(sqlite3.Error):
            self.create_backup()
        self.assertFalse(self.batch.exists())

    def test_hash_tampering_and_manifest_duplicates_refused(self) -> None:
        self.create_backup()
        anchor = self.batch / "usage.sqlite3.identity"
        anchor.write_text("c" * 32 + "\n")
        with self.assertRaisesRegex(backup.Refused, "backup_hash"):
            backup.verify_backup(self.batch)
        anchor.write_text("a" * 32 + "\n")
        manifest = self.batch / "manifest.json"
        manifest.write_text('{"version":"a","version":"b"}')
        with self.assertRaisesRegex(backup.Refused, "duplicate"):
            backup.verify_backup(self.batch)

    def test_usage_schema_and_version_rejected(self) -> None:
        with closing(sqlite3.connect(self.live / "usage.sqlite3")) as db:
            db.execute("DROP INDEX usage_created")
            db.commit()
        with self.assertRaisesRegex(backup.Refused, "usage_schema"):
            self.create_backup()

    def test_global_idempotency_constraint_and_session_fk_tampering_rejected(self) -> None:
        with closing(sqlite3.connect(self.live / "identity.sqlite3")) as db:
            db.execute("CREATE UNIQUE INDEX wrong_global_operation ON saved_trips(operation_id)")
            db.commit()
        with self.assertRaisesRegex(backup.Refused, "identity_constraints"):
            self.create_backup()

    def test_unknown_schema_version_and_incomplete_backup_refused(self) -> None:
        with closing(sqlite3.connect(self.live / "identity.sqlite3")) as db:
            db.execute("UPDATE storage_metadata SET value='future-identity'")
            db.commit()
        with self.assertRaisesRegex(backup.Refused, "identity_version"):
            self.create_backup()
        incomplete = self.backups / "incomplete"
        incomplete.mkdir(mode=0o700)
        with self.assertRaises(FileNotFoundError):
            backup.verify_backup(incomplete)

    def test_schema_constants_match_current_app_without_importing_it(self) -> None:
        tree = ast.parse((ROOT / "backend/app/business_usage.py").read_text())
        values = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
                  if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                  and node.targets[0].id in {"_TABLES", "_INDEXES"}}
        expected = {("table", key): value for key, value in values["_TABLES"].items()}
        expected.update({("index", key): value for key, value in values["_INDEXES"].items()})
        self.assertEqual(backup.USAGE_SQL, expected)

    def test_systemd_requires_loaded_inactive_dead_not_failed_missing_or_running(self) -> None:
        for state in ("LoadState=loaded\nActiveState=active\nSubState=running", "LoadState=not-found\nActiveState=inactive\nSubState=dead", "LoadState=loaded\nActiveState=failed\nSubState=failed"):
            with self.subTest(state=state), patch.object(backup.subprocess, "run", return_value=Mock(returncode=0, stdout=state)):
                with self.assertRaises(backup.Refused):
                    backup.systemd_stopped()
        with patch.object(backup.subprocess, "run", return_value=Mock(returncode=0, stdout="LoadState=loaded\nActiveState=inactive\nSubState=dead")) as command:
            backup.systemd_stopped()
            self.assertEqual(command.call_args.args[0][0], "/usr/bin/systemctl")
            self.assertIn("trippilot-api.service", command.call_args.args[0])
            self.assertNotIn("trippilot-backend.service", command.call_args.args[0])

    def test_sqlite_cli_reads_root_service_configuration_instead_of_inherited_environment(self) -> None:
        # All text and ownership metadata below are synthetic; never read /etc.
        config_text = (ROOT / "deploy/env/backend.env.example").read_text()
        path = Mock(spec=Path)
        path.read_text.return_value = config_text
        path.stat.return_value = SimpleNamespace(st_uid=0, st_gid=0, st_mode=0o100600, st_size=len(config_text))
        with patch.object(backup, "private_file") as private, patch.dict(os.environ, {"TRIPPILOT_MYSQL_URL": "ignored-inherited-value"}):
            backup.require_sqlite_backend(path)
            private.assert_called_once_with(path)
        path.read_text.return_value = config_text.replace("TRIPPILOT_MYSQL_URL=", "TRIPPILOT_MYSQL_URL=mysql+pymysql://trippilot:synthetic_URLsafe_only_32_chars@127.0.0.1:3306/trippilot") \
            .replace("BUSINESS_USAGE_IDENTITY_PATH=", "BUSINESS_USAGE_IDENTITY_PATH=/var/lib/trippilot/usage.sqlite3.identity")
        with patch.object(backup, "private_file"), patch.dict(os.environ, {"TRIPPILOT_MYSQL_URL": ""}):
            with self.assertRaisesRegex(backup.Refused, "active_mysql_requires_mysql_backup_tool"):
                backup.require_sqlite_backend(path)

    def test_service_config_missing_unsafe_or_unknown_never_falls_back_to_old_sqlite_files(self) -> None:
        with self.assertRaises(FileNotFoundError): backup.require_sqlite_backend(self.root / "missing.env")
        path = Mock(spec=Path)
        path.read_text.return_value = (ROOT / "deploy/env/backend.env.example").read_text()
        for uid, gid, mode in ((1, 0, 0o100600), (0, 1, 0o100600), (0, 0, 0o100640)):
            path.stat.return_value = SimpleNamespace(st_uid=uid, st_gid=gid, st_mode=mode, st_size=100)
            with patch.object(backup, "private_file"), self.assertRaisesRegex(backup.Refused, "root_backend_configuration_required"):
                backup.require_sqlite_backend(path)
        path.stat.return_value = SimpleNamespace(st_uid=0, st_gid=0, st_mode=0o100600, st_size=100)
        path.read_text.return_value = "UNRECOGNIZED=backend\n"
        with patch.object(backup, "private_file"), self.assertRaises(ValueError): backup.require_sqlite_backend(path)

    def test_active_mysql_refuses_sqlite_backup_before_destination_creation(self) -> None:
        import sys
        argv = ["backup.py", "backup", "--batch", "controlled-only", "--confirm-no-other-writers"]
        with patch.object(sys, "argv", argv), patch.object(backup, "production_root"), \
                patch.object(backup, "require_sqlite_backend", side_effect=backup.Refused("active_mysql_requires_mysql_backup_tool")) as selected, \
                patch.object(backup, "backup_pair") as create:
            self.assertEqual(backup.main(), 1)
            selected.assert_called_once_with()
            create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
