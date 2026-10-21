"""Real, independent SQLite quota storage; no network or user databases."""
import concurrent.futures
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from core.business_usage import (DEFAULT_PATH, PRIVATE_PATH, LEGACY_PATH, SERVICES,
                                UsageError, UsageSettings, UsageStore, get_usage_settings)

OWNER_A = "a" * 32
OWNER_B = "b" * 32
NOW = int(datetime(2026, 10, 18, 12, 0, 30, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())


def settings(path: Path, *, account: int = 100, global_limit: int = 1000, **kwargs) -> UsageSettings:
    limits = {service: {"account": account, "global": global_limit} for service in SERVICES}
    return UsageSettings(_env_file=None, business_usage_path=str(path), business_daily_limits=limits,
                         business_rate_account_limit=1000, business_rate_global_limit=2000, **kwargs)


class PersistentBusinessUsage(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "usage.sqlite3"
        self.config = settings(self.path)
        self.store = UsageStore(self.config, clock=lambda: NOW)
        self.store.initialize()

    def tearDown(self):
        self.directory.cleanup()

    def error(self, code, function, *args):
        with self.assertRaises(UsageError) as caught:
            function(*args)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def count(self):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute("SELECT count(*) FROM usage_attempts").fetchone()[0]

    def test_explicit_init_only_reopen_and_private_permissions(self):
        self.store.reserve(OWNER_A, "amap_poi")
        reopened = UsageStore(self.config, clock=lambda: NOW)
        reopened.verify()
        self.assertEqual(reopened.own_usage(OWNER_A)["services"]["amap_poi"]["used"], 1)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(Path(str(self.path) + ".identity").stat().st_mode & 0o777, 0o600)
        self.error("business_usage_unavailable", reopened.initialize)
        self.assertEqual(self.count(), 1)

    def test_daily_account_global_and_service_isolation(self):
        store = UsageStore(settings(self.path, account=2, global_limit=3), clock=lambda: NOW)
        store.reserve(OWNER_A, "amap_route")
        store.reserve(OWNER_A, "amap_route")
        account = self.error("business_quota_exhausted", store.reserve, OWNER_A, "amap_route")
        self.assertEqual(account.scope, "account")
        store.reserve(OWNER_B, "amap_route")
        global_error = self.error("business_quota_exhausted", store.reserve, OWNER_B, "amap_route")
        self.assertEqual(global_error.scope, "global")
        store.reserve(OWNER_A, "amap_weather")
        own = store.own_usage(OWNER_B)
        self.assertEqual(own["services"]["amap_route"], {"used": 1, "limit": 2, "remaining": 1})
        self.assertNotIn("global", json.dumps(own))
        self.assertNotIn(OWNER_A, json.dumps(own))
        self.assertEqual(own["services"]["amap_weather"]["used"], 0)
        self.assertEqual(global_error.status, 429)
        self.assertGreater(global_error.retry_after_seconds, 0)

    def test_zero_disables_without_fake_wait_and_unknown_service_refused(self):
        store = UsageStore(settings(self.path, account=0), clock=lambda: NOW)
        error = self.error("business_service_disabled", store.reserve, OWNER_A, "qwen_text")
        self.assertIsNone(error.retry_after_seconds)
        self.assertIsNone(error.reset_at)
        self.error("business_usage_unavailable", store.reserve, OWNER_A, "arbitrary")
        self.error("business_usage_unavailable", store.reserve, "owner-from-client", "amap_poi")
        self.assertEqual(self.count(), 0)

    def test_short_window_is_shared_across_services_and_survives_restart(self):
        config = settings(self.path)
        config.business_rate_account_limit = 2
        config.business_rate_global_limit = 3
        store = UsageStore(config, clock=lambda: NOW)
        store.reserve(OWNER_A, "amap_poi")
        store.reserve(OWNER_A, "qwen_text")
        reopened = UsageStore(config, clock=lambda: NOW + 1)
        error = self.error("business_rate_limited", reopened.reserve, OWNER_A, "amap_weather")
        self.assertEqual((error.scope, error.retry_after_seconds), ("account", 29))
        reopened.reserve(OWNER_B, "media_video")
        self.assertEqual(self.error("business_rate_limited", reopened.reserve, OWNER_B, "amap_route").scope, "global")
        next_window = UsageStore(config, clock=lambda: NOW + 60)
        next_window.reserve(OWNER_A, "amap_route")
        self.assertEqual(next_window.own_usage(OWNER_A)["services"]["amap_poi"]["used"], 1)

    def test_shanghai_midnight_not_utc_and_calendar_reset(self):
        before = int(datetime(2026, 12, 31, 23, 59, 59, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())
        config = settings(self.path, account=1)
        store = UsageStore(config, clock=lambda: before)
        store.reserve(OWNER_A, "qwen_vision")
        self.error("business_quota_exhausted", store.reserve, OWNER_A, "qwen_vision")
        view = store.own_usage(OWNER_A)
        self.assertEqual(view["reset_at"], "2027-01-01T00:00:00+08:00")
        after = UsageStore(config, clock=lambda: before + 1)
        self.assertEqual(after.own_usage(OWNER_A)["services"]["qwen_vision"]["used"], 0)
        after.reserve(OWNER_A, "qwen_vision")
        self.assertEqual(self.count(), 2)

    def test_failed_cancelled_uncertain_and_reserved_all_keep_budget(self):
        config = settings(self.path, account=4)
        store = UsageStore(config, clock=lambda: NOW)
        reserved = store.reserve(OWNER_A, "amap_route")
        sent_failed = store.reserve(OWNER_A, "amap_route")
        store.mark(sent_failed, "dispatching")
        store.mark(sent_failed, "failed")
        sent_cancelled = store.reserve(OWNER_A, "amap_route")
        store.mark(sent_cancelled, "dispatching")
        store.mark(sent_cancelled, "cancelled")
        not_sent = store.reserve(OWNER_A, "amap_route")
        store.mark(not_sent, "cancelled")
        store.mark(not_sent, "cancelled")
        self.error("business_quota_exhausted", store.reserve, OWNER_A, "amap_route")
        with closing(sqlite3.connect(self.path)) as db:
            rows = {row[0]: row[1:] for row in db.execute("SELECT id, state, dispatched_at FROM usage_attempts")}
        self.assertEqual(rows[reserved], ("reserved", None))
        self.assertEqual(rows[sent_failed], ("failed", NOW))
        self.assertEqual(rows[sent_cancelled], ("cancelled", NOW))
        self.assertEqual(rows[not_sent], ("cancelled", None))
        self.assertEqual(store.own_usage(OWNER_A)["services"]["amap_route"]["used"], 4)

    def test_state_machine_and_success_record(self):
        attempt = self.store.reserve(OWNER_A, "media_photo")
        self.error("business_usage_unavailable", self.store.mark, attempt, "received")
        self.store.mark(attempt, "dispatching")
        self.error("business_usage_unavailable", self.store.mark, attempt, "dispatching")
        self.store.mark(attempt, "received")
        self.store.mark(attempt, "received")
        self.error("business_usage_unavailable", self.store.mark, attempt, "failed")
        self.error("business_usage_unavailable", self.store.mark, "f" * 32, "dispatching")
        self.error("business_usage_unavailable", self.store.mark, attempt, "unknown")

    def test_threads_cannot_overspend_and_rejected_reservations_do_not_insert(self):
        store = UsageStore(settings(self.path, account=7), clock=lambda: NOW)

        def reserve(_):
            try:
                store.reserve(OWNER_A, "amap_route")
                return True
            except UsageError:
                return False

        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            outcomes = list(pool.map(reserve, range(32)))
        self.assertEqual(sum(outcomes), 7)
        self.assertEqual(self.count(), 7)

    def test_two_processes_share_global_atomic_limit(self):
        script = """
import sys
from core.business_usage import UsageSettings, UsageStore, UsageError, SERVICES
path, owner, timestamp = sys.argv[1:]
limits={key:{'account':100,'global':7} for key in SERVICES}
config=UsageSettings(_env_file=None,business_usage_path=path,business_daily_limits=limits,business_rate_account_limit=100,business_rate_global_limit=100)
store=UsageStore(config,clock=lambda:int(timestamp))
success=0
for _ in range(14):
 try:
  store.reserve(owner,'amap_poi'); success+=1
 except UsageError as error:
  if error.code!='business_quota_exhausted': raise
print(success)
"""
        processes = [subprocess.Popen([sys.executable, "-B", "-c", script, str(self.path), owner, str(NOW)],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                     for owner in (OWNER_A, OWNER_B)]
        totals = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, stderr)
            totals.append(int(stdout.strip()))
        self.assertEqual(sum(totals), 7)
        self.assertEqual(self.count(), 7)

    def test_missing_file_and_anchor_cannot_reset_and_runtime_never_creates(self):
        self.store.reserve(OWNER_A, "amap_weather")
        self.path.unlink()
        self.error("business_usage_unavailable", self.store.reserve, OWNER_A, "amap_weather")
        self.error("business_usage_unavailable", self.store.initialize)
        self.assertFalse(self.path.exists())
        absent = UsageStore(settings(Path(self.directory.name) / "never-created.sqlite3"))
        self.error("business_usage_unavailable", absent.reserve, OWNER_A, "amap_route")
        self.assertFalse(Path(absent.settings.business_usage_path).exists())

    def test_missing_mismatched_or_bad_anchor_refuses_all_operations(self):
        anchor = Path(str(self.path) + ".identity")
        original = anchor.read_bytes()
        for content in (b"x", b"0" * 32 + b"\n", b"a" * 100):
            anchor.write_bytes(content)
            self.error("business_usage_unavailable", self.store.reserve, OWNER_A, "amap_poi")
            self.error("business_usage_unavailable", self.store.own_usage, OWNER_A)
        anchor.write_bytes(original)
        self.store.verify()
        anchor.unlink()
        self.error("business_usage_unavailable", self.store.initialize)
        self.error("business_usage_unavailable", self.store.verify)

    def test_corrupt_schema_wrong_version_or_io_failure_fail_closed(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE usage_metadata SET value='other' WHERE key='version'")
        self.error("business_usage_unavailable", self.store.reserve, OWNER_A, "amap_route")
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE usage_metadata SET value='business-usage-v1' WHERE key='version'")
            db.execute("CREATE TABLE unexpected (id TEXT)")
        self.error("business_usage_unavailable", self.store.verify)
        with patch("core.business_usage.sqlite3.connect", side_effect=sqlite3.OperationalError("sensitive path sql")):
            error = self.error("business_usage_unavailable", self.store.reserve, OWNER_A, "amap_route")
        self.assertNotIn("sensitive", json.dumps(error.detail()))
        self.assertEqual(error.status, 503)

    def test_lock_timeout_does_not_create_untracked_allowance(self):
        with closing(sqlite3.connect(self.path, isolation_level=None)) as db:
            db.execute("BEGIN IMMEDIATE")
            self.error("business_usage_unavailable", self.store.reserve, OWNER_A, "amap_poi")
            db.rollback()
        self.assertEqual(self.count(), 0)
        self.store.reserve(OWNER_A, "amap_poi")

    def test_consistent_backup_and_matched_anchor_preserve_counts(self):
        self.store.reserve(OWNER_A, "amap_weather")
        copy_path = Path(self.directory.name) / "backup.sqlite3"
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as source, closing(sqlite3.connect(copy_path)) as target:
            source.backup(target)
        Path(str(copy_path) + ".identity").write_bytes(Path(str(self.path) + ".identity").read_bytes())
        copied = UsageStore(settings(copy_path), clock=lambda: NOW)
        copied.verify()
        self.assertEqual(copied.own_usage(OWNER_A)["services"]["amap_weather"]["used"], 1)
        copied.reserve(OWNER_A, "amap_weather")
        self.assertEqual(self.store.own_usage(OWNER_A)["services"]["amap_weather"]["used"], 1)

    def test_invalid_paths_and_private_database_never_written(self):
        protected = Path(self.directory.name) / "private.sqlite3"
        protected.write_bytes(b"preserve existing identity/archive contents")
        digest = hashlib.sha256(protected.read_bytes()).hexdigest()
        with patch.dict(os.environ, {"TRIPPILOT_DATABASE_PATH": str(protected)}):
            for path in (protected, Path(str(protected) + "-wal"), Path(str(protected) + "-journal"),
                         LEGACY_PATH, PRIVATE_PATH, Path("relative.sqlite3")):
                store = UsageStore(settings(path))
                self.error("business_usage_unavailable", store.initialize)
            hardlink = Path(self.directory.name) / "hardlink.sqlite3"
            os.link(protected, hardlink)
            self.error("business_usage_unavailable", UsageStore(settings(hardlink)).reserve, OWNER_A, "amap_poi")
        self.assertEqual(hashlib.sha256(protected.read_bytes()).hexdigest(), digest)
        link = Path(self.directory.name) / "link.sqlite3"
        link.symlink_to(self.path)
        self.error("business_usage_unavailable", UsageStore(settings(link)).reserve, OWNER_A, "amap_poi")
        self.assertTrue(DEFAULT_PATH.is_absolute())

    def test_clock_rollback_does_not_restore_earlier_window_or_day(self):
        self.store.reserve(OWNER_A, "amap_route")
        back = UsageStore(self.config, clock=lambda: NOW - 86400)
        self.error("business_usage_unavailable", back.reserve, OWNER_B, "amap_poi")
        self.error("business_usage_unavailable", back.own_usage, OWNER_A)
        self.assertEqual(self.count(), 1)
        self.assertEqual(self.store.own_usage(OWNER_A)["services"]["amap_route"]["used"], 1)

    def test_strict_configuration_exact_services_environment_and_limits(self):
        for overrides in ({"business_rate_account_limit": True}, {"business_rate_window_seconds": 0},
                          {"business_rate_global_limit": 2 ** 40}, {"business_rate_account_limit": 1.1},
                          {"business_daily_limits": {}},
                          {"business_daily_limits": {key: {"account": 1, "global": 1, "extra": 1} for key in SERVICES}},
                          {"business_daily_limits": {key: {"account": True, "global": 1} for key in SERVICES}},
                          {"business_daily_limits": '{"x":1,"x":2}'}, {"business_daily_limits": '{"x":NaN}'}):
            with self.subTest(overrides=overrides), self.assertRaises(ValidationError):
                UsageSettings(_env_file=None, **overrides)
        with patch.dict(os.environ, {"BUSINESS_RATE_ACCOUNT_LIMIT": "2", "BUSINESS_RATE_GLOBAL_LIMIT": "10", "BUSINESS_RATE_WINDOW_SECONDS": "30"}):
            config = UsageSettings(_env_file=None)
        self.assertEqual((config.business_rate_account_limit, config.business_rate_global_limit, config.business_rate_window_seconds), (2, 10, 30))
        with patch("core.business_usage.UsageSettings", side_effect=ValueError("private environment")):
            self.error("business_usage_unavailable", get_usage_settings)

    def test_invalid_clock_and_cli_no_sensitive_error(self):
        for value in (float("nan"), float("inf"), -1, True):
            bad = UsageStore(self.config, clock=lambda: value)
            self.error("business_usage_unavailable", bad.reserve, OWNER_A, "qwen_text")
        output = subprocess.run([sys.executable, "-B", "-m", "tools.business_usage", "verify", "--database", str(self.path)], capture_output=True, text=True, timeout=10)
        self.assertEqual(output.returncode, 0, output.stderr)
        refused = subprocess.run([sys.executable, "-B", "-m", "tools.business_usage", "init", "--database", str(self.path)], capture_output=True, text=True, timeout=10)
        self.assertEqual(refused.returncode, 1)
        self.assertNotIn(str(self.path), refused.stdout + refused.stderr)
        self.assertEqual(self.count(), 0)

    def test_commit_rollback_and_close_failures_are_safe_and_stop_paid_step(self):
        real_connect = sqlite3.connect

        class FaultedConnection:
            def __init__(self, connection, commit_error=None, rollback_error=False, close_error=False):
                self.connection = connection
                self.commit_error, self.rollback_error, self.close_error = commit_error, rollback_error, close_error

            def __getattr__(self, name):
                return getattr(self.connection, name)

            def commit(self):
                if self.commit_error == "before":
                    raise sqlite3.OperationalError("private SQL and storage path before commit")
                self.connection.commit()
                if self.commit_error == "after":
                    raise sqlite3.OperationalError("private SQL and storage path after commit")

            def rollback(self):
                if self.rollback_error:
                    raise sqlite3.OperationalError("private SQL and storage path during rollback")
                self.connection.rollback()

            def close(self):
                self.connection.close()
                if self.close_error:
                    raise sqlite3.OperationalError("private SQL and storage path during close")

        cases = [
            ({"commit_error": "before"}, False, 0),
            ({"commit_error": "after"}, False, 1),
            ({"commit_error": "before", "rollback_error": True}, False, 0),
            ({"rollback_error": True}, True, 0),
            ({"close_error": True}, False, 1),
        ]
        for index, (failures, disabled, expected_rows) in enumerate(cases):
            with self.subTest(failures=failures):
                path = Path(self.directory.name) / f"failure-{index}.sqlite3"
                store = UsageStore(settings(path, account=0 if disabled else 100), clock=lambda: NOW)
                store.initialize()
                paid_steps = []

                def dispatch():
                    store.reserve(OWNER_A, "amap_route")
                    paid_steps.append("should not reach provider")

                with patch("core.business_usage.sqlite3.connect", side_effect=lambda *args, **kwargs: FaultedConnection(real_connect(*args, **kwargs), **failures)):
                    error = self.error("business_usage_unavailable", dispatch)
                self.assertEqual(error.status, 503)
                self.assertNotIn("private", json.dumps(error.detail()))
                self.assertEqual(paid_steps, [])
                with closing(real_connect(path)) as db:
                    self.assertEqual(db.execute("SELECT count(*) FROM usage_attempts").fetchone()[0], expected_rows)
                # A commit acknowledged only by storage remains conservatively
                # occupied even when the caller received a safe failure.
                self.assertEqual(store.own_usage(OWNER_A)["services"]["amap_route"]["used"], expected_rows)

    def test_initialize_close_failure_is_safe_and_never_reinitializes(self):
        real_connect = sqlite3.connect

        class CloseFailure:
            def __init__(self, connection):
                self.connection = connection

            def __getattr__(self, name):
                return getattr(self.connection, name)

            def close(self):
                self.connection.close()
                raise sqlite3.OperationalError("private database path at close")

        path = Path(self.directory.name) / "init-close.sqlite3"
        store = UsageStore(settings(path), clock=lambda: NOW)
        with patch("core.business_usage.sqlite3.connect", side_effect=lambda *args, **kwargs: CloseFailure(real_connect(*args, **kwargs))):
            error = self.error("business_usage_unavailable", store.initialize)
        self.assertNotIn("private", json.dumps(error.detail()))
        self.assertEqual(error.status, 503)
        store.verify()
        self.error("business_usage_unavailable", store.initialize)


if __name__ == "__main__":
    unittest.main()
