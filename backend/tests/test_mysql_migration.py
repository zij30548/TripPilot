"""Copy-only full migration against real isolated MySQL and stopped SQLite.

No original/private user database, real account or provider endpoint is used.
"""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import event, inspect, text
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

from core.auth import PASSWORD_HASHER
from core.business_usage import SERVICES, UsageSettings, UsageStore
from memory.identity_models import AccountRow, LoginBucketRow, SessionRow
from memory.mysql_storage import MYSQL_STORAGE_VERSION, validate_private_schema
from memory.mysql_usage import MySQLUsageStore
from memory.saved_trips import ArchiveError, SavedTripRow, get_record, listing, save
from core.schemas.saved_trip import SaveTrip
from memory.storage import StorageError, engine_for
from tools import mysql_migration
from tools.mysql_migration import FILES, Refused, inspect_pair, sha256
from tests.mysql_support import temporary_mysql
from tests.test_destinations import modern_snapshot
from tests.test_duration import archive_for, city_request, controlled_schedule
from tests.test_saved_trips import snapshot

NOW = 1792296030
A, B = "a" * 32, "b" * 32


class FullMySQLMigration(unittest.TestCase):
    def setUp(self):
        self.engine, close = temporary_mysql()
        self.addCleanup(close)
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(temporary).resolve()
        self.root.chmod(0o700)
        self.source = self.root / "stopped-backup"
        self.source.mkdir(mode=0o700)
        self.private_path = self.source / FILES[0]
        self.source_engine = engine_for(str(self.private_path))
        self.addCleanup(self.source_engine.dispose)
        self.addCleanup(engine_for.cache_clear)
        password_hash = PASSWORD_HASHER.hash("controlled-migration-passphrase")
        with Session(self.source_engine) as db:
            db.add_all([AccountRow(id=A, username="migrate-a", password_hash=password_hash,
                                  disabled=False, created_at=NOW - 100),
                        AccountRow(id=B, username="migrate-b", password_hash=password_hash,
                                  disabled=True, created_at=NOW - 90)])
            db.commit()
            db.add_all([SessionRow(id="1" * 32, token_hash="c" * 64, user_id=A,
                                  csrf_token="d" * 43, created_at=NOW, expires_at=NOW + 3600),
                        LoginBucketRow(id="e" * 64, window_start=NOW - 30, attempts=3)])
            db.commit()
            self.requests = []
            self.ids = []
            operation = uuid4().hex
            cases = [(A, snapshot()), (B, modern_snapshot()),
                     (A, archive_for(controlled_schedule(city_request(days=7))))]
            for index, (owner, content) in enumerate(cases):
                request = SaveTrip.model_validate_json(json.dumps({"operation_id": operation if index < 2 else uuid4().hex,
                    "name": "合成迁移兼容验收", "snapshot": content}), strict=True)
                result = save(db, request, owner)
                self.requests.append((owner, request))
                self.ids.append(result.id)
            original = db.get(SavedTripRow, self.ids[0])
            quarantined = {column.name: getattr(original, column.name) for column in SavedTripRow.__table__.c}
            quarantined.update(id=uuid4().hex, owner_id=None, operation_id=uuid4().hex)
            db.add(SavedTripRow(**quarantined))
            db.commit()
        self.source_engine.dispose()
        self.usage_settings = UsageSettings(_env_file=None,
            business_usage_path=str(self.source / FILES[1]),
            business_rate_account_limit=1000, business_rate_global_limit=2000,
            business_daily_limits={service: {"account": 100, "global": 1000} for service in SERVICES})
        usage = UsageStore(self.usage_settings, clock=lambda: NOW)
        usage.initialize()
        # All five states include a sent-but-unknown attempt and unsent cancel.
        for index, state in enumerate(("reserved", "dispatching", "received", "failed", "cancelled")):
            attempt = usage.reserve(A if index % 2 else B, "amap_route")
            if state in {"dispatching", "received", "failed"}:
                usage.mark(attempt, "dispatching")
            if state in {"received", "failed", "cancelled"}:
                usage.mark(attempt, state)
        self.before = inspect_pair(self.source)
        self.hashes = {name: sha256(self.source / name) for name in FILES}
        self.receipt = self.root / "migration-receipt.json"

    def migrate(self):
        return mysql_migration.migrate(self.source, self.engine, self.receipt, source_stopped=True)

    def table_rows(self, source, table):
        if source:
            with closing(sqlite3.connect(self.private_path)) as db:
                db.row_factory = sqlite3.Row
                return [dict(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY id")]
        with self.engine.connect() as db:
            return [dict(row) for row in db.execute(text(f"SELECT * FROM {table} ORDER BY id")).mappings()]

    def assert_not_ready(self):
        with self.assertRaises(StorageError):
            validate_private_schema(self.engine)
        with self.engine.connect() as db:
            self.assertEqual(db.execute(text("SELECT count(*) FROM storage_metadata")).scalar_one(), 0)
            for name in ("accounts", "saved_trips", "usage_attempts"):
                self.assertEqual(db.execute(text(f"SELECT count(*) FROM {name}")).scalar_one(), 0)

    def test_full_pair_preserves_ids_json_times_ownership_sessions_and_all_usage_states(self):
        result = self.migrate()
        self.assertEqual(result["state"], "verified")
        self.assertTrue(result["ledger_identity_preserved"])
        self.assertEqual(inspect_pair(self.source), self.before)
        self.assertEqual({name: sha256(self.source / name) for name in FILES}, self.hashes)
        self.assertEqual(self.receipt.stat().st_mode & 0o777, 0o600)
        validate_private_schema(self.engine)
        for table in mysql_migration.COPY_ORDER:
            self.assertEqual(self.table_rows(True, table), self.table_rows(False, table))
        self.assertEqual(result["tables"]["usage_attempts"]["rows"], 5)
        with self.engine.connect() as db:
            states = sorted(db.execute(text("SELECT state FROM usage_attempts")).scalars())
        self.assertEqual(states, ["cancelled", "dispatching", "failed", "received", "reserved"])
        self.engine.dispose()
        validate_private_schema(self.engine)
        migrated = MySQLUsageStore(self.usage_settings, clock=lambda: NOW, engine=self.engine)
        migrated.verify()
        self.assertEqual(migrated.own_usage(A)["services"]["amap_route"]["used"], 2)
        self.assertEqual(migrated.own_usage(B)["services"]["amap_route"]["used"], 3)
        with Session(self.engine) as db:
            self.assertEqual([item.schema_version for item in listing(db, 0, 20, A).items], ["saved-trip-v3", "saved-trip-v1"])
            self.assertEqual([item.schema_version for item in listing(db, 0, 20, B).items], ["saved-trip-v2"])
            # Same operation_id for different owners is preserved, never merged.
            for (owner, request), identifier in zip(self.requests[:2], self.ids[:2]):
                self.assertEqual(save(db, request, owner).id, identifier)
            with self.assertRaises(ArchiveError) as caught:
                get_record(db, self.ids[0], B)
            self.assertEqual(caught.exception.status, 404)
            self.assertEqual(db.exec(select(SavedTripRow).where(SavedTripRow.owner_id.is_(None))).one().owner_id, None)

    def test_target_digest_uses_unbuffered_cursor_and_closes_it_before_next_statement(self):
        from pymysql.cursors import SSCursor

        self.migrate()
        cursors = []

        def observe(connection, cursor, statement, parameters, context, executemany):
            if statement == "SELECT * FROM accounts ORDER BY id":
                cursors.append(cursor)

        event.listen(self.engine, "after_cursor_execute", observe)
        try:
            with self.engine.connect() as db:
                rows = list(mysql_migration._mysql_rows(db, "accounts"))
                self.assertEqual(len(rows), 2)
                self.assertEqual(len(cursors), 1)
                self.assertIsInstance(cursors[0], SSCursor)
                self.assertIsNone(cursors[0].connection)
                self.assertEqual(db.execute(text("SELECT 1")).scalar_one(), 1)
                # A consumer that rejects a row also reliably closes its cursor.
                iterator = mysql_migration._mysql_rows(db, "accounts")
                next(iterator)
                iterator.close()
                self.assertIsNone(cursors[-1].connection)
                self.assertEqual(db.execute(text("SELECT 1")).scalar_one(), 1)
        finally:
            event.remove(self.engine, "after_cursor_execute", observe)

    def test_existing_target_or_receipt_refused_without_overwrite(self):
        self.migrate()
        receipt_hash = sha256(self.receipt)
        with self.assertRaises(Refused) as caught:
            self.migrate()
        self.assertEqual(str(caught.exception), "mysql_target_not_empty")
        self.assertEqual(sha256(self.receipt), receipt_hash)
        other, close = temporary_mysql()
        self.addCleanup(close)
        with self.assertRaises(FileExistsError):
            mysql_migration.migrate(self.source, other, self.receipt, source_stopped=True)
        self.assertEqual(inspect(other).get_table_names(), [])
        self.assertEqual(sha256(self.receipt), receipt_hash)

    def test_explicit_stop_missing_anchor_and_corrupt_record_refused(self):
        with self.assertRaises(Refused) as caught:
            mysql_migration.migrate(self.source, self.engine, self.receipt, source_stopped=False)
        self.assertEqual(str(caught.exception), "explicit_stopped_backup_confirmation_required")
        self.assertEqual(inspect(self.engine).get_table_names(), [])
        anchor = self.source / FILES[2]
        original = anchor.read_bytes()
        anchor.write_bytes(b"0" * 32 + b"\n")
        with self.assertRaises(Refused):
            self.migrate()
        anchor.write_bytes(original)
        with closing(sqlite3.connect(self.private_path)) as db, db:
            db.execute("UPDATE saved_trips SET snapshot_json='{}' WHERE id=?", (self.ids[0],))
        with self.assertRaises(ArchiveError):
            self.migrate()
        self.assert_not_ready()
        self.assertEqual(json.loads(self.receipt.read_text())["state"], "failed_or_uncertain_inspect_target_do_not_retry")

    def test_source_changed_during_copy_rolls_back_all_dml_and_refuses_ready(self):
        original = mysql_migration._source_hashes
        calls = 0
        def changed(directory):
            nonlocal calls
            calls += 1
            result = original(directory)
            return result if calls == 1 else {**result, FILES[0]: "0" * 64}
        with patch("tools.mysql_migration._source_hashes", side_effect=changed), self.assertRaises(Refused) as caught:
            self.migrate()
        self.assertEqual(str(caught.exception), "migration_source_changed")
        self.assert_not_ready()
        self.assertEqual({name: sha256(self.source / name) for name in FILES}, self.hashes)

    def test_final_commit_failure_retains_schema_receipt_and_does_not_publish_ready(self):
        original_import = mysql_migration.import_usage_rows
        original_commit = self.engine.dialect.do_commit
        final_ready = False
        def import_rows(connection, rows):
            nonlocal final_ready
            result = original_import(connection, rows)
            final_ready = True
            return result
        def commit(connection):
            if final_ready:
                raise OperationalError("controlled commit failure", {}, Exception("private database detail"))
            return original_commit(connection)
        with patch("tools.mysql_migration.import_usage_rows", side_effect=import_rows), \
                patch.object(self.engine.dialect, "do_commit", commit), self.assertRaises(OperationalError):
            self.migrate()
        self.assert_not_ready()
        self.assertEqual(json.loads(self.receipt.read_text())["state"], "failed_or_uncertain_inspect_target_do_not_retry")
        self.assertEqual({name: sha256(self.source / name) for name in FILES}, self.hashes)
        with self.assertRaises(Refused):
            self.migrate()

    def test_commit_ack_lost_preserves_complete_target_and_uncertain_receipt_no_retry(self):
        original_import = mysql_migration.import_usage_rows
        original_commit = self.engine.dialect.do_commit
        final_ready = False
        def import_rows(connection, rows):
            nonlocal final_ready
            result = original_import(connection, rows)
            final_ready = True
            return result
        def commit(connection):
            original_commit(connection)
            if final_ready:
                raise OperationalError("controlled ACK lost", {}, Exception("private detail"))
        with patch("tools.mysql_migration.import_usage_rows", side_effect=import_rows), \
                patch.object(self.engine.dialect, "do_commit", commit), self.assertRaises(OperationalError):
            self.migrate()
        # COMMIT really happened: never delete this target or promise rollback.
        validate_private_schema(self.engine)
        self.assertEqual(self.table_rows(True, "saved_trips"), self.table_rows(False, "saved_trips"))
        with self.engine.connect() as db:
            self.assertEqual(db.execute(text("SELECT count(*) FROM usage_attempts")).scalar_one(), 5)
        self.assertEqual(json.loads(self.receipt.read_text())["state"], "failed_or_uncertain_inspect_target_do_not_retry")
        with self.assertRaises(Refused):
            self.migrate()
        self.assertEqual({name: sha256(self.source / name) for name in FILES}, self.hashes)

    def test_invalid_account_hash_cannot_publish_a_ready_database(self):
        with closing(sqlite3.connect(self.private_path)) as db, db:
            db.execute("UPDATE accounts SET password_hash='not-an-argon2id-hash' WHERE id=?", (A,))
        with self.assertRaises(Refused):
            self.migrate()
        self.assert_not_ready()

    def test_fake_ready_target_is_not_trusted_as_an_empty_migration_destination(self):
        with self.engine.begin() as db:
            db.execute(text("CREATE TABLE storage_metadata (`key` VARCHAR(64) PRIMARY KEY, value VARCHAR(256)) ENGINE=InnoDB"))
            db.execute(text("INSERT INTO storage_metadata VALUES ('version', :v)"), {"v": MYSQL_STORAGE_VERSION})
        with self.assertRaises(Refused) as caught:
            self.migrate()
        self.assertEqual(str(caught.exception), "mysql_target_not_empty")
        self.assertFalse(self.receipt.exists())
        with self.assertRaises(StorageError):
            validate_private_schema(self.engine)


if __name__ == "__main__":
    unittest.main()
