"""Opt-in real MySQL chat extension tests, isolated databases only."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from sqlalchemy import text

from memory.chat_storage import ChatError, ChatStore, validate_chat_extension
from memory.mysql_storage import initialize_private_schema, validate_private_schema
from memory.mysql_usage import initialize_usage_schema, validate_usage_schema
from tests.archive_owner import OWNER, seed_owner
from tests.mysql_support import temporary_mysql
from tests.test_chat_storage import intent
from tools.chat_storage import Refused, mysql_copy, provision_chat_extension
from tools.mysql_backup import backup
from tools.mysql_migration import Refused as BackupRefused


class MySQLChat(unittest.TestCase):
    def setUp(self):
        self.engine, close = temporary_mysql()
        self.addCleanup(close)
        initialize_private_schema(self.engine)
        initialize_usage_schema(self.engine, "1" * 32)
        seed_owner(self.engine)
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.anchor = self.folder / "usage.identity"
        self.anchor.write_text("1" * 32 + "\n")
        self.anchor.chmod(0o600)

    def test_exact_chat_schema_owner_lock_and_late_cancel_commit(self):
        provision_chat_extension(self.engine)
        validate_private_schema(self.engine)
        store = ChatStore(self.engine)
        conversation = store.create(OWNER, str(uuid4()))
        def attempt(_):
            try:
                return store.begin_turn(OWNER, conversation["id"], str(uuid4()), 0, "杭州")
            except ChatError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(attempt, range(3)))
        self.assertEqual(sum(isinstance(row, dict) for row in results), 1)
        pending = next(row for row in results if isinstance(row, dict))
        op = pending["conversation"]["last_turn"]["operation_id"]
        store.cancel_turn(OWNER, conversation["id"], op)
        with self.assertRaisesRegex(ChatError, "chat_turn_obsolete"):
            store.finish_turn(OWNER, conversation["id"], op, pending["revision"], intent(), "旧结果", [])
        with self.engine.begin() as db:
            db.execute(text("ALTER TABLE chat_turns ADD COLUMN arbitrary VARCHAR(3)"))
        with self.assertRaisesRegex(ChatError, "invalid_chat_schema"):
            validate_private_schema(self.engine)

    def test_copy_and_empty_rollback_keep_accounts_attempts_and_base_readiness(self):
        target, close_target = temporary_mysql(); self.addCleanup(close_target)
        with self.engine.begin() as db:
            db.execute(text("INSERT INTO usage_attempts (id,owner_id,service,created_at,state,dispatched_at) VALUES (:id,:owner,'qwen_text',100,'dispatching',100)"),
                       {"id": "2" * 32, "owner": OWNER})
        result = mysql_copy(self.engine, target, self.folder / "copy.json", anchor=self.anchor, confirm_source_stopped=True)
        self.assertEqual(result["state"], "verified")
        self.assertFalse(result["quota_refunded"])
        validate_private_schema(target)
        with target.connect() as db:
            self.assertTrue(validate_chat_extension(db))
            validate_usage_schema(db, "1" * 32)
            self.assertEqual(db.execute(text("SELECT state FROM usage_attempts")).scalar_one(), "dispatching")
        rollback, close_rollback = temporary_mysql(); self.addCleanup(close_rollback)
        mysql_copy(target, rollback, self.folder / "rollback.json", anchor=self.anchor, confirm_source_stopped=True, rollback=True)
        validate_private_schema(rollback)
        with rollback.connect() as db:
            self.assertFalse(validate_chat_extension(db, required=False))
            self.assertEqual(db.execute(text("SELECT state FROM usage_attempts")).scalar_one(), "dispatching")
        with self.engine.connect() as db:
            self.assertFalse(validate_chat_extension(db, required=False))
        ChatStore(target).create(OWNER, str(uuid4()))
        empty, close_empty = temporary_mysql(); self.addCleanup(close_empty)
        with self.assertRaises(Refused):
            mysql_copy(target, empty, self.folder / "not-rollback.json", anchor=self.anchor, confirm_source_stopped=True, rollback=True)

    def test_copy_refuses_wrong_anchor_before_target_ddl(self):
        target, cleanup = temporary_mysql(); self.addCleanup(cleanup)
        self.anchor.write_text("f" * 32 + "\n")
        with self.assertRaisesRegex(Refused, "usage_identity_anchor_mismatch"):
            mysql_copy(self.engine, target, self.folder / "copy.json", anchor=self.anchor, confirm_source_stopped=True)
        from sqlalchemy import inspect
        self.assertEqual(inspect(target).get_table_names(), [])

    def test_old_backup_explicitly_rejects_chat_not_silent_omission(self):
        provision_chat_extension(self.engine)
        with self.assertRaises(BackupRefused) as raised:
            backup(self.engine, self.folder / "unused-anchor", self.folder / "must-not-exist.jsonl")
        self.assertEqual(str(raised.exception), "backup_chat_extension_requires_chat_copy_tool")
        self.assertFalse((self.folder / "must-not-exist.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
