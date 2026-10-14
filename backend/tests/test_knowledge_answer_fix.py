"""Offline contract/input regression, NOT evidence of model answer quality."""
import copy
import json
from pathlib import Path
import unittest

from app.integrations.qwen import MODEL, QwenError
from app.schemas.explanation import ExplanationUsage
from app.schemas.knowledge_answer import ModelAnswer
from app.services.knowledge_answer import SYSTEM, answer_payload, render_answer
from tests.test_knowledge_answer import fixtures


class EvidenceBoundaryTests(unittest.TestCase):
    def test_background_not_inherited_and_effective_date_not_discarded(self):
        _, r, _ = fixtures("摄影闪光灯自拍杆合作展览", "museum_people")
        p = answer_payload(r, MODEL)
        data = json.loads(p["messages"][1]["content"])
        west = next(d for d in data["documents"] if d["document_id"] == "museum-west")
        self.assertEqual(west["document_dates"]["notice_effective_from"], "2026-06-30")
        self.assertNotIn("2027-11-14", p["messages"][1]["content"])
        self.assertTrue(any("2027-11-14" in m.source.applicability for m in r.matches))
        for e, m in zip(data["evidence"], r.matches):
            self.assertEqual(set(e), {"evidence_id", "document_id", "heading", "curated_summary_sentences", "short_excerpt"})
            self.assertEqual("".join(e["curated_summary_sentences"]), m.section.summary)
            self.assertEqual(e["document_id"], m.source.id)
        self.assertEqual(len(data["documents"]), len({m.source.id for m in r.matches}))

    def test_explicit_snippet_dates_preserved_unlinked_background_not_copied(self):
        _, r, _ = fixtures("摄影", "museum_people")
        # Synthetic pair, not a corpus edit, a real fact, or a generated answer.
        for summary in ["合作展览按现场标志限制拍照。", "该合作展览仅在2030-03-01至2030-03-15禁止拍照。"]:
            controlled = r.model_copy(deep=True)
            controlled.matches[0].section.summary = summary
            controlled.matches[0].source.applicability = "另一个展览2031-01-01至2031-02-01。"
            data = json.loads(answer_payload(controlled, MODEL)["messages"][1]["content"])
            self.assertEqual("".join(data["evidence"][0]["curated_summary_sentences"]), summary)
            self.assertNotIn("2031-01-01", json.dumps(data))
        self.assertIn("只有片段明确关联的日期、人群、条件", SYSTEM)

    def test_historical_current_and_conflict_missing_pairs_not_hardcoded(self):
        # These expectations describe the output contract; no mock is claimed to
        # establish whether the real model chooses the correct member of a pair.
        pairs = [
            ("历史资料怎么规定", "answered", []),
            ("今天能否仍按历史资料入馆", "partial", ["current_applicability"]),
            ("两个预约渠道相互冲突", "partial", ["conflicting_materials"]),
            ("部分事项无收录", "partial", ["not_in_materials"]),
        ]
        for description, status, missing in pairs:
            output = {"answers": [{"text": "受控资料片段中的规定。", "evidence_ids": ["E1"]}], "missing": missing, "status": status}
            with self.subTest(description=description):
                self.assertEqual(ModelAnswer.model_validate(output).missing, missing)
        for query in ["历史资料怎么规定", "今天能否仍按历史资料入馆"]:
            _, r, _ = fixtures("预约", "museum_people")
            r.request.question = query
            self.assertEqual(answer_payload(r, MODEL)["messages"][0]["content"], SYSTEM)
        self.assertIn("current_applicability仅用于用户确实问", SYSTEM)
        self.assertIn("应标conflicting_materials而不是not_in_materials", SYSTEM)
        self.assertEqual(list(ModelAnswer.model_json_schema()["properties"]), ["answers", "missing", "status"])

    def test_saved_real_failures_rejected_or_explicitly_outside_structural_guard(self):
        rows = json.loads((Path(__file__).parent / "fixtures/knowledge_answer_prior_failures.json").read_text())["records"]
        for row in rows:
            q, r, _ = fixtures(row["question"], row["venue_id"])
            original = copy.deepcopy(row["model_output"])
            if row["case_id"] in ("a04", "a05"):
                # Includes E1 or [1] in prose. Whole answer fails, not stripped.
                with self.assertRaises(QwenError):
                    render_answer(row["model_output"], q, r, MODEL, ExplanationUsage())
            else:
                # a11: legal JSON and existing ID CANNOT prove date entailment.
                value = render_answer(row["model_output"], q, r, MODEL, ExplanationUsage())
                self.assertIn("2027年11月14日", value.answers[1].text)
            self.assertEqual(row["model_output"], original)

    def test_semantic_status_and_scope_are_not_silently_repaired(self):
        q, r, base = fixtures()
        base.update(status="partial", missing=["current_applicability"])
        value = render_answer(base, q, r, MODEL, ExplanationUsage())
        self.assertEqual(value.status, "partial")  # requires semantic review
        self.assertEqual(value.missing, ["current_applicability"])

    def test_internal_source_identity_in_prose_is_rejected_not_removed(self):
        q, r, content = fixtures()
        for identity in [r.matches[0].source.id, r.matches[0].chunk_id]:
            text = f"需预约（来源：{identity}）。"
            content["answers"][0]["text"] = text
            with self.assertRaises(QwenError): render_answer(content, q, r, MODEL, ExplanationUsage())
            self.assertEqual(content["answers"][0]["text"], text)
