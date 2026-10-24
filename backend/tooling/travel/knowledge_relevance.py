"""Offline, deterministic relevance candidate; never a semantic-model claim.

This module is deliberately absent from the production API. It keeps the
reviewed corpus, original question and complete displayed evidence unchanged.
Only a separate scoring view receives finite topic/synonym features and a
conservative relevance gate. Passing that gate is not answer entailment.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
import math
import re
from typing import Literal
import unicodedata

from pydantic import Field, model_validator

from core.schemas.knowledge_national import (
    METHOD as BASE_METHOD, NationalCorpus, NationalKnowledgeRequest,
    NationalKnowledgeResponse, NationalMatch, ReviewedSources,
)
from tooling.travel.knowledge_national import (
    MAX_SEARCH_BYTES, NationalKnowledgeIndex, applicability, scope_tokens,
)

METHOD = "topic-gated-lexical-candidate-v1"
CANDIDATE_VERSION = "knowledge-relevance-v1"

# General objects, not venue names, question IDs, answers or admission rules.
# Explicit objects constrain a shared relation: car deposits are not wheelchair
# deposits, and hotel reservations are not museum reservations.
OBJECTS: dict[str, tuple[str, ...]] = {
    "admission": ("门票", "入园", "入馆", "入场", "参观", "进馆", "进园"),
    "education": ("课程", "课件", "亲子课", "茶艺课", "教育活动", "探索体验", "探索空间"),
    "guide": ("讲解", "导览", "导游", "讲文物", "研学"),
    "wheelchair": ("轮椅",),
    "stroller": ("婴儿车", "婴儿童车", "童车", "推车"),
    "feeding": ("喂奶", "哺乳", "母婴", "照料婴儿", "家庭中心"),
    "luggage": ("行李", "寄存", "寄物", "背包", "包裹", "箱子", "存包", "放包"),
    "photo": ("拍照", "摄影", "闪光灯", "自拍杆", "三脚架", "脚架", "录像"),
    "facilities": ("游乐设施", "骑乘", "户外设施", "室外项目", "设备", "表演", "演出"),
    "lodging": ("酒店", "客房", "民宿", "住宿", "公寓", "房型", "泳池"),
    "car_rental": ("租车", "汽车租赁", "自驾租赁"),
    "air_travel": ("机票", "飞机", "航班", "航空"),
    "rail": ("高铁", "火车", "铁路", "地铁", "末班车"),
    "visa": ("签证", "护照", "领事", "出境"),
    "food": ("餐馆", "饭店", "餐厅", "菜单", "无麸质", "订餐"),
    "retail": ("茶叶", "商品", "购物", "花店", "送花"),
    "library": ("图书馆", "借书证", "借阅"),
    "exhibition": ("展览", "展厅", "陈列", "展陈", "看展", "展品"),
}

# Relation aliases describe the thing being asked, not its answer. Broad object
# words (children, people, tourism) alone intentionally cannot authorize a hit.
RELATIONS: dict[str, tuple[str, ...]] = {
    "opening": ("开放时间", "营业时间", "开门", "关门", "闭馆", "闭园", "停止入场", "停止入馆", "最晚入", "开到", "几点开", "几点关", "几点进", "周一", "周二", "周三", "周四", "周五", "周六", "周日"),
    "booking": ("预约", "预订", "预定", "订票", "购票", "放票", "抢票", "报名", "怎么约", "在哪约"),
    "ticket_reuse": ("一次有效", "再次入园", "再入园", "重新入园", "重新进园", "重新购票", "重入", "出园", "离园", "重复使用", "同一张票", "原来的票", "原来那张票", "原票", "旧票"),
    "price": ("费用", "收费", "价格", "票价", "免票", "免费", "押金", "多少钱", "租金", "几块钱"),
    "accompaniment": ("陪同", "监护", "陪伴", "陪着", "大人陪", "要人陪", "有人陪"),
    "photo_rules": ("拍照", "摄影", "闪光灯", "自拍杆", "三脚架", "脚架", "录像"),
    "location": ("地址", "在哪", "哪里", "位置", "几层", "哪层", "哪一层", "哪个门", "哪个入口", "从哪", "一楼", "二楼", "地下一层", "负一楼"),
    "contact": ("联系", "电话", "拨打", "咨询", "公众号", "前台", "问询"),
    "permission": ("许可", "允许", "准入", "申请", "审批", "材料"),
    "capacity": ("承载量", "接待能力", "人数上限", "最大接待", "瞬时", "几人一班", "每班人数"),
    "education_info": ("受众", "面向", "适合", "年龄", "课程分类", "活动分类"),
    "exhibition_info": ("陈列", "展览主题", "展陈", "展示", "展什么", "有什么展"),
}

Topic = Literal[
    "opening", "booking", "ticket_reuse", "price", "accompaniment", "photo_rules",
    "location", "contact", "permission", "capacity", "education_info", "exhibition_info",
    "weather_operations", "weather_forecast", "live_availability", "object_support", "lexical",
]
_WEATHER = ("下雨", "雨雪", "雷电", "大风", "天气", "气温", "降雨", "暴雨")
_OPERATIONS = ("停运", "暂停", "运营", "调整", "关闭", "照常", "设施", "项目", "表演", "演出", "户外", "室外", "重开", "开放")
_FORECAST = ("概率", "预报", "多少度", "会下雨", "下不下雨", "会不会下雨", "冷不冷", "几点下雨", "天气怎么样")
_AVAILABILITY = ("剩余", "余票", "约满", "还有几个", "还剩", "实时人数", "实时名额", "即时余量", "排队多久")
_LIMITATION = re.compile(r"未(?:收录|说明|标明|给出|联网)|没有(?:收录|实时|现行)|不等于|不代表|不能据此|不是[^。；;]{0,45}(?:保证|标准|承诺|实时|说明)")
_SENTENCES = re.compile(r"[。；;！？!?]+")
_QUERY_CLAUSES = re.compile(r"[。；;！？!?，,、]+")
_WEAK = {"个人", "人群", "旅游", "馆区", "场馆", "地点", "规定", "说明", "材料", "资料", "现在", "今天", "明天", "我们", "他们", "这个", "那个", "如何", "哪些", "是否", "里面", "外面", "自己", "一下", "相关", "情况", "当日", "当天", "具体", "当前", "普通", "问题", "规则", "服务"}


def _normal(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _contains(text: str, values: tuple[str, ...]) -> bool:
    return any(value in text for value in values)


def _positive_view(text: str) -> str:
    """A scoring view only. Do not erase policy negation or edit the evidence.

    A whole sentence with an editorial limitation is conservatively omitted.
    This can lose useful cues in mixed sentences; it cannot turn a missing
    price into positive price evidence by matching the word in its disclaimer.
    """
    return " ".join(sentence for sentence in _SENTENCES.split(_normal(text))
                    if sentence.strip() and not _LIMITATION.search(sentence))


@dataclass(frozen=True)
class _Aspect:
    relations: frozenset[str]
    objects: frozenset[str]


def _profile(text: str) -> _Aspect:
    text = _normal(text)
    objects = {name for name, aliases in OBJECTS.items() if _contains(text, aliases)}
    relations = {name for name, aliases in RELATIONS.items() if _contains(text, aliases)}
    if _contains(text, _WEATHER):
        if _contains(text, _FORECAST):
            relations.add("weather_forecast")
        elif _contains(text, _OPERATIONS):
            relations.add("weather_operations")
    if _contains(text, _AVAILABILITY):
        relations.add("live_availability")
    # A return plus a ticket/entry object is a compositional re-entry clue; the
    # same words without that object do not turn a trip home into ticket use.
    if _contains(text, ("回来", "回去", "再进", "接着用", "重复用")) and _contains(text, ("票", "入园", "进园", "出园", "离园")):
        relations.add("ticket_reuse")
    return _Aspect(frozenset(relations), frozenset(objects))


def _query_aspects(question: str) -> tuple[_Aspect, ...]:
    result = []
    last_objects: frozenset[str] = frozenset()
    for clause in _QUERY_CLAUSES.split(question):
        if not clause.strip():
            continue
        item = _profile(clause)
        objects = item.objects
        if not objects and item.relations:
            objects = last_objects
        if item.objects:
            last_objects = item.objects
        # Specific requested relationships take precedence over context words.
        relations = set(item.relations)
        if relations & {"price", "location", "contact", "permission", "capacity", "ticket_reuse", "weather_forecast", "live_availability"}:
            relations -= {"education_info", "exhibition_info"}
        result.append(_Aspect(frozenset(relations), objects))
    return tuple(result)


def _substantive(terms: list[str]) -> set[str]:
    return {term for term in terms if term not in _WEAK and len(term) >= 2
            and not term.isdecimal()}


def _object_compatible(query: frozenset[str], evidence: frozenset[str]) -> bool:
    # Admission/exhibition are general context if a more specific object exists.
    required = query - {"admission", "exhibition"}
    return not required or bool(required & evidence)


class RelevanceResponse(NationalKnowledgeResponse):
    schema_version: Literal["knowledge-relevance-offline-v1"] = "knowledge-relevance-offline-v1"
    method: Literal["topic-gated-lexical-candidate-v1"] = METHOD
    candidate_version: Literal["knowledge-relevance-v1"] = CANDIDATE_VERSION
    query_topics: list[Topic] = Field(max_length=17)
    rejected_count: int = Field(ge=0, le=3000)
    abstention_reason: Literal["no_supported_evidence", "matched_evidence"]

    @model_validator(mode="after")
    def candidate_coherent(self):
        if (len(set(self.query_topics)) != len(self.query_topics)
                or self.abstention_reason != ("matched_evidence" if self.matches else "no_supported_evidence")):
            raise ValueError("Offline relevance diagnostic mismatch")
        return self


class RelevanceIndex:
    method = METHOD
    candidate_version = CANDIDATE_VERSION

    def __init__(self, base: NationalKnowledgeIndex):
        if not isinstance(base, NationalKnowledgeIndex) or base.method != BASE_METHOD:
            raise ValueError("An unchanged lexical base is required")
        # Revalidate independently; neither caller mutations nor response edits
        # can alter the already-bound source corpus used by this candidate.
        self._base = NationalKnowledgeIndex(base.corpus, base.manifest)
        self._corpus = self._base.corpus.model_copy(deep=True)
        self._manifest = self._base.manifest.model_copy(deep=True)
        self.hash = self._base.hash
        self._units = []
        for match, _ in self._base.chunks:
            venue = self._base.venues[match.source.venue_id]
            city = self._base.cities[venue.city_id]
            heading = _positive_view(match.section.heading)
            excerpt = _positive_view(match.section.excerpt)
            positive = " ".join((heading, excerpt, _positive_view(match.section.summary)))
            profile = _profile(positive)
            terms = Counter(scope_tokens(positive, venue, city))
            terms.update("relation:" + name for name in profile.relations)
            terms.update("object:" + name for name in profile.objects)
            anchors = _substantive(scope_tokens(heading + " " + excerpt, venue, city))
            self._units.append((match.model_copy(deep=True), terms, profile, anchors))

    @property
    def base(self) -> NationalKnowledgeIndex:
        return NationalKnowledgeIndex(self._corpus, self._manifest)

    @property
    def corpus(self) -> NationalCorpus:
        return self._corpus.model_copy(deep=True)

    def search(self, request: NationalKnowledgeRequest) -> RelevanceResponse:
        if not isinstance(request, NationalKnowledgeRequest):
            raise ValueError("A validated retrieval request is required")
        request = NationalKnowledgeRequest.model_validate_json(request.model_dump_json())
        self._base.validate_request(request)
        venue, city = self._base.venues[request.venue_id], self._base.cities[request.city_id]
        aspects = _query_aspects(request.question)
        query_terms = set(scope_tokens(request.question, venue, city))
        informative = _substantive(list(query_terms))
        for aspect in aspects:
            query_terms.update("relation:" + name for name in aspect.relations)
            query_terms.update("object:" + name for name in aspect.objects)
        query_topics = sorted({name for aspect in aspects for name in aspect.relations})
        strong = any(aspect.relations or aspect.objects for aspect in aspects)
        rows = [row for row in self._units if row[0].source.venue_id == request.venue_id]
        average = sum(sum(terms.values()) for _, terms, _, _ in rows) / len(rows)
        frequencies = Counter(term for _, terms, _, _ in rows for term in terms)
        hits: list[NationalMatch] = []
        rejected = 0
        for match, terms, profile, anchors in rows:
            supported = any(
                _object_compatible(aspect.objects, profile.objects)
                and (bool(aspect.relations & profile.relations) if aspect.relations
                     else bool(aspect.objects - {"admission", "exhibition"})
                     and bool(aspect.objects & profile.objects))
                for aspect in aspects)
            if not strong:
                overlap = informative & _substantive(list(terms))
                supported = len(overlap) >= 2 and bool(overlap & anchors)
            common = sorted(query_terms & terms.keys())
            if not supported or not common or not average:
                rejected += 1
                continue
            score = sum(math.log(1 + (len(rows) - frequencies[term] + .5) / (frequencies[term] + .5)) *
                        terms[term] * 2.2 / (terms[term] + 1.2 * (.25 + .75 * sum(terms.values()) / average))
                        for term in common)
            hits.append(match.model_copy(update={"score": round(score, 6), "matched_terms": common[:24],
                "applicability_check": applicability(match.source, request.visit_date)}))
        hits.sort(key=lambda match: (-match.score, match.chunk_id))
        response = RelevanceResponse(corpus_version=self._corpus.corpus_version, corpus_hash=self.hash,
            document_count=len(self._corpus.documents), chunk_count=len(self._units), request=request,
            city=city.model_copy(deep=True), venue=venue.model_copy(deep=True),
            status="matches" if hits else "no_match", matches=hits[:request.limit],
            query_topics=query_topics, rejected_count=rejected,
            abstention_reason="matched_evidence" if hits else "no_supported_evidence")
        if len(json.dumps(response.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")).encode()) > MAX_SEARCH_BYTES:
            raise ValueError("Offline relevance output exceeds safe capacity")
        return response.model_copy(deep=True)
