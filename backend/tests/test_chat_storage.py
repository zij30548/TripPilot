"""Disk-backed chat transactions and isolated upgrades, no paid requests."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import OperationalError

from memory.chat_storage import ChatError, ChatStore, validate_chat_extension
from memory.storage import engine_for
from tests.archive_owner import OWNER, seed_owner
from tools.chat_storage import Refused, provision_chat_extension, sqlite_copy

OTHER = "b" * 32


def operation() -> str:
    return str(uuid4())


def intent() -> dict:
    return {"destination_id": "hangzhou", "start_date": "2026-11-01", "end_date": "2026-11-03", "travelers": 2,
            "budget_cny": "3000", "accommodation_hint": "西湖附近", "required_place_hints": ["西湖"],
            "preferences": [], "transport_mode": "walking"}


class ChatStorage(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name).resolve()
        self.path = self.folder / "private.sqlite3"
        self.engine = engine_for(str(self.path))
        seed_owner(self.engine)
        seed_owner(self.engine, OTHER)
        provision_chat_extension(self.engine)
        self.store = ChatStore(self.engine, clock=lambda: 1_800_000_000)

    def tearDown(self):
        self.engine.dispose()
        engine_for.cache_clear()
        self.temp.cleanup()

    def create(self):
        return self.store.create(OWNER, operation())

    def complete(self, conversation, requirements=None, issues=None):
        op = operation()
        pending = self.store.begin_turn(OWNER, conversation["id"], op, conversation["revision"], "我想去杭州，11月1日至3日，两人，预算3000，住西湖附近")
        return self.store.finish_turn(OWNER, conversation["id"], op, pending["revision"], requirements or intent(), "请核对需求。", [], issues=issues)

    def test_create_retry_and_owner_scope_survive_reopen(self):
        op = operation()
        a = self.store.create(OWNER, op)
        self.assertEqual(a, self.store.create(OWNER, op))
        b = self.store.create(OTHER, op)
        self.assertNotEqual(a["id"], b["id"])
        with self.assertRaises(ChatError) as raised:
            self.store.get(OTHER, a["id"])
        self.assertEqual(raised.exception.status, 404)
        self.engine.dispose(); engine_for.cache_clear()
        self.engine = engine_for(str(self.path))
        reopened = ChatStore(self.engine)
        self.assertEqual(reopened.get(OWNER, a["id"]), a)
        self.assertEqual([v["id"] for v in reopened.list(OTHER)["items"]], [b["id"]])

    def test_only_one_concurrent_turn_and_no_repeat_even_after_reopen(self):
        conversation = self.create()
        ids = [operation(), operation()]
        def attempt(op):
            try:
                return self.store.begin_turn(OWNER, conversation["id"], op, 0, "去杭州")
            except ChatError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, ids))
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        active = self.store.get(OWNER, conversation["id"])
        op = active["last_turn"]["operation_id"]
        self.engine.dispose(); engine_for.cache_clear()
        self.engine = engine_for(str(self.path)); self.store = ChatStore(self.engine)
        with self.assertRaisesRegex(ChatError, "chat_operation_already_used"):
            self.store.begin_turn(OWNER, conversation["id"], op, active["revision"], "去杭州")
        self.assertEqual(self.store.get(OWNER, conversation["id"])["last_turn"]["state"], "running")

    def test_cancel_aba_late_success_error_and_old_cancel_are_rejected(self):
        conversation = self.create()
        first = operation()
        pending = self.store.begin_turn(OWNER, conversation["id"], first, 0, "去杭州")
        cancelled = self.store.cancel_turn(OWNER, conversation["id"], first)
        self.assertEqual(cancelled, self.store.cancel_turn(OWNER, conversation["id"], first))
        newer = operation()
        current = self.store.begin_turn(OWNER, conversation["id"], newer, cancelled["revision"], "还是去杭州")
        for callback in [lambda: self.store.finish_turn(OWNER, conversation["id"], first, pending["revision"], intent(), "旧回复", []),
                         lambda: self.store.fail_turn(OWNER, conversation["id"], first, pending["revision"]),
                         lambda: self.store.cancel_turn(OWNER, conversation["id"], first)]:
            if callback is not None:
                try:
                    callback()
                except ChatError as error:
                    self.assertEqual(error.code, "chat_turn_obsolete")
        self.assertEqual(self.store.get(OWNER, conversation["id"])["last_turn"]["operation_id"], newer)
        self.assertEqual(self.store.get(OWNER, conversation["id"])["revision"], current["revision"])

    def test_confirm_is_explicit_guarded_and_new_turn_invalidates(self):
        complete = self.complete(self.create())
        self.assertIsNone(complete["confirmed_revision"])
        op = operation()
        confirmed = self.store.confirm(OWNER, complete["id"], complete["revision"], op)
        self.assertEqual(confirmed["confirmed_revision"], confirmed["revision"])
        self.assertEqual(confirmed, self.store.confirm(OWNER, complete["id"], complete["revision"], op))
        with self.assertRaisesRegex(ChatError, "chat_revision_conflict"):
            self.store.confirm(OWNER, complete["id"], complete["revision"], operation())
        pending = self.store.begin_turn(OWNER, complete["id"], operation(), confirmed["revision"], "改成北京")
        self.assertIsNone(pending["conversation"]["confirmed_revision"])
        with self.assertRaisesRegex(ChatError, "chat_turn_busy"):
            self.store.confirm(OWNER, complete["id"], pending["revision"], operation())

    def test_failed_cancelled_ambiguous_and_incomplete_cannot_confirm_old_fields(self):
        for state in ("failed", "cancelled", "uncertain", "ambiguous", "incomplete"):
            with self.subTest(state=state):
                complete = self.complete(self.create())
                op = operation()
                pending = self.store.begin_turn(OWNER, complete["id"], op, complete["revision"], "我要改一下")
                if state == "cancelled":
                    result = self.store.cancel_turn(OWNER, complete["id"], op)
                elif state in {"failed", "uncertain"}:
                    result = self.store.fail_turn(OWNER, complete["id"], op, pending["revision"], uncertain=state == "uncertain")
                else:
                    values = intent()
                    if state == "incomplete": values["travelers"] = None
                    result = self.store.finish_turn(OWNER, complete["id"], op, pending["revision"], values, "请补充", [],
                                                    issues=["ambiguous"] if state == "ambiguous" else [])
                with self.assertRaisesRegex(ChatError, "chat_requirements_incomplete"):
                    self.store.confirm(OWNER, result["id"], result["revision"], operation())

    def test_delete_ownership_revision_busy_and_terminal_removes_private_text(self):
        complete = self.complete(self.create())
        with self.assertRaises(ChatError): self.store.delete(OTHER, complete["id"], complete["revision"])
        with self.assertRaises(ChatError): self.store.delete(OWNER, complete["id"], 0)
        pending = self.store.begin_turn(OWNER, complete["id"], operation(), complete["revision"], "继续")
        with self.assertRaisesRegex(ChatError, "chat_turn_busy"):
            self.store.delete(OWNER, complete["id"], pending["revision"])
        cancelled = self.store.cancel_turn(OWNER, complete["id"], pending["conversation"]["last_turn"]["operation_id"])
        self.store.delete(OWNER, complete["id"], cancelled["revision"])
        self.assertEqual(self.store.list(OWNER)["items"], [])
        with self.engine.connect() as db:
            self.assertEqual(db.execute(text("SELECT COUNT(*) FROM chat_turns")).scalar_one(), 2)
            self.assertEqual(db.execute(text("SELECT messages_json FROM chat_conversations WHERE id=:id"), {"id": complete["id"]}).scalar_one(), "[]")
        with self.assertRaises(ChatError): self.store.get(OWNER, complete["id"])
        new = self.create()
        with self.assertRaisesRegex(ChatError, "chat_operation_already_used"):
            self.store.begin_turn(OWNER, new["id"], pending["conversation"]["last_turn"]["operation_id"], new["revision"], "旧请求不能重发")

    def test_limits_no_silent_truncation_and_corrupt_records_fail_closed(self):
        conversation = self.create()
        with self.engine.begin() as db:
            db.execute(text("UPDATE chat_conversations SET requirements_json=:payload WHERE id=:id"),
                       {"payload": '{"travelers":2,"travelers":3}', "id": conversation["id"]})
        with self.assertRaises(ChatError): self.store.get(OWNER, conversation["id"])
        other = self.create()
        for _ in range(20):
            pending = self.store.begin_turn(OWNER, other["id"], operation(), other["revision"], "继续")
            other = self.store.fail_turn(OWNER, other["id"], pending["conversation"]["last_turn"]["operation_id"], pending["revision"])
        with self.assertRaisesRegex(ChatError, "chat_turn_limit"):
            self.store.begin_turn(OWNER, other["id"], operation(), other["revision"], "继续")

    def test_delete_recreate_cannot_bypass_total_receipt_limit(self):
        with patch("memory.chat_storage.MAX_TOTAL_CONVERSATIONS", 2):
            first = self.create()
            self.store.delete(OWNER, first["id"], first["revision"])
            second = self.create()
            self.store.delete(OWNER, second["id"], second["revision"])
            self.assertEqual(self.store.list(OWNER)["items"], [])
            with self.assertRaisesRegex(ChatError, "chat_storage_limit"):
                self.create()
            # A separate account still has its independent bounded allowance.
            self.assertIsNotNone(self.store.create(OTHER, operation())["id"])

    def test_commit_failure_is_safe_and_uncertain_commit_never_redispatches(self):
        with patch.object(Connection, "commit", side_effect=OperationalError("private SQL", {}, Exception("secret"))):
            with self.assertRaisesRegex(ChatError, "chat_storage_unavailable"):
                self.create()
        self.assertEqual(self.store.list(OWNER)["items"], [])
        conversation = self.create()
        op = operation()
        original_commit = Connection.commit
        def lost_ack(connection):
            original_commit(connection)
            raise OperationalError("private SQL", {}, Exception("secret"))
        with patch.object(Connection, "commit", new=lost_ack):
            with self.assertRaisesRegex(ChatError, "chat_storage_unavailable"):
                self.store.begin_turn(OWNER, conversation["id"], op, 0, "去杭州")
        occupied = self.store.get(OWNER, conversation["id"])
        self.assertEqual(occupied["last_turn"]["state"], "running")
        with self.assertRaisesRegex(ChatError, "chat_operation_already_used"):
            self.store.begin_turn(OWNER, conversation["id"], op, occupied["revision"], "去杭州")
        self.store.cancel_turn(OWNER, conversation["id"], op)


class ChatMigration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name).resolve()
        self.source = self.folder / "old.sqlite3"
        self.engine = engine_for(str(self.source))
        seed_owner(self.engine)
        self.engine.dispose(); engine_for.cache_clear()

    def tearDown(self):
        engine_for.cache_clear()
        self.temp.cleanup()

    def test_old_schema_remains_usable_chat_unavailable_and_no_runtime_ddl(self):
        engine = engine_for(str(self.source))
        with self.assertRaisesRegex(ChatError, "chat_migration_required"):
            ChatStore(engine).create(OWNER, operation())
        engine.dispose(); engine_for.cache_clear()
        with closing(sqlite3.connect(self.source)) as db:
            self.assertNotIn("chat_conversations", {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")})

    def test_copy_upgrade_and_empty_rollback_rehearsal_preserve_original(self):
        before = hashlib.sha256(self.source.read_bytes()).hexdigest()
        target, receipt = self.folder / "chat.sqlite3", self.folder / "copy.json"
        result = sqlite_copy(self.source, target, receipt, confirm_source_stopped=True)
        self.assertTrue(result["source_unchanged"])
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), before)
        self.assertEqual(receipt.stat().st_mode & 0o777, 0o600)
        engine = engine_for(str(target))
        with engine.connect() as db: self.assertTrue(validate_chat_extension(db))
        engine.dispose(); engine_for.cache_clear()
        rollback = self.folder / "rollback.sqlite3"
        sqlite_copy(target, rollback, self.folder / "rollback.json", confirm_source_stopped=True, rollback=True)
        engine = engine_for(str(rollback))
        with engine.connect() as db: self.assertFalse(validate_chat_extension(db, required=False))
        engine.dispose(); engine_for.cache_clear()
        with self.assertRaises(Refused): sqlite_copy(self.source, target, self.folder / "other.json", confirm_source_stopped=True)

    def test_rollback_refuses_existing_chat_and_bad_schema_not_relaxed(self):
        target = self.folder / "chat.sqlite3"
        sqlite_copy(self.source, target, self.folder / "copy.json", confirm_source_stopped=True)
        engine = engine_for(str(target)); ChatStore(engine).create(OWNER, operation())
        engine.dispose(); engine_for.cache_clear()
        with self.assertRaises(Refused):
            sqlite_copy(target, self.folder / "rollback.sqlite3", self.folder / "rollback.json", confirm_source_stopped=True, rollback=True)
        engine = engine_for(str(target))
        with engine.begin() as db: db.execute(text("ALTER TABLE chat_conversations ADD COLUMN unexpected TEXT"))
        engine.dispose(); engine_for.cache_clear()
        with self.assertRaisesRegex(ChatError, "invalid_chat_schema"):
            engine_for(str(target))

    def test_copy_refuses_unknown_tables_or_nonprivate_destination(self):
        with closing(sqlite3.connect(self.source)) as db:
            db.execute("CREATE TABLE unrelated (value TEXT)")
            db.commit()
        with self.assertRaisesRegex(Refused, "unexpected_source_tables"):
            sqlite_copy(self.source, self.folder / "copy.sqlite3", self.folder / "copy.json", confirm_source_stopped=True)
        self.assertFalse((self.folder / "copy.sqlite3").exists())
        public = self.folder / "not-private"
        public.mkdir(mode=0o755)
        with self.assertRaisesRegex(Refused, "private_destination_directory_required"):
            sqlite_copy(self.source, public / "copy.sqlite3", self.folder / "private.json", confirm_source_stopped=True)


if __name__ == "__main__":
    unittest.main()
