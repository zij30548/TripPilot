"""Isolated B v3 experiment: only missing semantics change; no API registration."""
from dataclasses import dataclass
import json

from tooling.providers.qwen import MAX_PROMPT_BYTES
from core.schemas.knowledge import KnowledgeResponse
from core.schemas.knowledge_answer import AnswerRequest
from tooling.travel.knowledge import fingerprint
from tools import knowledge_b_candidate as previous

VERSION = "knowledge-evidence-selection-v3-plus-question-gaps"
MODEL = "qwen3.7-plus-2026-05-26"
SYSTEM = previous.SYSTEM + """
missing的判定以用户实际请求的结论为边界，而不是以资料之间有没有差异为边界。逐项确认：用户问了什么、所选单元能回答什么、还剩哪个用户确实要求但不能回答的事项。通用的行前核实提醒、谨慎措辞和来源局限本身，不新增用户未问的事项。
若用户只要求分别转述或对照不同时期、不同适用背景下的规定，分别保留来源及其条件即可；可以说明差异，不必裁决哪个今天有效。仅有年代、适用背景或条文差异，不自动产生conflicting_materials，也不自动产生current_applicability。
若用户还明确要求确定今天实际应采用哪条规则，比较部分有依据就保留；对资料无法确认的当前适用判断列current_applicability。不能仅因新年份、排序或获取时间而裁决。
若用户要确定一个实际做法，资料对该事项给出了不能同时成立、又无法由已有适用条件消解的说法，应保留双方依据并列conflicting_materials；不能把这个未裁决问题当成已经答全。不要把所有差异一概当历史差异，也不要把任何比较问题一概清空missing。
提交前核对每个missing是否对应一个真实未答的用户事项；没有该事项就不添加该缺口，有该事项则不遗漏或换成别的类别。不输出这一核对过程，不改变规定的两个列表结构。
"""


@dataclass(frozen=True)
class BoundQuestion:
    original: previous.BoundQuestion
    version: str = VERSION

    @property
    def identity(self) -> str:
        return fingerprint([self.version, self.original.identity])


def bind(request: AnswerRequest, retrieval: KnowledgeResponse) -> BoundQuestion:
    return BoundQuestion(previous.bind(request, retrieval))


def payload_for(bound: BoundQuestion) -> dict:
    if bound.version != VERSION:
        raise ValueError("Candidate version changed")
    payload = previous.payload_for(bound.original)
    payload["model"] = MODEL
    payload["messages"][0]["content"] = SYSTEM
    # The same JSON Schema, evidence order, user message and generation parameters.
    if len(json.dumps(payload, ensure_ascii=False).encode()) > MAX_PROMPT_BYTES:
        raise ValueError("Existing prompt capacity exceeded")
    return payload


def render(content: object, captured: BoundQuestion, current: BoundQuestion) -> dict:
    if captured != current or captured.version != VERSION:
        raise ValueError("Stale candidate/request identity")
    result = previous.render(content, captured.original, current.original)
    # Only experimental envelope metadata changes. Text, citations, status and
    # missing projection are delegated unchanged, including semantically bad gaps.
    return {**result, "version": VERSION, "binding_hash": current.identity}
