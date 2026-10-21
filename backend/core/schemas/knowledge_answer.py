"""Answer contract: model prose is bounded; provenance is owned by the server."""
from typing import Annotated, Literal
import re

from pydantic import Field, StringConstraints, field_validator, model_validator

from core.schemas.explanation import ExplanationUsage, Identifier
from core.schemas.knowledge import KnowledgeRequest, KnowledgeResponse, Strict, VenueId

PROMPT_VERSION = "knowledge-answer-6d2-fix-v3"
Hash = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
EvidenceId = Literal["E1", "E2", "E3", "E4", "E5"]
MissingCode = Literal["not_in_materials", "live_availability", "weather_not_collected", "current_applicability", "conflicting_materials", "other_venue"]


class AnswerRequest(Strict):
    venue_id: VenueId
    question: Annotated[str, StringConstraints(min_length=2, max_length=160)]
    request_id: Identifier
    corpus_version: Annotated[str, StringConstraints(min_length=1, max_length=40)]
    corpus_hash: Hash

    @field_validator("question")
    @classmethod
    def question_text(cls, value: str) -> str:
        KnowledgeRequest(venue_id="natural", question=value)
        return value


class AnswerItem(Strict):
    text: Annotated[str, StringConstraints(min_length=1, max_length=180)] = Field(
        description="简短事实正文，每个条件保留原主语，不合并不同人群要求。不写行内引用、来源ID或URL；引用仅放evidence_ids。")
    evidence_ids: list[EvidenceId] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def plain_and_unique(self) -> "AnswerItem":
        if (self.text != self.text.strip() or any(ord(c) < 32 for c in self.text)
                or re.search(r"https?://|www\.|[<>]", self.text, re.I)
                or re.search(r"\[\s*\d+(?:\s*[,，-]\s*\d+)*\s*\]|【\s*\d+\s*】", self.text)
                or re.search(r"E\d+", self.text)
                or len(set(self.evidence_ids)) != len(self.evidence_ids)):
            raise ValueError("Unsafe prose or duplicate evidence")
        return self


class ModelAnswer(Strict):
    answers: list[AnswerItem] = Field(max_length=3)
    missing: list[MissingCode] = Field(max_length=4, description=(
        "只列所问事项的未解决类别。即使已说明冲突，未能裁决仍必须含conflicting_materials。"
        "partial/insufficient必非空；answered必为空。历史规定已答全不加current_applicability。"))
    status: Literal["answered", "partial", "insufficient"] = Field(description=(
        "按答案与missing决定：全部所问事实有据且无冲突=answered；部分可答或冲突未解=partial；完全无据=insufficient。"))

    @model_validator(mode="after")
    def status_consistency(self) -> "ModelAnswer":
        if len(set(self.missing)) != len(self.missing) or len({a.text for a in self.answers}) != len(self.answers):
            raise ValueError("Duplicate output")
        if self.status == "insufficient":
            valid = not self.answers and bool(self.missing)
        elif self.status == "partial":
            valid = bool(self.answers) and bool(self.missing)
        else:
            valid = bool(self.answers) and not self.missing
        if not valid:
            raise ValueError("Status and answer disagree")
        return self


class Citation(Strict):
    evidence_id: EvidenceId
    chunk_id: str
    source_id: str
    url: str
    source_version: str
    content_hash: Hash


class AnswerResponse(ModelAnswer):
    schema_version: Literal["knowledge-answer-v1"] = "knowledge-answer-v1"
    prompt_version: Literal["knowledge-answer-6d2-fix-v3"] = PROMPT_VERSION
    request: AnswerRequest
    retrieval: KnowledgeResponse
    citations: list[Citation] = Field(max_length=5)
    origin: Literal["qwen", "local_no_match"]
    model: str | None
    usage: ExplanationUsage

    @model_validator(mode="after")
    def references(self) -> "AnswerResponse":
        r, q = self.retrieval, self.request
        if (r.request != KnowledgeRequest(venue_id=q.venue_id, question=q.question, limit=5)
                or r.corpus_version != q.corpus_version or r.corpus_hash != q.corpus_hash):
            raise ValueError("Stale or cross-scope retrieval")
        expected = {f"E{i+1}": m for i, m in enumerate(r.matches)}
        used = {eid for a in self.answers for eid in a.evidence_ids}
        if len({c.evidence_id for c in self.citations}) != len(self.citations) or used != {c.evidence_id for c in self.citations}:
            raise ValueError("Citation coverage mismatch")
        for c in self.citations:
            m = expected.get(c.evidence_id)
            if not m or (c.chunk_id, c.source_id, c.url, c.source_version, c.content_hash) != (
                    m.chunk_id, m.source.id, m.source.url, m.source.content_version, m.content_hash):
                raise ValueError("Unknown or wrong-version evidence")
        if self.origin == "local_no_match":
            if r.matches or self.status != "insufficient" or self.model is not None or self.usage.input_tokens is not None or self.usage.output_tokens is not None:
                raise ValueError("Invalid local result")
        elif not r.matches or self.model not in ("qwen3.7-flash", "qwen3.7-flash-2026-07-15"):
            raise ValueError("Invalid model origin")
        return self
