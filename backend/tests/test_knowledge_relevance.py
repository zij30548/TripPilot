"""Synthetic candidate checks, not new real-corpus tuning or semantic grades."""
from __future__ import annotations

import copy
from datetime import date
import json
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from core.schemas.knowledge_national import CONTROLLED_METHOD, NationalKnowledgeRequest
from tests.test_knowledge_national import corpus_values, reviewed
from tooling.travel.knowledge_national import NationalKnowledgeIndex
from tooling.travel.knowledge_relevance import (
    CANDIDATE_VERSION, METHOD, RelevanceIndex, RelevanceResponse, _positive_view,
)


def synthetic_index() -> NationalKnowledgeIndex:
    raw = corpus_values()
    prototype = raw["documents"][0]
    rows = [
        ("entry", "门票使用", "入园门票仅限本人并一次有效，离园后再次入园需重新购票。"),
        ("opening", "开放时间", "周一闭馆，节假日除外；周二至周日9:00至17:00，16:00停止入馆。"),
        ("wheel", "轮椅服务", "轮椅可在一楼租用，押金200元；须有效证件，仅限馆内使用。"),
        ("food", "照料婴儿", "家庭中心提供照料婴儿或喂奶的空间。未收录餐厅菜价。"),
        ("luggage", "行李寄存", "大件行李在地下一层寄存处存放。没有收录寄存费用或柜位余量。"),
        ("photos", "摄影规则", "禁止闪光灯和三脚架，但允许拍照的展览可按现场标志拍摄。"),
        ("weather", "天气与设施", "下雨或大风时户外设施可能暂停，天气改善且安全检测后才恢复。没有实时天气预报。"),
        ("course", "课程目标受众", "课程面向儿童和成人，主题包括古代艺术。未收录报名渠道和参加条件。"),
        ("campus", "馆区地址", "甲馆区地址为青松路11号；乙馆区在白云路22号。这不是课程报名说明。"),
        ("tour", "馆内讲解", "专职讲解可联系一楼前台，电话为010-12345678。未收录讲解实时名额。"),
        ("permit", "外部讲解许可", "馆外机构开展讲解须获得许可并提前三日申请。未说明个人旅游团适用。"),
        ("exhibit", "展览主题", "一层展览陈列以传统园林为主题。"),
        ("named", "陶瓷珍品", "展品有青花瓷器和釉里红陶瓷。"),
    ]
    raw["documents"] = []
    for identifier, heading, summary in rows:
        item = copy.deepcopy(prototype)
        item.update(id=identifier, url=f"https://museum.example.test/{identifier}")
        item["sections"][0].update(heading=heading, summary=summary, excerpt="", topics=["other"])
        raw["documents"].append(item)
    # Keep the independently reviewed Beijing identities covered, but never let
    # this city's otherwise identical policy bleed into the selected Shanghai.
    originals = corpus_values()["documents"]
    raw["documents"].extend(copy.deepcopy(originals[2:]))
    return NationalKnowledgeIndex(*reviewed(raw))


def request(question: str, **values) -> NationalKnowledgeRequest:
    return NationalKnowledgeRequest(city_id=values.pop("city_id", "310000"),
        venue_id=values.pop("venue_id", "shanghai-museum"), question=question, **values)


def ids(response) -> set[str]:
    return {match.chunk_id for match in response.matches}


