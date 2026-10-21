"""Offline acceptance setup boundaries. No MySQL or upstream is contacted."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from memory.chat_storage import ChatStore
from memory.storage import engine_for
from tests.archive_owner import OWNER, seed_owner
from tools.chat_acceptance_environment import (CASE_IDS, Refused, _directory, _new,
    admin_url, prepare, seed_conversations, seed_plan, usage_limits, validate_seeds)
from tools.chat_places_storage import provision_chat_places_extension
from tools.chat_storage import provision_chat_extension


def seeds():
    result = {identifier: {"kind": "empty"} for identifier in CASE_IDS}
    intent = {"destination_id": "320500", "start_date": "2026-11-01", "end_date": "2026-11-03",
        "travelers": 2, "budget_cny": "3000", "accommodation_hint": "观前街附近",
        "required_place_hints": ["拙政园"], "preferences": [], "transport_mode": "walking"}
    result["api02"] = {"kind": "intent", "text": "去苏州，2026年11月1日至3日，两人预算3000，住观前街附近，想去拙政园，步行。", "requirements": intent}
    result["api05"] = {"kind": "places", "text": "去苏州", "requirements": {"destination_id": "320500"}}
    return result


class AcceptanceEnvironment(unittest.TestCase):
    def test_only_explicit_isolated_admin_address(self):
        self.assertEqual(admin_url("mysql+pymysql://root@127.0.0.1:13306/trippilot_test_control").port, 13306)
        for value in ("", "mysql+pymysql://root@localhost:13306/trippilot_test_control",
            "mysql+pymysql://root@127.0.0.1:3306/trippilot_test_control",
            "mysql+pymysql://root@127.0.0.1:13306/trippilot",
            "mysql+pymysql://root@127.0.0.1:13306/trippilot_test_control?charset=utf8mb4"):
            with self.subTest(value=value), self.assertRaises(Refused):
                admin_url(value)

    def test_limits_only_authorized_services(self):
        limits = usage_limits()
        self.assertEqual(len(limits), 8)
        self.assertEqual(limits["qwen_text"], {"account": 12, "global": 12})
        self.assertEqual(limits["amap_poi"], {"account": 12, "global": 12})
        self.assertTrue(all(item["global"] == 0 for name, item in limits.items() if name not in {"qwen_text", "amap_poi"}))

    def test_private_files_exclusive_no_overwrite_or_symlink(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder).resolve() / "new"
            _directory(target)
            self.assertEqual(target.stat().st_mode & 0o777, 0o700)
            path = target / "receipt.json"
            _new(path, {"example": "not-secret"})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                _new(path, {"changed": True})
            self.assertEqual(json.loads(path.read_text()), {"example": "not-secret"})
            with self.assertRaises(Refused):
                _directory(target)
            link = Path(folder) / "link"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaises(Refused):
                _directory(link / "no")

    def test_bad_seeds_rejected_before_output_or_sql(self):
        with tempfile.TemporaryDirectory() as folder, patch("tools.chat_acceptance_environment.create_engine") as sql:
            output = Path(folder).resolve() / "new"
            bad = seeds(); bad["api02"]["requirements"]["destination_id"] = "invented-city"
            with self.assertRaises(Refused):
                prepare(output, "mysql+pymysql://root@127.0.0.1:13306/trippilot_test_control", bad)
            self.assertFalse(output.exists()); sql.assert_not_called()
        with self.assertRaises(Refused):
            validate_seeds({"api01": {"kind": "empty"}})

    def test_controlled_seed_history_revision_and_owner_isolation(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = engine_for(str(Path(folder) / "isolated.sqlite3"))
            try:
                seed_owner(engine); seed_owner(engine, "b" * 32)
                provision_chat_extension(engine); provision_chat_places_extension(engine)
                with patch("httpx.AsyncClient.send", side_effect=AssertionError("no network allowed")):
                    views = seed_conversations(engine, OWNER, seeds())
                self.assertEqual(len(views), 9)
                self.assertEqual(views["chrome"]["conversation"]["revision"], 0)
                self.assertEqual(views["api02"]["conversation"]["revision"], 2)
                self.assertEqual(views["api02"]["seed_kind"], "intent")
                self.assertEqual(views["api02"]["conversation"]["requirements"]["destination_id"], "320500")
                self.assertEqual(views["api05"]["places"]["city_id"], "320500")
                self.assertEqual(views["api05"]["places"]["revision"], 1)
                self.assertEqual(views["api07"]["places"]["city_id"], None)
                self.assertEqual(ChatStore(engine).list("b" * 32)["items"], [])
            finally:
                engine.dispose(); engine_for.cache_clear()

    def test_frozen_case_seed_projection_excludes_questions_and_labels(self):
        manifest = json.loads((Path(__file__).resolve().parents[1] / "evaluations/chat12-acceptance-v1.json").read_text())
        plan = seed_plan(manifest)
        self.assertEqual(set(plan), set(CASE_IDS))
        self.assertEqual(plan["api05"]["kind"], "places")
        self.assertNotIn("expected", json.dumps(plan))
        self.assertNotIn(manifest["cases"][1]["user_text"], json.dumps(plan, ensure_ascii=False))
        self.assertEqual(plan["chrome"], {"kind": "empty"})

    def test_sql_failure_preserves_private_receipt_and_does_not_retry(self):
        with tempfile.TemporaryDirectory() as folder, patch("tools.chat_acceptance_environment.create_engine") as factory:
            factory.return_value.connect.side_effect = OSError("must not appear in public error")
            output = Path(folder).resolve() / "new"
            with self.assertRaisesRegex(Refused, "^preparation_failed_preserve_resources$"):
                prepare(output, "mysql+pymysql://root@127.0.0.1:13306/trippilot_test_control", seeds())
            failure = json.loads((output / "failed.json").read_text())
            self.assertFalse(failure["automatic_retry"])
            self.assertEqual(failure["phase"], "new_database_and_dml_user")
            self.assertTrue((output / "resources.json").is_file())
            self.assertEqual(factory.call_count, 1)


if __name__ == "__main__":
    unittest.main()
