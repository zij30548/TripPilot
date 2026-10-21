"""Disk persistence/ownership/version tests use isolated temporary fixtures."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import inspect, text, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import OperationalError

from memory.chat_storage import ChatStore, validate_chat_extension
from memory.storage import engine_for
from memory.assistant_models import AssistantTaskRow
from memory.assistant_store import AssistantError, AssistantStore, validate_assistant_extension, validate_checkpoint
from tests.archive_owner import OWNER, seed_owner
from tests.test_chat_storage import intent
from tools.assistant_storage import Refused, provision_assistant_extension
from tools.chat_storage import provision_chat_extension

OTHER = "b" * 32


def op() -> str:
    return str(uuid4())


class AssistantStorage(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.path = self.folder / "independent.sqlite3"
        self.engine = engine_for(str(self.path))
        self.addCleanup(self.engine.dispose)
        self.addCleanup(engine_for.cache_clear)
        seed_owner(self.engine)
        seed_owner(self.engine, OTHER)
        provision_chat_extension(self.engine)
        self.chat = ChatStore(self.engine)
        self.conversation = self.chat.create(OWNER, op())
        provision_assistant_extension(self.engine)
        self.store = AssistantStore(self.engine)

    def begin(self):
        return self.store.begin(OWNER, self.conversation["id"], op(), self.conversation["revision"])

    def test_no_runtime_ddl_missing_extension_fails_explicitly(self):
        with self.engine.begin() as db:
            db.execute(text("DROP TABLE assistant_preferences"))
            db.execute(text("DROP TABLE assistant_tasks"))
            db.execute(text("DELETE FROM storage_metadata WHERE `key`='assistant_version'"))
        with self.assertRaisesRegex(AssistantError, "assistant_migration_required"):
            self.begin()
        with self.engine.connect() as db:
            self.assertNotIn("assistant_tasks", inspect(db).get_table_names())
            self.assertTrue(validate_chat_extension(db))
        with patch("memory.mysql_storage.current_mysql_engine", side_effect=AssistantError("mysql_unavailable")):
            with self.assertRaisesRegex(AssistantError, "mysql_unavailable"):
                AssistantStore()

    def test_running_checkpoint_survives_reopen_and_never_authorizes_resend(self):
        task = self.begin()
        data = {"workspace": {"city_id": "310100"}, "trace": [{"step": 1, "tool": "search_places", "state": "dispatching"}]}
        saved = self.store.checkpoint(OWNER, task["id"], 0, data, "running")
        self.engine.dispose()
        engine_for.cache_clear()
        self.engine = engine_for(str(self.path))
        reopened = AssistantStore(self.engine)
        self.assertEqual(reopened.get(OWNER, task["id"]), saved)
        with self.assertRaisesRegex(AssistantError, "assistant_operation_already_used"):
            reopened.begin(OWNER, self.conversation["id"], task["id"], 0)
        unknown = reopened.checkpoint(OWNER, task["id"], 1, {"trace": [{"tool": "search_places", "state": "uncertain"}]}, "uncertain")
        self.assertEqual(unknown["state"], "uncertain")
        for action in (lambda: reopened.checkpoint(OWNER, task["id"], 2, {}, "running"),
                       lambda: reopened.begin(OWNER, self.conversation["id"], task["id"], 0)):
            with self.assertRaises(AssistantError):
                action()

    def test_cross_owner_all_task_paths_and_preferences_are_isolated(self):
        task = self.begin()
        for action in (lambda: self.store.get(OTHER, task["id"]),
                       lambda: self.store.latest(OTHER, self.conversation["id"]),
                       lambda: self.store.checkpoint(OTHER, task["id"], 0, {}, "cancelled")):
            with self.assertRaises(Exception) as error:
                action()
            self.assertEqual(error.exception.status, 404)
        self.store.set_preference(OWNER, "pace", "希望轻松一点", consent=True)
        self.assertEqual(self.store.list_preferences(OTHER), [])
        with self.assertRaisesRegex(AssistantError, "assistant_preference_conflict"):
            self.store.delete_preference(OTHER, "pace", 1)

    def test_cas_concurrency_only_one_checkpoint_and_no_old_success(self):
        task = self.begin()
        def save(index):
            try:
                return self.store.checkpoint(OWNER, task["id"], 0, {"stop_reason": str(index)}, "waiting")
            except AssistantError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(save, (1, 2)))
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        self.assertIn("assistant_task_obsolete", results)
        self.assertEqual(self.store.get(OWNER, task["id"])["revision"], 1)
        self.store.checkpoint(OWNER, task["id"], 1, {}, "cancelled")
        next_task = self.begin()
        with self.assertRaisesRegex(AssistantError, "assistant_task_obsolete"):
            self.store.checkpoint(OWNER, task["id"], 2, {}, "succeeded")
        self.assertEqual(self.store.latest(OWNER, self.conversation["id"])["id"], next_task["id"])

    def test_explicit_action_after_waiting_checks_latest_and_orders_same_clock(self):
        self.store.clock = lambda: 1_800_000_000
        first = self.begin()
        with self.assertRaisesRegex(AssistantError, "assistant_task_busy"):
            self.begin()
        self.store.checkpoint(OWNER, first["id"], 0, {"waiting_for": "adoption"}, "waiting")
        second = self.store.begin(OWNER, self.conversation["id"], op(), 0, expected_latest_task_id=first["id"])
        self.assertEqual(self.store.latest(OWNER, self.conversation["id"])["id"], second["id"])
        self.store.checkpoint(OWNER, second["id"], 0, {}, "succeeded")
        with self.assertRaisesRegex(AssistantError, "assistant_task_obsolete"):
            self.store.begin(OWNER, self.conversation["id"], op(), 0, expected_latest_task_id=first["id"])

    def test_conversation_change_makes_task_stale_and_blocks_commit(self):
        task = self.begin()
        turn = op()
        pending = self.chat.begin_turn(OWNER, self.conversation["id"], turn, 0, "改成北京")
        self.chat.fail_turn(OWNER, self.conversation["id"], turn, pending["revision"])
        self.assertTrue(self.store.get(OWNER, task["id"])["stale"])
        with self.assertRaisesRegex(AssistantError, "assistant_revision_conflict"):
            self.store.checkpoint(OWNER, task["id"], 0, {}, "succeeded")

    def test_chat_bound_start_checkpoint_and_finalization_are_not_stale(self):
        operation = op()
        pending = self.chat.begin_turn(OWNER, self.conversation["id"], operation, 0, "去杭州")
        task = self.store.begin(OWNER, self.conversation["id"], operation, pending["revision"])
        waiting = self.store.checkpoint(OWNER, task["id"], 0, {"waiting_for": "confirmation"}, "waiting")
        final_chat = self.chat.finish_turn(OWNER, self.conversation["id"], operation, pending["revision"], intent(), "请核对需求。", [])
        result = self.store.finalize(OWNER, operation, waiting["revision"], final_chat["revision"], waiting["checkpoint"], "waiting")
        self.assertFalse(result["stale"])
        self.assertEqual(result["conversation_revision"], final_chat["revision"])
        self.assertEqual(result, self.store.get(OWNER, operation))
        with self.assertRaisesRegex(AssistantError, "assistant_task_obsolete"):
            self.store.finalize(OWNER, operation, waiting["revision"], final_chat["revision"], {}, "succeeded")

    def test_finalize_wrong_operation_or_arbitrary_version_cannot_rebind(self):
        task = self.begin()
        turn = op()
        pending = self.chat.begin_turn(OWNER, self.conversation["id"], turn, 0, "去杭州")
        final_chat = self.chat.finish_turn(OWNER, self.conversation["id"], turn, pending["revision"], intent(), "请核对。", [])
        with self.assertRaisesRegex(AssistantError, "assistant_task_obsolete"):
            self.store.finalize(OWNER, task["id"], 0, final_chat["revision"], {}, "succeeded")
        with self.assertRaisesRegex(AssistantError, "assistant_task_obsolete"):
            self.store.finalize(OWNER, task["id"], 0, final_chat["revision"],
                {"stop_reason": "post_commit_uncertain", "waiting_for": "readback",
                 "continuation": {"kind": "read_receipts_only", "operation_id": task["id"]}}, "uncertain")
        self.assertEqual(self.store.get(OWNER, task["id"])["state"], "running")

    def test_exact_successful_chat_can_mark_only_its_unfinished_followup_uncertain(self):
        operation = op()
        pending = self.chat.begin_turn(OWNER, self.conversation["id"], operation, 0, "去杭州")
        task = self.store.begin(OWNER, self.conversation["id"], operation, pending["revision"])
        committed = self.chat.finish_turn(OWNER, self.conversation["id"], operation, pending["revision"], intent(), "请核对。", [])
        with self.assertRaisesRegex(AssistantError, "assistant_task_obsolete"):
            self.store.finalize(OWNER, operation, 0, committed["revision"], {}, "uncertain")
        data = {"stop_reason": "post_commit_uncertain", "waiting_for": "readback",
                "continuation": {"kind": "read_receipts_only", "operation_id": operation}}
        with self.assertRaisesRegex(AssistantError, "assistant_task_obsolete"):
            self.store.finalize(OWNER, operation, 0, committed["revision"] + 1, data, "uncertain")
        with self.assertRaises(AssistantError):
            self.store.finalize(OTHER, operation, 0, committed["revision"], data, "uncertain")
        result = self.store.finalize(OWNER, operation, task["revision"], committed["revision"], data, "uncertain")
        self.assertFalse(result["stale"])
        self.assertEqual(result["state"], "uncertain")
        self.assertEqual(self.chat.get(OWNER, self.conversation["id"])["last_turn"]["state"], "succeeded")
        with self.assertRaisesRegex(AssistantError, "assistant_operation_already_used"):
            self.store.begin(OWNER, self.conversation["id"], operation, committed["revision"])

    def test_preference_explicit_consent_cas_reopen_update_delete(self):
        for kwargs in ({}, {"consent": False}, {"consent": True, "source": "model"}):
            with self.assertRaisesRegex(AssistantError, "assistant_preference_invalid"):
                self.store.set_preference(OWNER, "pace", "轻松", **kwargs)
        first = self.store.set_preference(OWNER, "pace", "轻松", consent=True)
        self.assertEqual(first["source"], "user")
        self.assertTrue(first["consent"])
        with self.assertRaisesRegex(AssistantError, "assistant_preference_conflict"):
            self.store.set_preference(OWNER, "pace", "紧凑", consent=True)
        changed = AssistantStore(self.engine).set_preference(OWNER, "pace", "紧凑", 1, consent=True)
        self.assertEqual(changed["version"], 2)
        with self.assertRaisesRegex(AssistantError, "assistant_preference_conflict"):
            self.store.delete_preference(OWNER, "pace", 1)
        self.store.delete_preference(OWNER, "pace", 2)
        self.assertEqual(self.store.list_preferences(OWNER), [])
        self.assertEqual(self.store.preference_versions(OWNER), {"pace": 3})
        self.assertEqual(self.store.preference_versions(OTHER), {})
        with self.assertRaisesRegex(AssistantError, "assistant_preference_conflict"):
            self.store.set_preference(OWNER, "pace", "旧创建请求", 0, consent=True)
        fresh = self.store.set_preference(OWNER, "pace", "重新确认的偏好", 3, consent=True)
        self.assertEqual(fresh["version"], 4)
        with self.assertRaisesRegex(AssistantError, "assistant_preference_conflict"):
            self.store.delete_preference(OWNER, "pace", 2)

    def test_checkpoint_bounds_and_trace_never_store_private_fields(self):
        task = self.begin()
        for payload in ({"prompt": "private"}, {"workspace": {"api_key": "private"}},
                        {"trace": [{"tool": "search_places", "arguments": {"name": "private"}}]},
                        {"trace": [{"tool": "含正文"}]}, {"result": float("nan")},
                        {"workspace": {"value": "x" * 200_001}}, {"trace": [{}] * 101}):
            with self.subTest(payload_type=list(payload)), self.assertRaises(AssistantError):
                self.store.checkpoint(OWNER, task["id"], 0, payload, "running")
        self.assertEqual(self.store.get(OWNER, task["id"])["revision"], 0)
        with self.assertRaises(AssistantError):
            validate_checkpoint({"workspace": [{"nested": "x" * 200_000}] * 12})

    def test_corrupt_schema_and_duplicate_json_fail_closed(self):
        task = self.begin()
        with self.engine.begin() as db:
            db.execute(update(AssistantTaskRow).where(AssistantTaskRow.id == task["id"]).values(checkpoint_json='{"trace":[],"trace":[]}'))
        with self.assertRaisesRegex(AssistantError, "assistant_record_invalid"):
            self.store.get(OWNER, task["id"])
        with self.engine.begin() as db:
            db.execute(text("ALTER TABLE assistant_tasks ADD COLUMN unexpected TEXT"))
        with self.engine.connect() as db, self.assertRaisesRegex(AssistantError, "invalid_assistant_schema"):
            validate_chat_extension(db)

    def test_explicit_provisioning_never_overwrites_existing_extension(self):
        task = self.begin()
        with self.assertRaisesRegex(Refused, "assistant_extension_already_present"):
            provision_assistant_extension(self.engine)
        self.assertEqual(self.store.get(OWNER, task["id"]), task)

    def test_commit_ack_loss_keeps_operation_occupied_and_never_resends(self):
        operation = op()
        original = Connection.commit
        def committed_then_lost_ack(db):
            original(db)
            raise OperationalError("controlled", {}, Exception("controlled acknowledgement loss"))
        with patch.object(Connection, "commit", new=committed_then_lost_ack):
            with self.assertRaisesRegex(Exception, "chat_storage_unavailable"):
                self.store.begin(OWNER, self.conversation["id"], operation, 0)
        self.assertEqual(self.store.get(OWNER, operation)["state"], "running")
        with self.assertRaisesRegex(AssistantError, "assistant_operation_already_used"):
            self.store.begin(OWNER, self.conversation["id"], operation, 0)

    def test_transaction_failure_before_commit_does_not_publish_partial_checkpoint(self):
        task = self.begin()
        def refused_commit(_db):
            raise OperationalError("controlled", {}, Exception("controlled precommit failure"))
        with patch.object(Connection, "commit", new=refused_commit):
            with self.assertRaisesRegex(Exception, "chat_storage_unavailable"):
                self.store.checkpoint(OWNER, task["id"], 0, {"waiting_for": "confirmation"}, "waiting")
        self.assertEqual(self.store.get(OWNER, task["id"]), task)


class AssistantMySQL(unittest.TestCase):
    """Opt-in InnoDB checks never use normal application DB configuration."""
    def setUp(self):
        from tests.mysql_support import temporary_mysql
        from core.auth import PASSWORD_HASHER
        from memory.identity_models import AccountRow
        from memory.mysql_storage import initialize_private_schema
        from memory.mysql_usage import initialize_usage_schema
        from tools.chat_places_storage import provision_chat_places_extension
        self.engine, cleanup = temporary_mysql()
        self.addCleanup(cleanup)
        initialize_private_schema(self.engine)
        initialize_usage_schema(self.engine, "1" * 32)
        seed_owner(self.engine)
        with self.engine.begin() as db:
            db.execute(AccountRow.__table__.update().values(password_hash=PASSWORD_HASHER.hash("isolated-assistant-test-password")))
        provision_chat_extension(self.engine)
        provision_chat_places_extension(self.engine)
        self.chat = ChatStore(self.engine)
        self.conversation = self.chat.create(OWNER, op())
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.folder.chmod(0o700)
        self.anchor = self.folder / "usage.identity"
        self.anchor.write_text("1" * 32 + "\n")
        self.anchor.chmod(0o600)

    def target(self):
        from tests.mysql_support import temporary_mysql
        engine, cleanup = temporary_mysql()
        self.addCleanup(cleanup)
        return engine

    def test_copy_upgrade_then_preserve_tasks_preferences_and_all_usage_states(self):
        from memory.mysql_usage import usage_attempts
        from tools.assistant_storage import mysql_copy
        from tools.chat_places_storage import mysql_copy as old_copy
        with self.engine.begin() as db:
            db.execute(usage_attempts.insert(), [{"id": uuid4().hex, "owner_id": OWNER, "service": "qwen_text", "created_at": 100,
                "state": state, "dispatched_at": None if state == "reserved" else 100}
                for state in ("reserved", "dispatching", "received", "failed", "cancelled")])
        target = self.target()
        result = mysql_copy(self.engine, target, self.folder / "upgrade.json", anchor=self.anchor, confirm_source_stopped=True)
        self.assertEqual(result["state"], "verified")
        self.assertFalse(result["quota_refunded"])
        store = AssistantStore(target)
        task = store.begin(OWNER, self.conversation["id"], op(), 0)
        saved = store.checkpoint(OWNER, task["id"], 0, {"trace": [{"tool": "search_places", "state": "uncertain"}]}, "uncertain")
        preference = store.set_preference(OWNER, "pace", "轻松", consent=True)
        copied = self.target()
        result2 = mysql_copy(target, copied, self.folder / "with-assistant.json", anchor=self.anchor, confirm_source_stopped=True)
        self.assertEqual(result2["state"], "verified")
        self.assertEqual(AssistantStore(copied).get(OWNER, task["id"]), saved)
        self.assertEqual(AssistantStore(copied).list_preferences(OWNER), [preference])
        with copied.connect() as db, target.connect() as original:
            self.assertEqual(sorted(db.execute(usage_attempts.select()).mappings(), key=lambda row: row["id"]),
                sorted(original.execute(usage_attempts.select()).mappings(), key=lambda row: row["id"]))
        with self.assertRaisesRegex(Refused, "assistant_extension_requires_own_copy_tool"):
            old_copy(target, self.target(), self.folder / "old-tool.json", anchor=self.anchor, confirm_source_stopped=True)

    def test_corrupt_source_checkpoint_rejected_before_target_ddl(self):
        from tools.assistant_storage import mysql_copy
        provision_assistant_extension(self.engine)
        task = AssistantStore(self.engine).begin(OWNER, self.conversation["id"], op(), 0)
        with self.engine.begin() as db:
            db.execute(update(AssistantTaskRow).where(AssistantTaskRow.id == task["id"]).values(checkpoint_json='{"prompt":"private"}'))
        target = self.target()
        with self.assertRaises(AssistantError):
            mysql_copy(self.engine, target, self.folder / "bad-source.json", anchor=self.anchor, confirm_source_stopped=True)
        with target.connect() as db:
            self.assertEqual(inspect(db).get_table_names(), [])

    def test_real_owner_lock_concurrent_admission_survives_reopen(self):
        provision_assistant_extension(self.engine)
        def reserve(_):
            try:
                return AssistantStore(self.engine).begin(OWNER, self.conversation["id"], op(), 0)
            except AssistantError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reserve, range(2)))
        self.assertEqual(sum(isinstance(row, dict) for row in results), 1)
        task = next(row for row in results if isinstance(row, dict))
        self.engine.dispose()
        self.assertEqual(AssistantStore(self.engine).get(OWNER, task["id"]), task)


if __name__ == "__main__":
    unittest.main()