class RelevanceTests(unittest.TestCase):
    def setUp(self):
        self.base = synthetic_index()
        self.index = RelevanceIndex(self.base)

    def test_independent_version_and_original_question_are_not_rewritten(self):
        original = request("出园吃饭以后，用原票还能再进吗？", visit_date=date(2026, 11, 1))
        result = self.index.search(original)
        self.assertEqual((result.schema_version, result.method, result.candidate_version),
                         ("knowledge-relevance-offline-v1", METHOD, CANDIDATE_VERSION))
        self.assertEqual(result.request, original)
        self.assertEqual(result.corpus_hash, self.base.hash)
        self.assertEqual(result.request.question, "出园吃饭以后，用原票还能再进吗？")
        self.assertIn("entry/entry", ids(result))
        self.assertEqual(RelevanceResponse.model_validate_json(result.model_dump_json()), result)

    def test_reentry_synonyms_are_general_and_not_venue_or_question_rules(self):
        for question in ("离园以后同一张票还能接着用吗", "出去转一圈，拿原来那张票回来能再进园吗", "旧票可重复使用吗"):
            with self.subTest(question=question):
                self.assertIn("entry/entry", ids(self.index.search(request(question))))
        self.assertNotIn("entry/entry", ids(self.index.search(request("明天从酒店回来怎么走"))))

    def test_explicit_objects_separate_equal_price_and_booking_relations(self):
        self.assertIn("wheel/entry", ids(self.index.search(request("轮椅押金多少钱"))))
        for question in ("租车押金多少钱", "酒店可以怎么预约", "机票现在价格多少", "签证申请要交什么材料"):
            with self.subTest(question=question):
                self.assertEqual(self.index.search(request(question)).matches, [])

    def test_missing_fee_or_real_time_notes_are_not_positive_support(self):
        for question in ("寄存行李收费多少", "讲解还有几个名额", "明天气温多少度", "课程报名渠道是什么"):
            with self.subTest(question=question):
                self.assertEqual(self.index.search(request(question)).matches, [])
        self.assertIn("luggage/entry", ids(self.index.search(request("大箱子在哪寄存"))))
        self.assertIn("tour/entry", ids(self.index.search(request("馆内讲解怎么联系"))))

    def test_ordinary_negative_policy_and_complete_condition_text_survive(self):
        result = self.index.search(request("展览拍照可以带三脚架吗"))
        match = next(match for match in result.matches if match.chunk_id == "photos/entry")
        self.assertEqual(match.section.summary, "禁止闪光灯和三脚架，但允许拍照的展览可按现场标志拍摄。")
        times = self.index.search(request("周一开放吗"))
        opening = next(match for match in times.matches if match.chunk_id == "opening/entry")
        self.assertIn("节假日除外", opening.section.summary)
        self.assertIn("16:00停止入馆", opening.section.summary)
        for policy in ("不能将食品带入展厅，但饮水除外", "无需另行预约或付费", "禁止闪光灯，摄影器材另有例外"):
            self.assertEqual(policy.replace("，", ","), _positive_view(policy))

    def test_weather_operations_are_not_weather_forecasts(self):
        self.assertIn("weather/entry", ids(self.index.search(request("下雨后户外项目会照常吗"))))
        for question in ("明天的降雨概率多少", "明天会不会下雨", "天气预报现在多少度"):
            self.assertEqual(self.index.search(request(question)).matches, [], question)

    def test_course_audience_location_and_registration_are_distinct(self):
        self.assertIn("course/entry", ids(self.index.search(request("课程适合哪些受众"))))
        self.assertIn("campus/entry", ids(self.index.search(request("甲馆区地址在哪"))))
        for question in ("儿童课程怎么报名", "乙馆区亲子课几人一班"):
            self.assertEqual(self.index.search(request(question)).matches, [], question)

    def test_mix_known_and_unknown_keeps_only_supported_units_without_answer_claim(self):
        result = self.index.search(request("轮椅押金多少；另外酒店有泳池吗"))
        self.assertIn("wheel/entry", ids(result))
        self.assertNotIn("permit/entry", ids(result))
        self.assertEqual(result.abstention_reason, "matched_evidence")
        self.assertNotIn("answer", result.model_dump())
        self.assertNotIn("fully_answered", result.model_dump())

    def test_weak_terms_scope_words_and_name_only_matches_abstain(self):
        for question in ("个人旅游", "受控馆个人规定", "青松路附近酒店", "上海受控博物馆", "有什么相关资料"):
            self.assertEqual(self.index.search(request(question)).matches, [], question)

    def test_unknown_topic_fallback_needs_two_substantive_anchors(self):
        self.assertIn("named/entry", ids(self.index.search(request("陶瓷青花瓷器釉里红"))))
        self.assertEqual(self.index.search(request("青花" )).matches, [])

    def test_reference_date_and_source_bytes_project_unchanged(self):
        original = {match.chunk_id: match for match, _ in self.base.chunks}
        result = self.index.search(request("周一何时闭馆", visit_date=date(2027, 1, 1)))
        for match in result.matches:
            base = original[match.chunk_id]
            self.assertEqual(match.source, base.source)
            self.assertEqual(match.section, base.section)
            self.assertEqual(match.content_hash, base.content_hash)
            self.assertEqual(match.applicability_check.status, "current_applicability_unknown")

    def test_mutable_caller_and_response_cannot_change_candidate_corpus(self):
        before = self.index.search(request("轮椅押金多少"))
        self.base.corpus.documents[0].sections[0].summary = "调用方修改"
        self.base.chunks[0][0].section.summary = "调用方改结果"
        copy_base = self.index.base
        copy_base.corpus.documents[0].sections[0].summary = "修改导出基础索引"
        exported = self.index.corpus
        exported.documents[0].sections[0].summary = "修改导出语料"
        before.matches[0].section.summary = "修改查询结果"
        after = self.index.search(request("轮椅押金多少"))
        self.assertNotEqual(after.matches[0].section.summary, "修改查询结果")
        self.assertNotEqual(self.index.corpus.documents[0].sections[0].summary, "调用方修改")

    def test_request_validation_scope_and_strict_diagnostics(self):
        with self.assertRaises(ValueError):
            self.index.search(request("儿童预约", venue_id="beijing-museum"))
        with self.assertRaises(ValueError):
            self.index.search({"question": "预约"})
        response = self.index.search(request("不存在的太空船引擎"))
        self.assertEqual(response.abstention_reason, "no_supported_evidence")
        for values in ({"method": "production"}, {"candidate_version": "changed"},
                       {"abstention_reason": "matched_evidence"}, {"rejected_count": 3001},
                       {"query_topics": ["invented"]}, {"unknown": "unsafe"}):
            raw = response.model_dump(mode="json"); raw.update(values)
            with self.assertRaises(ValidationError):
                RelevanceResponse.model_validate_json(json.dumps(raw))

    def test_ranking_is_deterministic_limited_and_no_controlled_base_admitted(self):
        self.assertEqual(self.index.search(request("周一开放")), self.index.search(request("周一开放")))
        self.assertLessEqual(len(self.index.search(request("预约讲解", limit=1)).matches), 1)
        self.base.method = CONTROLLED_METHOD
        with self.assertRaises(ValueError):
            RelevanceIndex(self.base)

    def test_no_network_model_or_business_side_effects(self):
        with patch("httpx.AsyncClient.request", side_effect=AssertionError("No network")), \
             patch("httpx.Client.request", side_effect=AssertionError("No network")):
            result = self.index.search(request("轮椅押金多少"))
        self.assertIn("wheel/entry", ids(result))


if __name__ == "__main__":
    unittest.main()
