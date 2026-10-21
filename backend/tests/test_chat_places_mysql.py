"""Opt-in local InnoDB extension/copy tests, isolated random databases only."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import inspect, select, text
from sqlmodel import Session

from core.auth import PASSWORD_HASHER
from memory.chat_models import ConversationRow
from memory.chat_places_storage import ChatPlacesStore, validate_chat_places_extension
from memory.chat_storage import ChatError, ChatStore
from memory.mysql_storage import initialize_private_schema, validate_private_schema
from memory.mysql_usage import initialize_usage_schema, usage_attempts, validate_usage_schema
from memory.identity_models import AccountRow, InvitationRow, LoginBucketRow, SessionRow
from memory.saved_trips import SavedTripRow, save
from core.schemas.saved_trip import SaveTrip
from tests.archive_owner import OWNER, seed_owner
from tests.mysql_support import temporary_mysql
from tests.test_chat_places_storage import batch, op
from tests.test_chat_storage import intent
from tools.chat_places_storage import mysql_copy, provision_chat_places_extension
from tools.chat_storage import Refused, mysql_copy as old_copy, provision_chat_extension
from tools.mysql_backup import backup
from tests.test_saved_trips import payload, snapshot
from tests.test_destinations import modern_snapshot
from tests.test_duration import archive_for, city_request, controlled_schedule


class PlacesMySQL(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password_hash = PASSWORD_HASHER.hash("controlled-places-migration-passphrase")

    def setUp(self):
        self.engine, close = temporary_mysql(); self.addCleanup(close)
        initialize_private_schema(self.engine)
        initialize_usage_schema(self.engine, "1" * 32)
        seed_owner(self.engine)
        with self.engine.begin() as db:
            db.execute(AccountRow.__table__.update().values(password_hash=self.password_hash))
        provision_chat_extension(self.engine)
        self.chat = ChatStore(self.engine)
        self.conversation = self.chat.create(OWNER, op())
        pending = self.chat.begin_turn(OWNER, self.conversation["id"], op(), 0, "去杭州")
        self.conversation = self.chat.finish_turn(OWNER, self.conversation["id"], pending["conversation"]["last_turn"]["operation_id"],
                                                   pending["revision"], intent(), "请核对。", [])
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.anchor = self.folder / "usage.identity"
        self.anchor.write_text("1" * 32 + "\n"); self.anchor.chmod(0o600)

    def target(self):
        engine, close = temporary_mysql(); self.addCleanup(close)
        return engine

    def test_real_copy_preserves_chat_all_attempt_states_then_empty_rollback(self):
        states = ["reserved", "dispatching", "received", "failed", "cancelled"]
        with self.engine.begin() as db:
            db.execute(usage_attempts.insert(), [{"id": uuid4().hex, "owner_id": OWNER, "service": "qwen_text", "created_at": 100,
                "state": state, "dispatched_at": None if state == "reserved" else 100} for state in states])
        archived = []
        with Session(self.engine) as db:
            for content in (snapshot(), modern_snapshot(), archive_for(controlled_schedule(city_request(days=7)))):
                request = SaveTrip.model_validate_json(json.dumps({"operation_id": uuid4().hex, "name": "受控迁移兼容", "snapshot": content}), strict=True)
                archived.append(save(db, request, OWNER).model_dump(mode="json"))
        target = self.target()
        receipt = self.folder / "copy.json"
        result = mysql_copy(self.engine, target, receipt, anchor=self.anchor, confirm_source_stopped=True)
        self.assertEqual(result["state"], "verified")
        self.assertFalse(result["quota_refunded"])
        self.assertEqual(receipt.stat().st_mode & 0o777, 0o600)
        validate_private_schema(target)
        self.assertEqual(ChatStore(target).get(OWNER, self.conversation["id"]), self.conversation)
        with target.connect() as db:
            self.assertTrue(validate_chat_places_extension(db))
            validate_usage_schema(db, "1" * 32)
            self.assertEqual(sorted(db.execute(select(usage_attempts.c.state)).scalars()), sorted(states))
            self.assertEqual(sorted(db.execute(select(SavedTripRow.schema_version)).scalars()), ["saved-trip-v1", "saved-trip-v2", "saved-trip-v3"])
        from memory.saved_trips import get_record
        with Session(target) as db:
            self.assertEqual([get_record(db, row["id"], OWNER).model_dump(mode="json") for row in archived], archived)
        with self.engine.connect() as db:
            self.assertFalse(validate_chat_places_extension(db, required=False))
        rollback = self.target()
        mysql_copy(target, rollback, self.folder / "rollback.json", anchor=self.anchor, confirm_source_stopped=True, rollback=True)
        self.assertEqual(ChatStore(rollback).get(OWNER, self.conversation["id"]), self.conversation)
        with rollback.connect() as db:
            self.assertFalse(validate_chat_places_extension(db, required=False))
            self.assertEqual(sorted(db.execute(select(usage_attempts.c.state)).scalars()), sorted(states))
        store = ChatPlacesStore(target)
        store.sync(OWNER, self.conversation["id"], op(), self.conversation["revision"], 0)
        with self.assertRaisesRegex(Refused, "rollback_rehearsal_requires_empty_chat_places"):
            mysql_copy(target, self.target(), self.folder / "refused.json", anchor=self.anchor, confirm_source_stopped=True, rollback=True)

    def test_real_account_lock_one_running_search_and_reopen_confirm(self):
        provision_chat_places_extension(self.engine)
        validate_private_schema(self.engine)
        store = ChatPlacesStore(self.engine)
        view = store.sync(OWNER, self.conversation["id"], op(), self.conversation["revision"], 0)
        def reserve(_):
            try:
                return store.begin_operation(OWNER, view["conversation_id"], op(), view["intent_revision"], view["revision"], "search", {"keyword": "西湖", "role": "required"})
            except ChatError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(reserve, range(3)))
        self.assertEqual(sum(isinstance(item, dict) for item in results), 1)
        pending = next(item for item in results if isinstance(item, dict))
        result = store.finish_operation(OWNER, view["conversation_id"], pending["operation_id"], pending["revision"], view["intent_revision"], batch={**batch(), "keyword": "西湖"})
        reopened = ChatPlacesStore(self.engine)
        confirmed = reopened.confirm(OWNER, view["conversation_id"], op(), view["intent_revision"], result["revision"], result["batch"]["id"], "poi-one", "required")
        self.assertEqual(confirmed["required_places"][0]["id"], "poi-one")
        self.assertEqual(ChatPlacesStore(self.engine).get(OWNER, view["conversation_id"]), confirmed)
        with self.engine.begin() as db:
            db.execute(text("ALTER TABLE chat_places_operations ADD COLUMN unexpected VARCHAR(3)"))
        with self.assertRaisesRegex(ChatError, "invalid_chat_places_schema"):
            validate_private_schema(self.engine)

    def test_preflight_wrong_anchor_corrupt_chat_or_nonempty_target_does_not_ddl(self):
        target = self.target()
        self.anchor.write_text("f" * 32 + "\n")
        with self.assertRaisesRegex(Exception, "usage_identity|anchor"):
            mysql_copy(self.engine, target, self.folder / "bad-anchor.json", anchor=self.anchor, confirm_source_stopped=True)
        with target.connect() as db:
            self.assertEqual(inspect(db).get_table_names(), [])
        self.anchor.write_text("1" * 32 + "\n")
        with self.engine.begin() as db:
            db.execute(ConversationRow.__table__.update().values(requirements_json='{"travelers":2,"travelers":3}'))
        with self.assertRaises((ChatError, ValueError)):
            mysql_copy(self.engine, target, self.folder / "bad-chat.json", anchor=self.anchor, confirm_source_stopped=True)
        with target.connect() as db:
            self.assertEqual(inspect(db).get_table_names(), [])

    def test_partial_ddl_failure_preserves_failed_receipt_and_target_unready(self):
        target = self.target()
        from tools import chat_places_storage as module
        original = module.initialize_usage_schema
        def fail_after_usage(*args):
            original(*args)
            raise OSError("controlled_failure")
        receipt = self.folder / "partial.json"
        with patch.object(module, "initialize_usage_schema", new=fail_after_usage), self.assertRaises(OSError):
            mysql_copy(self.engine, target, receipt, anchor=self.anchor, confirm_source_stopped=True)
        self.assertEqual(json.loads(receipt.read_text())["state"], "failed_or_uncertain_preserve_artifacts")
        with target.connect() as db:
            self.assertIn("accounts", inspect(db).get_table_names())
            self.assertIsNone(db.execute(text("SELECT value FROM storage_metadata WHERE `key`='version'")).scalar())
        with self.assertRaisesRegex(Refused, "mysql_target_not_empty"):
            mysql_copy(self.engine, target, self.folder / "no-overwrite.json", anchor=self.anchor, confirm_source_stopped=True)

    def test_old_copy_and_backup_never_silently_omit_new_extension(self):
        provision_chat_places_extension(self.engine)
        with self.assertRaisesRegex(Refused, "chat_places_extension_requires_own_copy_tool"):
            old_copy(self.engine, self.target(), self.folder / "old.json", anchor=self.anchor, confirm_source_stopped=True, rollback=True)
        with self.assertRaisesRegex(Exception, "backup_chat_extension_requires_chat_copy_tool"):
            backup(self.engine, self.anchor, self.folder / "must-not-exist.jsonl")
        self.assertFalse((self.folder / "must-not-exist.jsonl").exists())

    def test_exact_schema_rejects_narrowed_timestamp_integer(self):
        provision_chat_places_extension(self.engine)
        with self.engine.begin() as db:
            db.execute(text("ALTER TABLE chat_places_workspaces MODIFY created_at INTEGER NOT NULL"))
        with self.assertRaisesRegex(ChatError, "invalid_chat_places_schema"):
            validate_private_schema(self.engine)

    def assert_content_rejected_before_ddl(self, table: str):
        target, receipt = self.target(), self.folder / (table + ".json")
        with self.assertRaisesRegex(Refused, "invalid_source_" + table):
            mysql_copy(self.engine, target, receipt, anchor=self.anchor, confirm_source_stopped=True)
        with target.connect() as db:
            self.assertEqual(inspect(db).get_table_names(), [])
        self.assertEqual(json.loads(receipt.read_text())["state"], "failed_or_uncertain_preserve_artifacts")

    def test_corrupt_account_rejected_before_any_target_ddl(self):
        with self.engine.begin() as db:
            db.execute(AccountRow.__table__.update().values(password_hash="not-argon2"))
        self.assert_content_rejected_before_ddl("accounts")

    def test_corrupt_session_rejected_before_any_target_ddl(self):
        with self.engine.begin() as db:
            db.execute(SessionRow.__table__.insert().values(id=uuid4().hex, token_hash="a" * 64, user_id=OWNER,
                csrf_token="b" * 43, created_at=100, expires_at=50))
        self.assert_content_rejected_before_ddl("auth_sessions")

    def test_corrupt_archive_requires_full_snapshot_replay_before_ddl(self):
        with Session(self.engine) as db:
            archived = save(db, SaveTrip.model_validate_json(json.dumps(payload()), strict=True), OWNER)
        with self.engine.begin() as db:
            db.execute(SavedTripRow.__table__.update().where(SavedTripRow.id == archived.id).values(snapshot_json="{}"))
        self.assert_content_rejected_before_ddl("saved_trips")

    def test_invalid_usage_dispatch_state_rejected_without_replenishing(self):
        with self.engine.begin() as db:
            db.execute(usage_attempts.insert().values(id=uuid4().hex, owner_id=OWNER, service="qwen_text",
                created_at=100, state="reserved", dispatched_at=101))
        self.assert_content_rejected_before_ddl("usage_attempts")
        with self.engine.connect() as db:
            self.assertEqual(db.execute(select(usage_attempts.c.state)).scalar_one(), "reserved")

    def test_invalid_invitation_consumption_and_negative_bucket_are_rejected(self):
        with self.engine.begin() as db:
            db.execute(InvitationRow.__table__.insert().values(id=uuid4().hex, token_hash="c" * 64,
                created_at=100, expires_at=200, consumed_by=OWNER, consumed_at=None, revoked=False))
        self.assert_content_rejected_before_ddl("invitations")
        with self.engine.begin() as db:
            db.execute(InvitationRow.__table__.delete())
            db.execute(LoginBucketRow.__table__.insert().values(id="d" * 64, window_start=100, attempts=-1))
        self.assert_content_rejected_before_ddl("login_buckets")

    def test_nonlatest_chat_turn_identity_is_checked(self):
        pending = self.chat.begin_turn(OWNER, self.conversation["id"], op(), self.conversation["revision"], "继续")
        self.chat.fail_turn(OWNER, self.conversation["id"], pending["conversation"]["last_turn"]["operation_id"], pending["revision"])
        with self.engine.begin() as db:
            db.execute(text("UPDATE chat_turns SET operation_id='not-a-uuid' WHERE frozen_revision=1"))
        target = self.target()
        with self.assertRaisesRegex(Refused, "invalid_source_chat_identity"):
            mysql_copy(self.engine, target, self.folder / "old-turn.json", anchor=self.anchor, confirm_source_stopped=True)
        with target.connect() as db:
            self.assertEqual(inspect(db).get_table_names(), [])

    def test_fresh_snapshot_detects_concurrent_source_change(self):
        from tools import chat_places_storage as module
        original = module.initialize_private_schema
        def changed_source(*args, **kwargs):
            original(*args, **kwargs)
            with self.engine.begin() as db:
                db.execute(AccountRow.__table__.update().values(username="controlled-concurrent-change"))
        target, receipt = self.target(), self.folder / "source-changed.json"
        with patch.object(module, "initialize_private_schema", new=changed_source), self.assertRaisesRegex(Refused, "source_snapshot_changed"):
            mysql_copy(self.engine, target, receipt, anchor=self.anchor, confirm_source_stopped=True)
        self.assertEqual(json.loads(receipt.read_text())["state"], "failed_or_uncertain_preserve_artifacts")
        with target.connect() as db:
            self.assertIn("chat_places_workspaces", inspect(db).get_table_names())


if __name__ == "__main__":
    unittest.main()
