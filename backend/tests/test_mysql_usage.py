"""Real isolated InnoDB quotas; upstream transport remains controlled/offline."""
from __future__ import annotations

import asyncio
import concurrent.futures
import os
from pathlib import Path
import tempfile
import threading
import subprocess
import sys
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import OperationalError

from app.business_usage import SERVICES, UsageError, UsageSettings, UsageStore, get_usage_store
from app.mysql_usage import (MYSQL_USAGE_VERSION, MySQLUsageStore,
                             import_usage_rows, initialize_usage_schema)
from app.usage_runtime import MeteredTransport, bind_usage_context, reset_usage_context
from tests.mysql_support import temporary_mysql

OWNER_A, OWNER_B = "a" * 32, "b" * 32
NOW = 1792296030


class MySQLUsageTests(unittest.TestCase):
    def setUp(self):
        self.engine, close = temporary_mysql()
        self.addCleanup(close)
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.anchor = Path(self.directory) / "usage.identity"
        self.identity = uuid4().hex
        self.anchor.write_text(self.identity + "\n", encoding="ascii")
        self.anchor.chmod(0o600)
        self.settings = UsageSettings(_env_file=None,
            business_usage_path=str(Path(self.directory) / "source-no-longer-required.sqlite3"),
            business_usage_identity_path=str(self.anchor),
            business_rate_account_limit=1000, business_rate_global_limit=2000,
            business_daily_limits={service: {"account": 100, "global": 1000} for service in SERVICES})
        initialize_usage_schema(self.engine, self.identity)
        self.store = MySQLUsageStore(self.settings, clock=lambda: NOW, engine=self.engine)

    def count(self):
        with self.engine.connect() as connection:
            return connection.execute(text("SELECT count(*) FROM usage_attempts")).scalar_one()

    def test_reopen_durable_own_summary_and_no_runtime_initialization(self):
        attempt = self.store.reserve(OWNER_A, "amap_route")
        self.store.mark(attempt, "dispatching")
        self.store.mark(attempt, "received")
        self.engine.dispose()
        reopened = MySQLUsageStore(self.settings, clock=lambda: NOW, engine=self.engine)
        reopened.verify()
        summary = reopened.own_usage(OWNER_A)
        self.assertEqual(summary["version"], "business-usage-v1")
        self.assertEqual(summary["services"]["amap_route"]["used"], 1)
        self.assertNotIn("global", str(summary))
        with self.assertRaises(UsageError):
            reopened.initialize()
        with self.assertRaises(UsageError):
            initialize_usage_schema(self.engine, self.identity)
        self.assertEqual(self.count(), 1)

    def test_account_global_rate_and_daily_limits_reuse_existing_semantics(self):
        for service in SERVICES:
            self.settings.business_daily_limits[service] = self.settings.business_daily_limits[service].model_copy(update={"account": 2, "global_": 3})
        self.store.reserve(OWNER_A, "amap_route")
        self.store.reserve(OWNER_A, "amap_route")
        with self.assertRaises(UsageError) as caught:
            self.store.reserve(OWNER_A, "amap_route")
        self.assertEqual((caught.exception.code, caught.exception.scope), ("business_quota_exhausted", "account"))
        self.store.reserve(OWNER_B, "amap_route")
        with self.assertRaises(UsageError) as caught:
            self.store.reserve(OWNER_B, "amap_route")
        self.assertEqual((caught.exception.code, caught.exception.scope), ("business_quota_exhausted", "global"))
        self.settings.business_rate_account_limit = 2
        with self.assertRaises(UsageError) as caught:
            self.store.reserve(OWNER_A, "amap_poi")
        self.assertEqual(caught.exception.code, "business_rate_limited")
        self.assertEqual(self.count(), 3)

    def test_threads_compete_for_one_global_allowance_without_stale_snapshot(self):
        self.settings.business_daily_limits["amap_poi"] = self.settings.business_daily_limits["amap_poi"].model_copy(update={"global_": 7})
        barrier = threading.Barrier(8)

        def reserve(index):
            barrier.wait(10)
            try:
                self.store.reserve(OWNER_A if index % 2 else OWNER_B, "amap_poi")
                return True
            except UsageError as error:
                self.assertEqual(error.code, "business_quota_exhausted")
                return False

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(reserve, range(8)))
        self.assertEqual(sum(outcomes), 7)
        self.assertEqual(self.count(), 7)

    def test_independent_processes_share_global_limit(self):
        script = """
import os
from sqlalchemy import create_engine
from app.business_usage import SERVICES, UsageError, UsageSettings
from app.mysql_usage import MySQLUsageStore
settings=UsageSettings(_env_file=None,business_usage_identity_path=os.environ['TEST_USAGE_ANCHOR'],
 business_rate_account_limit=100,business_rate_global_limit=100,
 business_daily_limits={service:{'account':100,'global':7} for service in SERVICES})
engine=create_engine(os.environ['TEST_USAGE_URL'],hide_parameters=True)
store=MySQLUsageStore(settings,engine=engine,clock=lambda:1792296030)
success=0
for _ in range(14):
 try: store.reserve(os.environ['TEST_USAGE_OWNER'],'amap_route'); success+=1
 except UsageError as error:
  if error.code!='business_quota_exhausted': raise
engine.dispose()
print(success)
"""
        processes = []
        for owner in (OWNER_A, OWNER_B):
            environment = {**os.environ, "TEST_USAGE_URL": self.engine.url.render_as_string(hide_password=False),
                           "TEST_USAGE_ANCHOR": str(self.anchor), "TEST_USAGE_OWNER": owner}
            processes.append(subprocess.Popen([sys.executable, "-B", "-c", script],
                env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        outcomes = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, stderr)
            outcomes.append(int(stdout.strip()))
        self.assertEqual(sum(outcomes), 7)
        self.assertEqual(self.count(), 7)

    def test_commit_failure_before_or_after_ack_stops_dispatch_and_never_refunds(self):
        original = self.engine.dialect.do_commit
        for after, expected in ((False, 0), (True, 1)):
            with self.subTest(after_commit=after):
                def commit(connection):
                    if after:
                        original(connection)
                    raise OperationalError("private commit detail", {}, Exception("private"))
                sent = []
                with patch.object(self.engine.dialect, "do_commit", commit), self.assertRaises(UsageError) as caught:
                    self.store.reserve(OWNER_A, "qwen_text")
                    sent.append("must not execute")
                self.assertEqual(sent, [])
                self.assertNotIn("private", str(caught.exception.detail()))
                self.assertEqual(self.count(), expected)
        self.assertEqual(self.store.own_usage(OWNER_A)["services"]["qwen_text"]["used"], 1)

    def test_global_guard_lock_timeout_fails_closed_without_new_attempt(self):
        with self.engine.connect() as blocker, blocker.begin():
            blocker.execute(text("SELECT value FROM usage_metadata WHERE `key`='identity' FOR UPDATE"))
            with self.assertRaises(UsageError) as caught:
                self.store.reserve(OWNER_A, "media_video")
            self.assertEqual(caught.exception.code, "business_usage_unavailable")
        self.assertEqual(self.count(), 0)
        self.store.reserve(OWNER_A, "media_video")

    def test_missing_mismatched_anchor_version_schema_and_backend_failure_fail_closed(self):
        self.store.reserve(OWNER_A, "qwen_text")
        self.anchor.unlink()
        with self.assertRaises(UsageError):
            self.store.reserve(OWNER_A, "qwen_text")
        self.anchor.write_text(uuid4().hex + "\n")
        with self.assertRaises(UsageError):
            self.store.verify()
        self.anchor.write_text(self.identity + "\n")
        with self.engine.begin() as connection:
            connection.execute(text("UPDATE usage_metadata SET value='wrong' WHERE `key`='version'"))
        with self.assertRaises(UsageError):
            self.store.reserve(OWNER_A, "qwen_text")
        with self.engine.begin() as connection:
            connection.execute(text("UPDATE usage_metadata SET value=:v WHERE `key`='version'"), {"v": MYSQL_USAGE_VERSION})
        with patch.object(self.engine, "connect", side_effect=OperationalError("privateSQL", {}, Exception("privatepassword"))):
            with self.assertRaises(UsageError) as caught:
                self.store.reserve(OWNER_A, "qwen_text")
        self.assertNotIn("private", str(caught.exception.detail()))
        self.assertEqual(self.count(), 1)

    def test_unknown_attempt_state_and_conservative_failure_cancel_never_refund(self):
        pending = self.store.reserve(OWNER_A, "amap_poi")
        cancelled = self.store.reserve(OWNER_A, "amap_poi")
        self.store.mark(cancelled, "cancelled")
        dispatched = self.store.reserve(OWNER_A, "amap_poi")
        self.store.mark(dispatched, "dispatching")
        self.store.mark(dispatched, "failed")
        self.store.mark(dispatched, "failed")
        with self.assertRaises(UsageError):
            self.store.mark(dispatched, "received")
        with self.assertRaises(UsageError):
            self.store.mark(pending, "received")
        with self.engine.connect() as connection:
            rows = {row[0]: tuple(row[1:]) for row in connection.execute(text("SELECT id,state,dispatched_at FROM usage_attempts"))}
        self.assertEqual(rows[pending], ("reserved", None))
        self.assertEqual(rows[cancelled], ("cancelled", None))
        self.assertEqual(rows[dispatched], ("failed", NOW))
        self.assertEqual(self.store.own_usage(OWNER_A)["services"]["amap_poi"]["used"], 3)

    def test_sqlite_import_preserves_attempt_ids_states_and_allowance(self):
        source_path = Path(self.directory) / "legacy.sqlite3"
        source_settings = self.settings.model_copy(update={"business_usage_path": str(source_path)})
        source = UsageStore(source_settings, clock=lambda: NOW)
        source.initialize()
        identifiers = [source.reserve(OWNER_A, "qwen_vision") for _ in range(3)]
        source.mark(identifiers[0], "dispatching")
        source.mark(identifiers[0], "received")
        source.mark(identifiers[1], "cancelled")
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(source_path)) as connection:
            connection.row_factory = sqlite3.Row
            rows = [dict(row) for row in connection.execute("SELECT * FROM usage_attempts")]
        with self.engine.begin() as connection:
            self.assertEqual(import_usage_rows(connection, rows), 3)
        with self.assertRaises(UsageError):
            with self.engine.begin() as connection:
                import_usage_rows(connection, [])
        self.assertEqual(self.store.own_usage(OWNER_A)["services"]["qwen_vision"]["used"], 3)
        with self.engine.connect() as connection:
            actual = [dict(row._mapping) for row in connection.execute(text("SELECT * FROM usage_attempts ORDER BY id"))]
        self.assertEqual(actual, sorted(rows, key=lambda row: row["id"]))

    def test_import_invalid_row_rolls_back_entire_attempt_batch(self):
        valid = {"id": uuid4().hex, "owner_id": OWNER_A, "service": "media_photo", "created_at": NOW,
                 "state": "reserved", "dispatched_at": None}
        with self.assertRaises(UsageError):
            with self.engine.begin() as connection:
                import_usage_rows(connection, [valid, {**valid, "id": uuid4().hex, "extra": "reject"}])
        self.assertEqual(self.count(), 0)

    def test_boundary_and_rollback_clock_and_no_legacy_fallback(self):
        self.store.reserve(OWNER_A, "amap_weather")
        backwards = MySQLUsageStore(self.settings, clock=lambda: NOW - 1, engine=self.engine)
        with self.assertRaises(UsageError):
            backwards.reserve(OWNER_A, "amap_weather")
        with patch("app.mysql_storage.mysql_configured", return_value=True), patch("app.mysql_usage.MySQLUsageStore", side_effect=UsageError()):
            with self.assertRaises(UsageError):
                get_usage_store()
        self.assertEqual(self.count(), 1)

    def test_unrelated_private_tables_allowed_component_columns_not_relaxed(self):
        with self.engine.begin() as connection:
            connection.execute(text("CREATE TABLE private_fixture (id INTEGER PRIMARY KEY) ENGINE=InnoDB"))
        self.store.verify()
        with self.engine.begin() as connection:
            connection.execute(text("ALTER TABLE usage_attempts ADD COLUMN unexpected INTEGER"))
        with self.assertRaises(UsageError):
            self.store.verify()

    def test_unsafe_commit_durability_blocks_reservation_without_changing_server(self):
        real_execute = Connection.execute
        class Setting:
            def __init__(self, value):
                self.value = value
            def scalar_one(self):
                return self.value
        for value in (0, 2, "1", 1):
            with self.subTest(durability=value):
                def execute(connection, statement, *args, **kwargs):
                    if str(statement) == "SELECT @@GLOBAL.innodb_flush_log_at_trx_commit":
                        return Setting(value)
                    return real_execute(connection, statement, *args, **kwargs)
                with patch.object(Connection, "execute", execute):
                    if type(value) is int and value == 1:
                        self.store.reserve(OWNER_A, "qwen_vision")
                    else:
                        with self.assertRaises(UsageError) as caught:
                            self.store.reserve(OWNER_A, "qwen_vision")
                        self.assertEqual(caught.exception.code, "business_usage_unavailable")
                        self.assertEqual(self.count(), 0)
        self.assertEqual(self.count(), 1)


class MySQLMeteredTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = MySQLUsageTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store = self.fixture.store

    async def test_real_mysql_counts_controlled_retry_failed_and_cancelled_dispatch(self):
        count = 0
        entered = asyncio.Event()

        async def upstream(request):
            nonlocal count
            count += 1
            if count == 1:
                raise httpx.ConnectError("controlled", request=request)
            if count == 3:
                entered.set()
                await asyncio.Future()
            return httpx.Response(200, json={})

        token = bind_usage_context(OWNER_A, "/places/search", self.store)
        try:
            async with httpx.AsyncClient(transport=MeteredTransport(httpx.MockTransport(upstream))) as client:
                with self.assertRaises(httpx.ConnectError):
                    await client.get("https://restapi.amap.com/v5/place/text")
                await client.get("https://restapi.amap.com/v5/place/text")
                task = asyncio.create_task(client.get("https://restapi.amap.com/v5/place/text"))
                await entered.wait()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        finally:
            reset_usage_context(token)
        self.assertEqual(count, 3)
        with self.fixture.engine.connect() as connection:
            states = sorted(connection.execute(text("SELECT state FROM usage_attempts")).scalars())
        self.assertEqual(states, ["cancelled", "failed", "received"])


if __name__ == "__main__":
    unittest.main()
