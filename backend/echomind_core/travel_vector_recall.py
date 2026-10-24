"""Reviewed complete units over original KnowledgeBase/Chroma vector recall.

Only explicit prepare imports the trusted selected venue into private storage.
Original 500-character chunks remain unchanged; a hit is mapped back to its
complete checked unit before any source is displayed. No implicit seeding,
fallback, model download, answer generation or index migration is performed.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date
import hashlib
import json
import math
from typing import Any, Awaitable, Callable

from core.business_usage import UsageError
from core.llm_utils import RuntimeFailure, bounded_json, strict_json, validate_arguments
from core.schemas.knowledge_national import NationalSource, applicability_check_for
from echomind_core.execution import ExecutionError, execution_gate
from echomind_core.runtime import CandidateResources
from echomind_core.travel_capabilities import ReviewedKnowledgePort, _exact_model_objects
from echomind_core.upstream.tooling.knowledge_base import KnowledgeBase
from tooling.travel.knowledge import fingerprint
from tooling.travel.knowledge_national import NationalKnowledgeIndex

VERSION = "travel-reviewed-chroma-recall-v1"
_RAW_FIELDS = frozenset({"title", "content", "score", "chunk", "document_id", "version"})
_SCHEMA = {"type": "object", "properties": {
    "query": {"type": "string", "minLength": 2, "maxLength": 160},
    "top_k": {"type": "integer", "minimum": 1, "maximum": 5}},
    "required": ["query", "top_k"], "additionalProperties": False}


class ReviewedVectorRecall:
    def __init__(self, resources: CandidateResources, reviewed: ReviewedKnowledgePort,
                 city_id: str, venue_id: str, visit_date: date | None = None):
        if (type(resources) is not CandidateResources or type(reviewed) is not ReviewedKnowledgePort
                or visit_date is not None and type(visit_date) is not date):
            raise ExecutionError("travel_vector_configuration")
        arguments = {"city_id": city_id, "venue_id": venue_id, "question": "资料范围核对",
                     "limit": 5, "visit_date": visit_date.isoformat() if visit_date else None}
        checked = reviewed._request(arguments)
        _exact_model_objects(reviewed._index.corpus)
        _exact_model_objects(reviewed._index.manifest)
        # Independently validate the original reviewed registry. This constructs
        # no external index and performs no BM25 query or different recall.
        validated = NationalKnowledgeIndex(reviewed._index.corpus, reviewed._index.manifest)
        canonical = {match.chunk_id: match.model_dump(mode="json") for match, _ in validated.chunks}
        if validated.hash != reviewed.corpus_hash or canonical != reviewed._matches:
            raise ExecutionError("travel_vector_source")
        self.resources, self.reviewed = resources, reviewed
        self.city_id, self.venue_id, self.visit_date = checked.city_id, checked.venue_id, visit_date
        self.corpus_hash, self.corpus_version = reviewed.corpus_hash, reviewed.corpus_version
        self._manifest_hash = fingerprint(reviewed._index.manifest.model_dump(mode="json"))
        self._source_rows = strict_json(bounded_json(canonical, 1024 * 1024))
        units = [row for row in canonical.values() if row["source"]["venue_id"] == venue_id]
        if not 1 <= len(units) <= 32:
            raise ExecutionError("travel_vector_preparation_limit")
        documents, mappings, all_chunks = [], {}, []
        for unit in units:
            identifier = "trv:" + self.corpus_hash + ":" + hashlib.sha256(unit["chunk_id"].encode()).hexdigest()[:32]
            title, content = unit["source"]["title"], unit["section"]["summary"]
            chunks = KnowledgeBase._chunk_text(None, content, 500)
            # Same explicit identity and version transformations as the
            # preserved add_documents. Actual stored metadata is checked below.
            stored_id = hashlib.sha256(json.dumps(["explicit", identifier], ensure_ascii=True).encode()).hexdigest()
            if stored_id in mappings or not chunks:
                raise ExecutionError("travel_vector_source")
            documents.append({"document_id": identifier, "title": title, "content": content})
            mappings[stored_id] = {"unit": deepcopy(unit), "title": title, "chunks": tuple(chunks),
                                  "version": hashlib.sha256(content.encode()).hexdigest()}
            all_chunks.extend(chunks)
        if len(set(all_chunks)) > 10:
            raise ExecutionError("travel_vector_preparation_limit")
        self._documents = bounded_json(documents, 65_536)
        self._mappings, self._chunk_count = mappings, len(all_chunks)
        self._state, self._prepared_scope = "unprepared", None

    def _check_source(self) -> None:
        try:
            _exact_model_objects(self.reviewed._index.corpus)
            _exact_model_objects(self.reviewed._index.manifest)
            for match, _ in self.reviewed._index.chunks:
                _exact_model_objects(match)
            self.reviewed._request({"city_id": self.city_id, "venue_id": self.venue_id,
                "question": "资料范围核对", "visit_date": self.visit_date.isoformat() if self.visit_date else None})
            if (self.reviewed.corpus_hash != self.corpus_hash or self.reviewed.corpus_version != self.corpus_version
                    or fingerprint(self.reviewed._index.manifest.model_dump(mode="json")) != self._manifest_hash
                    or self.reviewed._matches != self._source_rows
                    or {match.chunk_id: match.model_dump(mode="json") for match, _ in self.reviewed._index.chunks} != self._source_rows):
                raise ValueError()
        except (RuntimeFailure, ValueError, TypeError, KeyError):
            raise ExecutionError("travel_vector_source") from None

    async def prepare(self) -> dict[str, Any]:
        gate = self.resources._gate()
        self._check_source()
        if self._state == "ready":
            if gate.scope != self._prepared_scope:
                raise ExecutionError("travel_vector_context_changed")
            return self.summary()
        if self._state == "preparing":
            raise ExecutionError("travel_vector_preparing")
        if self._state != "unprepared":
            raise ExecutionError("travel_vector_preparation_uncertain")
        self._state = "preparing"
        try:
            count = await self.resources.import_documents(strict_json(self._documents))
            gate.check()
            self._check_source()
            if type(count) is not int or count != self._chunk_count:
                raise ExecutionError("travel_vector_preparation_failed")
            self._prepared_scope, self._state = gate.scope, "ready"
            return self.summary()
        except BaseException:
            # A partially written or uncertain private index is not retried.
            self._state = "uncertain"
            raise

    def summary(self) -> dict[str, Any]:
        return {"version": VERSION, "state": self._state, "corpus_hash": self.corpus_hash,
                "city_id": self.city_id, "venue_id": self.venue_id,
                "unit_count": len(self._mappings), "original_chunk_count": self._chunk_count,
                "recall": "original-knowledgebase-chroma", "retrieval_only": True,
                "quality_acceptance": "not_assessed", "auto_prepare": False}

    def bound_recall(self) -> Callable[[dict[str, Any], dict[str, Any]], Awaitable[list[dict[str, Any]]]]:
        async def checked_recall(arguments: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
            try:
                validate_arguments(arguments, _SCHEMA)
            except RuntimeFailure:
                raise ExecutionError("travel_vector_arguments") from None
            gate = execution_gate()
            if self.resources._gate() is not gate:
                raise ExecutionError("travel_vector_context_changed")
            expected = {"owner_id": gate.scope.owner_id, "conversation_id": gate.scope.conversation_id,
                        "session_id": gate.scope.session_id, "input_version": gate.scope.input_version}
            if (type(context) is not dict or set(context) != set(expected) or context != expected
                    or type(context["input_version"]) is not int
                    or any(type(context[key]) is not str for key in ("owner_id", "conversation_id", "session_id"))):
                raise ExecutionError("travel_vector_context_changed")
            if self._state != "ready":
                raise ExecutionError("travel_vector_unprepared")
            if gate.scope != self._prepared_scope:
                raise ExecutionError("travel_vector_context_changed")
            self._check_source()
            rows = await self.resources.search(arguments["query"], arguments["top_k"])
            gate.check()
            self._check_source()
            if type(rows) is not list or len(rows) > arguments["top_k"]:
                raise ExecutionError("travel_vector_result")
            result, seen = [], set()
            for row in rows:
                if type(row) is not dict or set(row) != _RAW_FIELDS:
                    raise ExecutionError("travel_vector_result")
                entry = self._mappings.get(row["document_id"]) if type(row["document_id"]) is str else None
                if (entry is None or row["title"] != entry["title"] or row["version"] != entry["version"]
                        or type(row["chunk"]) is not int or not 0 <= row["chunk"] < len(entry["chunks"])
                        or row["content"] != entry["chunks"][row["chunk"]]
                        or type(row["score"]) not in (int, float) or not math.isfinite(row["score"])
                        or not -1.001 <= row["score"] <= 1.001):
                    raise ExecutionError("travel_vector_result")
                unit = deepcopy(entry["unit"])
                if unit["chunk_id"] in seen:
                    continue
                seen.add(unit["chunk_id"])
                source = NationalSource.model_validate_json(json.dumps(unit["source"]))
                unit["applicability_check"] = applicability_check_for(source, self.visit_date).model_dump(mode="json")
                # Full summary/conditions and exact source, never the split hit.
                result.append({"document_id": unit["source"]["id"], "chunk": unit["chunk_id"],
                    "title": unit["source"]["title"], "content": unit["section"]["summary"],
                    **unit, "score": row["score"], "matched_terms": [], "retrieval_only": True,
                    "corpus_hash": self.corpus_hash, "corpus_version": self.corpus_version,
                    "recall_method": VERSION, "vector_similarity_is_confidence": False})
            return strict_json(bounded_json(result, 32_000))

        async def recall(arguments: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
            gate = execution_gate()
            try:
                return await checked_recall(arguments, context)
            except UsageError as error:
                gate._usage_error = error
                gate.fail(error.code)
                raise
            except RuntimeFailure as error:
                gate.fail(error.code)
                raise
            except asyncio.CancelledError:
                gate.fail("cancelled")
                raise
            except Exception:
                gate.fail("travel_vector_unavailable")
                raise ExecutionError("travel_vector_unavailable") from None
        return recall
