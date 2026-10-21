"""Frozen offline trajectories, NOT a real-model semantic quality score.

The selection replay below deliberately supplies expected values as a controlled
model substitute. It checks the schema/provenance/state reducer only. The user
messages and review labels remain available for separately authorised evaluation;
no production module imports this manifest or dispatches by a case/question ID.
"""
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from core.schemas.chat import Intent, Selection, Source
from tooling.travel.chat import (
    FIXED_LIMITATION, apply_selection, missing_fields, model_payload,
    ready_to_confirm, render_reply,
)


MANIFEST = Path(__file__).resolve().parents[1] / "evaluations/chat1-trajectories-v1.json"
FROZEN_SHA256 = "15a35359647ef880b35440570ce6e9ab2b6cc2dc209da37843fa4b880c9ab89c"
CATEGORIES = {"complete", "missing", "correction", "ambiguity", "scope", "lifecycle"}


class ChatOfflineTrajectoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.raw = MANIFEST.read_bytes()
        cls.manifest = json.loads(cls.raw)

    def setUp(self) -> None:
        # These tests never exercise a live supplier. No budget is authorised.
        self.send_guard = patch("httpx.AsyncClient.send", side_effect=AssertionError("offline-only"))
        self.send_guard.start()
        self.addCleanup(self.send_guard.stop)

    def test_frozen_identity_split_and_zero_authorization(self) -> None:
        self.assertEqual(sha256(self.raw).hexdigest(), FROZEN_SHA256)
        self.assertEqual(self.manifest["schema_version"], "chat1-trajectories-v1")
        self.assertEqual(self.manifest["real_model_calls_authorized"], 0)
        self.assertEqual(self.manifest["production_dependencies"], [])
        cases = self.manifest["cases"]
        self.assertEqual([case["id"] for case in cases], [f"c{i:02}" for i in range(1, 25)])
        self.assertEqual(Counter(case["split"] for case in cases), {"development": 12, "validation": 12})
        self.assertEqual({case["category"] for case in cases}, CATEGORIES)
        for category in CATEGORIES:
            with self.subTest(category=category):
                self.assertEqual(Counter(case["split"] for case in cases if case["category"] == category),
                                 {"development": 2, "validation": 2})

    def test_every_trajectory_has_multiple_user_turns_and_explicit_boundaries(self) -> None:
        fields = set(self.manifest["fields"])
        for case in self.manifest["cases"]:
            with self.subTest(case=case["id"]):
                user_steps = [step for step in case["steps"] if "user_text" in step]
                self.assertGreaterEqual(len(user_steps), 2)
                self.assertTrue(case["expected_outcome"])
                self.assertGreaterEqual(len(case["forbidden_outcomes"]), 3)
                self.assertEqual(len(case["forbidden_outcomes"]), len(set(case["forbidden_outcomes"])))
                for step in case["steps"]:
                    self.assertEqual(int("event" in step) + int("user_text" in step), 1)
                    if "event" in step:
                        self.assertTrue(step["expected_state"])
                        continue
                    self.assertTrue(step["user_text"].strip())
                    self.assertLessEqual(len(step["user_text"]), 2000)
                    self.assertLessEqual(set(step["expected_fields"]), fields)
                    self.assertLessEqual(set(step.get("must_remain_unknown", [])), fields)
                    self.assertLessEqual(set(step.get("preserve_fields", [])), fields)
                    for forbidden_key in ("poi_id", "coordinates", "owner_id", "confirmed_place"):
                        self.assertNotIn(forbidden_key, step["expected_fields"])

    def test_controlled_expected_selection_replay_is_not_semantic_model_evaluation(self) -> None:
        supported = set(Intent.model_fields)
        ignored = set(self.manifest["non_executable_review_fields"])
        self.assertEqual(ignored, {"day_start", "day_end"})
        self.assertIn("unimplemented preferences", self.manifest["non_executable_review_note"])
        # c23 explicitly switches accounts: the new identity starts with no state.
        for case in self.manifest["cases"]:
            intent = Intent()
            for step_index, step in enumerate(case["steps"]):
                with self.subTest(case=case["id"], step=step_index):
                    if "event" in step:
                        if step["event"] == "logout_account_a_login_account_b_then_deliver_a_response":
                            intent = Intent()
                        continue
                    previous = intent.model_dump()
                    values = dict(previous)
                    for key, value in step["expected_fields"].items():
                        if key in supported:
                            values[key] = str(value) if key == "budget_cny" and value is not None else value
                        else:
                            self.assertIn(key, ignored)
                    proposed = Intent.model_validate(values)
                    changed = [key for key in supported if previous[key] != getattr(proposed, key)]
                    # A controlled exact quote is provenance only; it cannot prove
                    # that these expected fields semantically follow from the quote.
                    selection = Selection(requirements=proposed, issues=[], sources=[
                        Source(field=key, quote=step["user_text"]) for key in changed
                    ])
                    intent, sources = apply_selection(intent, selection, step["user_text"])
                    self.assertEqual({source.field for source in sources}, set(changed))
                    for key in step.get("must_remain_unknown", []):
                        if key in supported:
                            self.assertIsNone(getattr(intent, key))
                    for key in step.get("preserve_fields", []):
                        if key in supported:
                            self.assertEqual(getattr(intent, key), previous[key])
                    if step.get("preserve_other_fields"):
                        for key in supported - set(step["expected_fields"]):
                            self.assertEqual(getattr(intent, key), previous[key])

    def test_labels_ids_and_expectations_do_not_enter_model_payload(self) -> None:
        now = datetime(2026, 10, 20, 8, 0, tzinfo=timezone.utc)
        for case in self.manifest["cases"]:
            with self.subTest(case=case["id"]):
                first = next(step for step in case["steps"] if "user_text" in step)
                payload = model_payload({"requirements": Intent().model_dump(), "messages": []},
                                        first["user_text"], model="offline-test-model", now=now)
                visible = json.loads(payload["messages"][1]["content"])
                self.assertEqual(visible["latest_user_text"], first["user_text"])
                self.assertEqual(visible["current_intent"], Intent().model_dump())
                self.assertEqual(visible["conversation"], [])
                self.assertEqual(visible["today"], "2026-10-20")
                self.assertEqual(set(visible), {"today", "current_intent", "conversation",
                                               "latest_user_text", "supported_destinations"})
                for forbidden in ("expected_fields", "expected_outcome", "forbidden_outcomes",
                                  "review_note", "expected_state", "real_model_calls_authorized"):
                    self.assertNotIn(forbidden, json.dumps(payload, ensure_ascii=False))

    def test_changed_field_requires_unique_current_user_provenance(self) -> None:
        before = Intent(destination_id="hangzhou", travelers=2)
        after = Intent.model_validate(before.model_dump() | {"travelers": 4})
        user = "人数改成4个人。"
        invalid = [
            {"requirements": after.model_dump(), "sources": [], "issues": []},
            {"requirements": after.model_dump(), "sources": [{"field": "travelers", "quote": "旧消息"}], "issues": []},
            {"requirements": after.model_dump(), "sources": [{"field": "destination_id", "quote": user}], "issues": []},
            {"requirements": after.model_dump(), "sources": [{"field": "travelers", "quote": user}] * 2, "issues": []},
            {"requirements": after.model_dump(), "sources": [{"field": "travelers", "quote": user}], "issues": ["ambiguous", "ambiguous"]},
        ]
        for raw in invalid:
            with self.subTest(selection=raw):
                with self.assertRaises(ValueError):
                    apply_selection(before, Selection.model_validate(raw), user)
                self.assertEqual(before.travelers, 2)
        result, _ = apply_selection(before, Selection(requirements=after,
            sources=[Source(field="travelers", quote="4个人")], issues=[]), user)
        self.assertEqual(result.travelers, 4)

    def test_missing_any_model_intent_field_is_rejected_not_defaulted(self) -> None:
        previous = Intent(destination_id="hangzhou", travelers=2)
        full = previous.model_dump() | {"travelers": 4}
        for field in Intent.model_fields:
            with self.subTest(missing=field):
                partial = dict(full)
                partial.pop(field)
                selection = Selection.model_validate({"requirements": partial,
                    "sources": [{"field": "travelers", "quote": "4个人"}], "issues": []})
                with self.assertRaisesRegex(ValueError, "complete_intent_required"):
                    apply_selection(previous, selection, "改成4个人")
                self.assertEqual(previous.travelers, 2)

    def test_confirmability_and_fixed_limits_do_not_claim_scheduled_pois(self) -> None:
        self.assertEqual(set(missing_fields(Intent())), {"destination_id", "start_date", "end_date",
                         "travelers", "budget_cny", "accommodation_hint"})
        complete = Intent(destination_id="hangzhou", start_date="2026-11-01", end_date="2026-11-03",
                          travelers=2, budget_cny="3000", accommodation_hint="西湖附近",
                          required_place_hints=["西湖"], preferences=["轻松一点"])
        self.assertTrue(ready_to_confirm(complete, []))
        self.assertTrue(ready_to_confirm(complete, ["unimplemented_preference"]))
        for issue in ("ambiguous", "unsupported_scope", "unsupported_action"):
            with self.subTest(issue=issue):
                self.assertFalse(ready_to_confirm(complete, [issue]))
                self.assertIn(FIXED_LIMITATION, render_reply(complete, [issue]))
        reply = render_reply(complete, ["unimplemented_preference"])
        self.assertIn("尚未转成可执行排程约束", reply)
        self.assertIn("尚未通过真实POI搜索确认", reply)
        self.assertIn("不是已生成行程", reply)
        self.assertIn("开始日期", render_reply(Intent(), []))


if __name__ == "__main__":
    unittest.main()
