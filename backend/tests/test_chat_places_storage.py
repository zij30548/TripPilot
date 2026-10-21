"""Real temporary disk transactions; zero model/AMap calls."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import OperationalError

from app.chat_places_storage import ChatPlacesError, ChatPlacesStore, validate_chat_places_extension
from app.chat_storage import ChatError, ChatStore
from app.storage import engine_for
from tests.archive_owner import OWNER, seed_owner
from tests.test_chat_storage import intent
from tools.chat_places_storage import provision_chat_places_extension
from tools.chat_storage import provision_chat_extension

OTHER = "b" * 32


def op() -> str:
    return str(uuid4())


def place(identifier: str = "poi-one", *, city_id: str = "330100", name: str = "同名地点") -> dict:
    from app.chat_cities import city_for
    city = city_for(city_id)
    return {"id": identifier, "name": name, "address": "受控测试地址", "latitude": 30.25, "longitude": 120.15,
            "category": None, "source": "amap", "region": {"citycode": city.citycode, "cityname": city.official_name,
            "adcode": city.district_adcodes[-1]}}


def batch(city_id: str = "330100", *, role: str = "required", places: list | None = None) -> dict:
    return {"id": op(), "city_id": city_id, "role": role, "keyword": "受控地点", "queried_at": "2026-10-21T09:00:00Z",
            "places": places if places is not None else [place(city_id=city_id)]}


class PlacesDiskStorage(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.path = self.folder / "private.sqlite3"
        self.engine = engine_for(str(self.path))
        self.addCleanup(self.engine.dispose)
        self.addCleanup(engine_for.cache_clear)
        seed_owner(self.engine); seed_owner(self.engine, OTHER)
        provision_chat_extension(self.engine)
        self.chat = ChatStore(self.engine, clock=lambda: 1_800_000_000)
        self.conversation = self.chat.create(OWNER, op())
        provision_chat_places_extension(self.engine)
        self.store = ChatPlacesStore(self.engine, clock=lambda: 1_800_000_000)

    def city(self, city_id="330100"):
        view = self.store.get(OWNER, self.conversation["id"])
        return self.store.set_city(OWNER, view["conversation_id"], op(), view["intent_revision"], view["revision"], city_id)

    def search(self, view, *, places=None, role="required"):
        operation = op()
        pending = self.store.begin_operation(OWNER, view["conversation_id"], operation, view["intent_revision"], view["revision"],
                                             "search", {"keyword": "受控地点", "role": role})
        result = self.store.finish_operation(OWNER, view["conversation_id"], operation, pending["revision"], view["intent_revision"],
                                             batch=batch(view["city_id"], role=role, places=places))
        return result

    def confirm(self, view, place_id, role="required", operation=None):
        return self.store.confirm(OWNER, view["conversation_id"], operation or op(), view["intent_revision"], view["revision"],
                                  view["batch"]["id"], place_id, role)

    def update_intent(self, destination="hangzhou"):
        current = self.chat.get(OWNER, self.conversation["id"])
        operation = op()
        pending = self.chat.begin_turn(OWNER, current["id"], operation, current["revision"], "修改目的地")
        return self.chat.finish_turn(OWNER, current["id"], operation, pending["revision"], {**intent(), "destination_id": destination}, "请核对。", [])

    def test_get_empty_is_read_only_and_old_schema_never_auto_expands(self):
        before = self.store.get(OWNER, self.conversation["id"])
        self.assertEqual(before["revision"], 0)
        self.assertIsNone(before["city_id"])
        with self.engine.connect() as db:
            self.assertEqual(db.execute(text("SELECT COUNT(*) FROM chat_places_workspaces")).scalar_one(), 0)
            self.assertEqual(db.execute(text("SELECT COUNT(*) FROM chat_places_operations")).scalar_one(), 0)
        source = engine_for(str(self.folder / "old.sqlite3")); self.addCleanup(source.dispose)
        seed_owner(source); provision_chat_extension(source)
        old = ChatStore(source).create(OWNER, op())
        with self.assertRaisesRegex(ChatPlacesError, "chat_places_migration_required"):
            ChatPlacesStore(source).get(OWNER, old["id"])
        with source.connect() as db:
            self.assertNotIn("chat_places_workspaces", inspect(db).get_table_names())

    def test_same_body_retry_is_current_frozen_result_different_body_conflicts(self):
        operation, cid = op(), self.conversation["id"]
        result = self.store.set_city(OWNER, cid, operation, 0, 0, "330100")
        self.assertEqual(self.store.set_city(OWNER, cid, operation, 0, 0, "330100"), result)
        with self.assertRaisesRegex(ChatPlacesError, "chat_places_operation_conflict"):
            self.store.set_city(OWNER, cid, operation, 0, 0, "310000")
        other = self.chat.create(OTHER, op())
        self.assertEqual(self.store.set_city(OTHER, other["id"], operation, 0, 0, "310000")["city_id"], "310000")
        with self.assertRaises(ChatError) as error:
            self.store.get(OTHER, cid)
        self.assertEqual(error.exception.status, 404)

    def test_actual_candidates_only_preview_confirm_identity_and_capacity(self):
        searched = self.search(self.city(), places=[place(str(i)) for i in range(7)])
        self.assertEqual(searched["required_places"], [])
        with self.assertRaisesRegex(ChatPlacesError, "chat_places_unknown_place"):
            self.confirm(searched, "forged")
        with self.assertRaisesRegex(ChatPlacesError, "chat_places_batch_obsolete"):
            self.confirm(searched, "0", "accommodation")
        current = searched
        for index in range(6):
            current = self.confirm(current, str(index))
        self.assertEqual([item["id"] for item in current["required_places"]], [str(i) for i in range(6)])
        with self.assertRaisesRegex(ChatPlacesError, "chat_places_required_limit"):
            self.confirm(current, "6")
        with self.assertRaisesRegex(ChatPlacesError, "chat_places_duplicate_place"):
            self.confirm(current, "0")
        # Same name but distinct IDs remain separate; no name-based merge.
        self.assertEqual(len({item["name"] for item in current["required_places"]}), 1)

    def test_accommodation_can_share_visit_identity_and_remove_is_guarded(self):
        view = self.confirm(self.search(self.city()), "poi-one")
        view = self.confirm(self.search(view, role="accommodation"), "poi-one", "accommodation")
        self.assertEqual(view["accommodation"], view["required_places"][0])
        removed = self.store.remove(OWNER, view["conversation_id"], op(), view["intent_revision"], view["revision"], "poi-one", "accommodation")
        self.assertIsNone(removed["accommodation"])
        self.assertEqual(len(removed["required_places"]), 1)
        with self.assertRaisesRegex(ChatPlacesError, "chat_places_unknown_place"):
            self.store.remove(OWNER, removed["conversation_id"], op(), removed["intent_revision"], removed["revision"], "forged", "required")

    def test_city_aba_never_revives_batch_or_old_confirmation(self):
        original = self.search(self.city())
        second = self.store.set_city(OWNER, original["conversation_id"], op(), 0, original["revision"], "310000")
        third = self.store.set_city(OWNER, original["conversation_id"], op(), 0, second["revision"], "330100")
        self.assertIsNone(third["batch"])
        with self.assertRaisesRegex(ChatPlacesError, "chat_places_revision_conflict"):
            self.confirm(original, "poi-one")
        with self.assertRaisesRegex(ChatPlacesError, "chat_places_batch_obsolete"):
            self.store.confirm(OWNER, third["conversation_id"], op(), 0, third["revision"], original["batch"]["id"], "poi-one", "required")

    def test_intent_change_stale_get_hides_batch_explicit_sync_preserves_only_same_city(self):
        view = self.confirm(self.search(self.city()), "poi-one")
        changed = self.update_intent()
        stale = self.store.get(OWNER, view["conversation_id"])
        self.assertTrue(stale["stale"])
        self.assertIsNone(stale["batch"])
        with self.assertRaisesRegex(ChatPlacesError, "chat_intent_revision_conflict"):
            self.confirm(view, "poi-one")
        synced = self.store.sync(OWNER, view["conversation_id"], op(), changed["revision"], stale["revision"])
        self.assertFalse(synced["stale"])
        self.assertEqual(len(synced["required_places"]), 1)
        self.assertIsNone(synced["batch"])
        changed = self.update_intent("beijing")
        synced = self.store.sync(OWNER, view["conversation_id"], op(), changed["revision"], synced["revision"])
        self.assertEqual(synced["city_id"], "110000")
        self.assertEqual(synced["required_places"], [])

    def test_sync_resolves_new_six_digit_city_without_legacy_fallback(self):
        changed = self.update_intent("320100")
        synced = self.store.sync(OWNER, changed["id"], op(), changed["revision"], 0)
        self.assertEqual(synced["city_id"], "320100")
        self.assertEqual(self.chat.get(OWNER, changed["id"])["requirements"]["destination_id"], "320100")

    def test_cancel_reopen_and_late_success_error_old_cancel_do_not_restore(self):
        view, operation = self.city(), op()
        pending = self.store.begin_operation(OWNER, view["conversation_id"], operation, 0, view["revision"], "ask", {"text": "查一下地点"})
        cancelled = self.store.cancel(OWNER, view["conversation_id"], operation, pending["revision"])
        self.assertEqual(cancelled, self.store.cancel(OWNER, view["conversation_id"], operation, pending["revision"]))
        with self.assertRaises(ChatError):
            self.store.check_running(OWNER, view["conversation_id"], operation, pending["revision"], 0)
        newer = self.store.begin_operation(OWNER, view["conversation_id"], op(), 0, cancelled["revision"], "ask", {"text": "新请求"})
        for extra in ({"batch": batch()}, {"error_code": "request_failed"}):
            with self.assertRaises(ChatError):
                self.store.finish_operation(OWNER, view["conversation_id"], operation, pending["revision"], 0, **extra)
        with self.assertRaises(ChatError):
            self.store.cancel(OWNER, view["conversation_id"], operation, pending["revision"])
        self.assertEqual(self.store.get(OWNER, view["conversation_id"])["revision"], newer["revision"])

    def test_pending_uncertain_and_completed_retry_never_authorize_another_dispatch(self):
        view, operation, request = self.city(), op(), {"keyword": "地点", "role": "required"}
        pending = self.store.begin_operation(OWNER, view["conversation_id"], operation, 0, view["revision"], "search", request)
        reopened = ChatPlacesStore(self.engine)
        with self.assertRaisesRegex(ChatPlacesError, "chat_places_operation_pending"):
            reopened.begin_operation(OWNER, view["conversation_id"], operation, 0, view["revision"], "search", request)
        result = reopened.finish_operation(OWNER, view["conversation_id"], operation, pending["revision"], 0, error_code="request_uncertain")
        self.assertEqual(result["last_operation"]["state"], "uncertain")
        retry = reopened.begin_operation(OWNER, view["conversation_id"], operation, 0, view["revision"], "search", request)
        self.assertFalse(retry["dispatch"])
        self.assertEqual(retry["workspace"], result)

    def test_concurrent_requests_reserve_only_one_and_quota_receipts_bounded(self):
        view = self.city()
        def reserve(_):
            try:
                return self.store.begin_operation(OWNER, view["conversation_id"], op(), 0, view["revision"], "ask", {"text": "地点"})
            except ChatError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(reserve, range(3)))
        self.assertEqual(sum(isinstance(item, dict) for item in results), 1)
        with patch("app.chat_places_storage.MAX_OPERATIONS", 2), self.assertRaisesRegex(ChatPlacesError, "chat_places_operation_limit"):
            pending = next(item for item in results if isinstance(item, dict))
            cancelled = self.store.cancel(OWNER, view["conversation_id"], pending["operation_id"], pending["revision"])
            self.store.set_city(OWNER, view["conversation_id"], op(), 0, cancelled["revision"], "310000")

    def test_bad_batch_cross_city_corruption_and_exact_schema_fail_closed(self):
        view = self.city()
        pending = self.store.begin_operation(OWNER, view["conversation_id"], op(), 0, view["revision"], "search", {"keyword": "地点", "role": "required"})
        with self.assertRaisesRegex(ChatPlacesError, "chat_places_city_mismatch"):
            self.store.finish_operation(OWNER, view["conversation_id"], pending["operation_id"], pending["revision"], 0, batch=batch("310000"))
        with self.assertRaisesRegex(ChatPlacesError, "chat_places_batch_mismatch"):
            self.store.finish_operation(OWNER, view["conversation_id"], pending["operation_id"], pending["revision"], 0,
                                        batch={**batch(), "keyword": "另一个请求的候选"})
        self.store.cancel(OWNER, view["conversation_id"], pending["operation_id"], pending["revision"])
        with self.engine.begin() as db:
            db.execute(text("UPDATE chat_places_workspaces SET required_places_json=:data"), {"data": '[{"id":"a","id":"b"}]'})
        with self.assertRaises(ChatError):
            self.store.get(OWNER, view["conversation_id"])
        with self.engine.begin() as db:
            db.execute(text("ALTER TABLE chat_places_workspaces ADD COLUMN ignored TEXT"))
        with self.engine.connect() as db, self.assertRaisesRegex(ChatPlacesError, "invalid_chat_places_schema"):
            validate_chat_places_extension(db)

    def test_commit_failure_rollback_and_lost_ack_leave_receipt_occupied(self):
        view, operation = self.city(), op()
        original = Connection.commit
        def lost_ack(db):
            original(db)
            raise OperationalError("private query", {}, Exception("secret"))
        with patch.object(Connection, "commit", new=lost_ack), self.assertRaisesRegex(ChatError, "chat_storage_unavailable"):
            self.store.begin_operation(OWNER, view["conversation_id"], operation, 0, view["revision"], "ask", {"text": "地点"})
        self.assertEqual(self.store.get(OWNER, view["conversation_id"])["last_operation"]["state"], "running")
        with self.assertRaisesRegex(ChatPlacesError, "chat_places_operation_pending"):
            self.store.begin_operation(OWNER, view["conversation_id"], operation, 0, view["revision"], "ask", {"text": "地点"})
        # A pre-commit failure rolls back a fresh independent conversation.
        new = self.chat.create(OWNER, op())
        with patch.object(Connection, "commit", side_effect=OperationalError("private", {}, Exception("secret"))), self.assertRaises(ChatError):
            self.store.set_city(OWNER, new["id"], op(), 0, 0, "330100")
        self.assertEqual(self.store.get(OWNER, new["id"])["revision"], 0)

    def test_chat_delete_erases_private_place_text_but_retains_operation_tombstones(self):
        view = self.confirm(self.search(self.city()), "poi-one")
        pending = self.store.begin_operation(OWNER, view["conversation_id"], op(), 0, view["revision"], "ask", {"text": "私密地点描述"})
        with self.engine.connect() as db:
            hashes = db.execute(text("SELECT operation_id,input_hash FROM chat_places_operations ORDER BY id")).all()
        self.chat.delete(OWNER, view["conversation_id"], 0)
        with self.engine.connect() as db:
            self.assertEqual(db.execute(text("SELECT COUNT(*) FROM chat_places_workspaces")).scalar_one(), 0)
            self.assertTrue(all(value is None for value in db.execute(text("SELECT result_json FROM chat_places_operations")).scalars()))
            self.assertEqual(db.execute(text("SELECT operation_id,input_hash FROM chat_places_operations ORDER BY id")).all(), hashes)
            self.assertEqual(db.execute(text("SELECT state FROM chat_places_operations WHERE operation_id=:id"), {"id": pending["operation_id"]}).scalar_one(), "cancelled")
        with self.assertRaises(ChatError) as error:
            self.store.finish_operation(OWNER, view["conversation_id"], pending["operation_id"], pending["revision"], 0, batch=batch())
        self.assertEqual(error.exception.status, 404)
        new = self.chat.create(OWNER, op())
        with self.assertRaisesRegex(ChatPlacesError, "chat_places_operation_conflict"):
            self.store.begin_operation(OWNER, new["id"], pending["operation_id"], 0, 0, "ask", {"text": "私密地点描述"})


if __name__ == "__main__":
    unittest.main()
