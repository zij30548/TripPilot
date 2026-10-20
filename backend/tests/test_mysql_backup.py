"""Real MySQL logical backup/recovery rehearsal; synthetic private data only."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import inspect, select, text
from sqlalchemy.exc import OperationalError
from sqlmodel import Session

from app.auth import PASSWORD_HASHER
from app.identity_models import AccountRow, InvitationRow, SessionRow
from app.mysql_storage import initialize_private_schema, validate_private_schema
from app.mysql_usage import initialize_usage_schema, usage_attempts
from app.saved_trips import SavedTripRow, get_record, save
from app.schemas.saved_trip import SaveTrip
from app.storage import StorageError
from tests.mysql_support import temporary_mysql
from tests.test_saved_trips import payload
from tools.mysql_backup import Refused, backup, inspect_backup, restore_drill

IDENTITY = "a" * 32
OWNER = "b" * 32


class MySQLBackup(unittest.TestCase):
    def setUp(self):
        self.engine, cleanup = temporary_mysql()
        self.addCleanup(cleanup)
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(temporary).resolve()
        self.root.chmod(0o700)
        self.anchor = self.root / "usage.identity"
        self.anchor.write_text(IDENTITY + "\n")
        self.anchor.chmod(0o600)
        self.path = self.root / "private-backup.jsonl"
        initialize_private_schema(self.engine)
        initialize_usage_schema(self.engine, IDENTITY)
        with Session(self.engine) as db:
            db.add(AccountRow(id=OWNER, username="backup-user", password_hash=PASSWORD_HASHER.hash("controlled-backup-passphrase"), created_at=100))
            db.commit()
            db.add_all([SessionRow(id="c" * 32, token_hash="d" * 64, user_id=OWNER, csrf_token="e" * 43, created_at=100, expires_at=200),
                        InvitationRow(id="f" * 32, token_hash="1" * 64, created_at=90, expires_at=300,
                                      consumed_by=OWNER, consumed_at=100)])
            db.commit()
            self.saved = save(db, SaveTrip.model_validate_json(json.dumps(payload()), strict=True), OWNER)
        with self.engine.begin() as db:
            db.execute(usage_attempts.insert(), [
                {"id": str(index) * 32, "owner_id": OWNER, "service": "amap_route", "created_at": 110,
                 "state": state, "dispatched_at": None if state in {"reserved", "cancelled"} else 111}
                for index, state in enumerate(("reserved", "dispatching", "received", "failed", "cancelled"), 2)])

    def target(self):
        engine, cleanup = temporary_mysql(prefix="trippilot_restore_")
        self.addCleanup(cleanup)
        return engine

    def rows(self, engine, table):
        with engine.connect() as db:
            return [dict(row) for row in db.execute(text(f"SELECT * FROM {table} ORDER BY id")).mappings()]

    def test_consistent_private_backup_and_isolated_restore_preserve_quota_and_revoke_sessions(self):
        before = {name: self.rows(self.engine, name) for name in ("accounts", "auth_sessions", "saved_trips", "usage_attempts", "invitations")}
        result = backup(self.engine, self.anchor, self.path)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(result["sha256"], hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertNotIn("mysql+pymysql", self.path.read_text())
        checked = inspect_backup(self.path, result["sha256"])
        self.assertEqual(checked["rows"]["usage_attempts"], 5)
        target = self.target()
        target_anchor = self.root / "restored.identity"
        restored = restore_drill(target, self.path, target_anchor, expected_sha256=result["sha256"])
        self.assertEqual(restored["sessions_revoked"], 1)
        self.assertEqual(restored["usage_attempts_preserved"], 5)
        self.assertFalse(restored["quota_restore_clearance"])
        self.assertEqual(target_anchor.read_bytes(), self.anchor.read_bytes())
        for name, expected in before.items():
            self.assertEqual(self.rows(self.engine, name), expected)
            self.assertEqual(self.rows(target, name), [] if name == "auth_sessions" else expected)
        with Session(target) as db:
            self.assertEqual(get_record(db, self.saved.id, OWNER), self.saved)
        validate_private_schema(target)

    def test_existing_output_and_bad_anchor_never_overwrite_or_reset(self):
        self.path.write_bytes(b"do not overwrite")
        self.path.chmod(0o600)
        with self.assertRaises(FileExistsError):
            backup(self.engine, self.anchor, self.path)
        self.assertEqual(self.path.read_bytes(), b"do not overwrite")
        self.anchor.write_text("0" * 32 + "\n")
        target = self.root / "rejected.jsonl"
        with self.assertRaises(Exception):
            backup(self.engine, self.anchor, target)
        # A private started file may remain, but cannot be mistaken for complete.
        if target.exists():
            with self.assertRaises(Refused):
                inspect_backup(target, hashlib.sha256(target.read_bytes()).hexdigest())

    def test_repeatable_read_backup_does_not_mix_a_later_usage_commit(self):
        from tools import mysql_backup
        original = mysql_backup._encoded
        changed = False
        def later_write(value):
            nonlocal changed
            if isinstance(value, dict) and value.get("kind") == "row" and value.get("table") == "accounts" and not changed:
                changed = True
                with self.engine.begin() as db:
                    db.execute(text("UPDATE accounts SET disabled=true WHERE id=:id"), {"id": OWNER})
                    db.execute(usage_attempts.insert(), {"id": "9" * 32, "owner_id": OWNER, "service": "amap_route",
                                                       "created_at": 120, "state": "dispatching", "dispatched_at": 121})
            return original(value)
        with patch("tools.mysql_backup._encoded", later_write):
            result = backup(self.engine, self.anchor, self.path)
        self.assertTrue(changed)
        self.assertEqual(result["rows"]["usage_attempts"], 5)
        self.assertEqual(len(self.rows(self.engine, "usage_attempts")), 6)
        parsed = [json.loads(line) for line in self.path.read_text().splitlines()]
        account = next(row["value"] for row in parsed if row.get("table") == "accounts")
        self.assertFalse(account["disabled"])
        inspect_backup(self.path, result["sha256"])

    def test_invalid_bytes_tampering_unknown_fields_and_wrong_hash_fail_before_ddl(self):
        result = backup(self.engine, self.anchor, self.path)
        target = self.target()
        for index, raw in enumerate((b"\xff\n", self.path.read_bytes().replace(b"backup-user", b"tamper-user"),
                                     self.path.read_bytes().replace(b'"kind":"header"', b'"kind":"header","unexpected":true'))):
            path = self.root / f"invalid-{index}.jsonl"
            path.write_bytes(raw)
            path.chmod(0o600)
            with self.assertRaises((Refused, ValueError, UnicodeError)):
                restore_drill(target, path, self.root / f"invalid-{index}.identity",
                              expected_sha256=hashlib.sha256(raw).hexdigest())
            self.assertEqual(inspect(target).get_table_names(), [])
        with self.assertRaises(Refused):
            restore_drill(target, self.path, self.root / "bad-hash.identity", expected_sha256="0" * 64)
        self.assertEqual(inspect(target).get_table_names(), [])

    def test_production_name_and_existing_restore_target_are_rejected(self):
        result = backup(self.engine, self.anchor, self.path)
        with self.assertRaisesRegex(Refused, "isolated_restore_database_required"):
            restore_drill(self.engine, self.path, self.root / "no.identity", expected_sha256=result["sha256"])
        target = self.target()
        with target.begin() as db:
            db.execute(text("CREATE TABLE existing (id INT PRIMARY KEY)"))
        with self.assertRaisesRegex(Refused, "restore_target_not_empty"):
            restore_drill(target, self.path, self.root / "no.identity", expected_sha256=result["sha256"])
        self.assertFalse((self.root / "no.identity").exists())

    def test_concurrent_restore_lock_rejects_before_anchor_or_schema_creation(self):
        result = backup(self.engine, self.anchor, self.path)
        target = self.target()
        key = "trippilot_restore_" + hashlib.sha256(target.url.database.encode()).hexdigest()[:32]
        with target.connect() as lock:
            lock.execute(text("SELECT GET_LOCK(:key, 0)"), {"key": key})
            try:
                with self.assertRaisesRegex(Refused, "restore_already_running"):
                    restore_drill(target, self.path, self.root / "locked.identity", expected_sha256=result["sha256"])
            finally:
                lock.execute(text("SELECT RELEASE_LOCK(:key)"), {"key": key})
        self.assertEqual(inspect(target).get_table_names(), [])
        self.assertFalse((self.root / "locked.identity").exists())

    def test_private_permissions_symlinks_and_hardlinks_rejected(self):
        result = backup(self.engine, self.anchor, self.path)
        self.path.chmod(0o644)
        with self.assertRaises(Refused):
            inspect_backup(self.path, result["sha256"])
        self.path.chmod(0o600)
        linked = self.root / "linked.jsonl"
        linked.symlink_to(self.path)
        with self.assertRaises(Refused):
            inspect_backup(linked, result["sha256"])
        import os
        hard = self.root / "hard.jsonl"
        os.link(self.path, hard)
        with self.assertRaises(Refused):
            inspect_backup(self.path, result["sha256"])

    def test_restore_commit_failure_leaves_unready_target_and_no_returned_success(self):
        result = backup(self.engine, self.anchor, self.path)
        target = self.target()
        from sqlalchemy.engine import Connection
        original_execute = Connection.execute
        def fail_marker(connection, statement, *args, **kwargs):
            if str(statement).startswith("INSERT INTO storage_metadata"):
                raise OperationalError("controlled", {}, Exception("not logged"))
            return original_execute(connection, statement, *args, **kwargs)
        with patch.object(Connection, "execute", fail_marker):
            with self.assertRaises(OperationalError):
                restore_drill(target, self.path, self.root / "failed.identity", expected_sha256=result["sha256"])
        with self.assertRaises(StorageError):
            validate_private_schema(target)
        with target.connect() as db:
            self.assertEqual(db.execute(text("SELECT count(*) FROM accounts")).scalar_one(), 0)
            self.assertEqual(db.execute(text("SELECT count(*) FROM usage_attempts")).scalar_one(), 0)
        self.assertTrue((self.root / "failed.identity").exists())


if __name__ == "__main__":
    unittest.main()
