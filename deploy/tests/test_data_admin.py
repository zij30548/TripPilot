import os
from pathlib import Path
import sqlite3
from contextlib import closing
import sys
import tempfile
import unittest
from unittest.mock import patch

DEPLOY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEPLOY))
sys.path.insert(0, str(DEPLOY.parent / "backend"))
import data_admin
from backup import inspect_pair
from core.business_usage import UsageSettings, UsageStore
from memory.storage import engine_for


class ExplicitDataInitialization(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.root.chmod(0o700)
        self.env = patch.dict(os.environ, {"TRIPPILOT_DATABASE_PATH": str(self.root / "identity.sqlite3"), "BUSINESS_USAGE_PATH": str(self.root / "usage.sqlite3"), "TRIPPILOT_MYSQL_URL": "", "BUSINESS_USAGE_IDENTITY_PATH": ""})
        self.env.start()

    def tearDown(self):
        engine_for.cache_clear()
        self.env.stop()
        self.temp.cleanup()

    def test_explicit_new_then_read_only_existing_identity(self):
        self.assertIn("initialized", data_admin.initialize_identity(self.root))
        path = self.root / "identity.sqlite3"
        before = path.read_bytes()
        with patch("memory.storage.engine_for", side_effect=AssertionError("existing must not initialize")):
            self.assertIn("read-only", data_admin.initialize_identity(self.root))
        self.assertEqual(before, path.read_bytes())

    def test_existing_corrupt_or_orphan_is_not_overwritten(self):
        path = self.root / "identity.sqlite3"
        path.write_bytes(b"corrupt")
        path.chmod(0o600)
        with self.assertRaises(Exception): data_admin.initialize_identity(self.root)
        self.assertEqual(path.read_bytes(), b"corrupt")
        path.unlink()
        Path(str(path) + "-wal").touch()
        with self.assertRaises(ValueError): data_admin.initialize_identity(self.root)
        self.assertFalse(path.exists())

    def test_real_production_initial_schema_matches_backup_read_validator(self):
        data_admin.initialize_identity(self.root)
        UsageStore(UsageSettings()).initialize()
        result = inspect_pair(self.root)
        self.assertEqual(result["identity"]["sessions"], 0)
        self.assertEqual(result["usage"]["attempt_count"], 0)

    def test_usage_explicit_init_repeat_and_marker_prevents_lost_ledger_reset(self):
        data_admin.initialize_identity(self.root)
        self.assertIn("ONCE", data_admin.initialize_usage(self.root))
        before = (self.root / "usage.sqlite3").read_bytes()
        self.assertIn("verified", data_admin.initialize_usage(self.root))
        self.assertEqual(before, (self.root / "usage.sqlite3").read_bytes())
        (self.root / "usage.sqlite3").unlink()
        (self.root / "usage.sqlite3.identity").unlink()
        with self.assertRaises(ValueError): data_admin.initialize_usage(self.root)
        self.assertFalse((self.root / "usage.sqlite3").exists())

    def test_broken_sidecar_links_refuse_first_init(self):
        for base, action in (("identity.sqlite3", data_admin.initialize_identity), ("usage.sqlite3", data_admin.initialize_usage)):
            sidecar = self.root / (base + "-wal")
            sidecar.symlink_to("missing")
            with self.assertRaises(ValueError): action(self.root)
            self.assertFalse((self.root / base).exists())
            sidecar.unlink()

    def test_existing_accounts_without_ledger_must_not_receive_new_allowance(self):
        data_admin.initialize_identity(self.root)
        with closing(sqlite3.connect(self.root / "identity.sqlite3")) as db:
            db.execute("INSERT INTO accounts VALUES (?, ?, ?, ?, ?)", ("a" * 32, "account", "not-a-real-hash", 0, 0))
            db.commit()
        with self.assertRaises(ValueError): data_admin.initialize_usage(self.root)
        self.assertFalse((self.root / "usage.sqlite3").exists())

    def test_lost_identity_is_not_recreated_alongside_existing_usage(self):
        (self.root / ".usage-initialized").touch(mode=0o600)
        with self.assertRaises(ValueError): data_admin.initialize_identity(self.root)
        self.assertFalse((self.root / "identity.sqlite3").exists())

    def test_mysql_selection_refuses_both_sqlite_initializers_before_any_files(self):
        with patch.dict(os.environ, {"TRIPPILOT_MYSQL_URL": "mysql+pymysql://synthetic-not-a-connection"}):
            for initializer in (data_admin.initialize_identity, data_admin.initialize_usage):
                with self.assertRaisesRegex(ValueError, "mysql_requires_explicit_migration"):
                    initializer(self.root)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_mysql_verify_calls_actual_mysql_validator_not_stale_sqlite_pair(self):
        with patch.dict(os.environ, {"TRIPPILOT_MYSQL_URL": "mysql+pymysql://synthetic-not-a-connection"}), \
                patch("tools.mysql_migration.verify") as mysql_verify, patch("backup.inspect_pair") as sqlite_verify:
            self.assertIn("MySQL", data_admin.verify_active_storage(self.root))
            mysql_verify.assert_called_once_with()
            sqlite_verify.assert_not_called()
            mysql_verify.side_effect = ValueError("synthetic failure")
            with self.assertRaises(ValueError): data_admin.verify_active_storage(self.root)
            sqlite_verify.assert_not_called()

    def test_sqlite_verification_remains_read_only_when_no_mysql_selected(self):
        with patch("backup.inspect_pair") as sqlite_verify:
            self.assertIn("verified read-only", data_admin.verify_active_storage(self.root))
            sqlite_verify.assert_called_once_with(self.root)


if __name__ == "__main__": unittest.main()
