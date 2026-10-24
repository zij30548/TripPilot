"""Artifact-only retrieval ablation; no model, network or production switch.

The lexical arm delegates to the unchanged BM25 implementation, the hybrid arm
delegates to its existing reciprocal-rank fusion seam. Pure cosine is an
independent arm, not a calibrated relevance or truth probability. The caller
must separately bind query-vector provenance to the exact request: mathematical
validation here cannot prove that a supplied vector encodes its question.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from core.schemas.knowledge_national import (
    METHOD, NationalCorpus, NationalKnowledgeRequest, NationalKnowledgeResponse, NationalMatch,
    OfflineVectorArtifact, OfflineVectorUnit,
)
from tooling.travel.knowledge_national import (
    MAX_SEARCH_BYTES, MAX_VECTOR_BYTES, NationalKnowledgeIndex,
    _bounded_model, _vector, applicability,
)

CANDIDATE_VERSION = "knowledge-semantic-candidate-v1"
SEMANTIC_METHOD = "offline-cosine-national-v1"
HYBRID_METHOD = "offline-rrf-national-v1"
Arm = Literal["lexical", "semantic", "hybrid"]
METHODS = {"lexical": METHOD, "semantic": SEMANTIC_METHOD, "hybrid": HYBRID_METHOD}
ModelName = Annotated[str, StringConstraints(min_length=1, max_length=100)]


class SemanticResponse(NationalKnowledgeResponse):
    """Separate offline schema; old strict consumers continue to reject it."""

    schema_version: Literal["knowledge-semantic-offline-v1"] = "knowledge-semantic-offline-v1"
    candidate_version: Literal["knowledge-semantic-candidate-v1"] = CANDIDATE_VERSION
    method: Literal["cjk-bigram-bm25-national-v1", "offline-cosine-national-v1", "offline-rrf-national-v1"]
    arm: Arm
    embedding_model: ModelName | None
    embedding_dimension: int | None = Field(ge=1, le=4096)

    @model_validator(mode="after")
    def arm_binding(self):
        if self.method != METHODS[self.arm]:
            raise ValueError("Offline arm and method mismatch")
        if self.arm == "lexical":
            if self.embedding_model is not None or self.embedding_dimension is not None:
                raise ValueError("Lexical arm does not consume an embedding")
        elif self.embedding_model is None or self.embedding_dimension is None:
            raise ValueError("Explicit offline embedding identity required")
        return self


def _safe_vector(values: Sequence[float], dimension: int) -> tuple[float, ...]:
    try:
        return _vector(values, dimension)
    except (TypeError, ArithmeticError):
        raise ValueError("Unsafe offline vector") from None


def _exact_fields(value, expected_type: type) -> None:
    # model_copy(update=...) can attach undeclared values that model_dump drops;
    # a JSON round-trip alone would silently accept that corrupted object.
    if (type(value) is not expected_type or value.__pydantic_extra__
            or set(value.__dict__) != set(expected_type.model_fields)):
        raise ValueError("Exact validated offline model required")


class OfflineSemanticIndex:
    """Own immutable-by-interface snapshots, never mutate or extend a live index.

    Retains the old 16 MiB vector artifact, 3,000 units, 4,096 dimensions, 20
    fusion candidates, five returned units and 128 KiB response limits. No
    threshold is tuned to evaluation questions: semantic uses cosine > 0,
    exactly as the old offline seam. This deliberately does not solve abstention.
    """

    def __init__(self, base: NationalKnowledgeIndex, document_vectors: OfflineVectorArtifact,
                 expected_model: str):
        if (type(base) is not NationalKnowledgeIndex or base.method != METHOD
                or base._vectors is not None or base._reranker is not None):
            raise ValueError("An unchanged lexical baseline index is required")
        if (type(expected_model) is not str or not 1 <= len(expected_model) <= 100
                or expected_model != expected_model.strip()
                or any(ord(char) < 32 or ord(char) == 127 for char in expected_model)):
            raise ValueError("Explicit bounded embedding model required")
        _exact_fields(document_vectors, OfflineVectorArtifact)
        if (type(document_vectors.dimension) is not int or not 1 <= document_vectors.dimension <= 4096
                or type(document_vectors.units) is not list or not 1 <= len(document_vectors.units) <= 3000):
            raise ValueError("Bounded offline document vectors required")
        for unit in document_vectors.units:
            _exact_fields(unit, OfflineVectorUnit)
            # Check before JSON serialization can coerce a mutated bool to 1.0.
            _safe_vector(unit.values, document_vectors.dimension)
        _bounded_model(document_vectors, MAX_VECTOR_BYTES)
        artifact = OfflineVectorArtifact.model_validate_json(document_vectors.model_dump_json())
        lexical = NationalKnowledgeIndex(base.corpus, base.manifest)
        if (base.hash != lexical.hash or base.unit_hashes != lexical.unit_hashes
                or base.cities != lexical.cities or base.venues != lexical.venues
                or base.chunks != lexical.chunks):
            raise ValueError("Baseline state does not match its reviewed corpus")
        # Reuse the old registry/model/unit/hash/dimension checks and RRF code.
        try:
            hybrid = NationalKnowledgeIndex(lexical.corpus, lexical.manifest,
                vectors=artifact, expected_embedding_model=expected_model)
            normalized = {unit.chunk_id: _safe_vector(unit.values, artifact.dimension)
                          for unit in artifact.units}
        except (TypeError, ArithmeticError):
            raise ValueError("Unsafe offline document vector") from None
        self.base = base
        self._lexical, self._hybrid = lexical, hybrid
        self._model, self._dimension = expected_model, artifact.dimension
        self._vectors = normalized
        self._matches = {match.chunk_id: match.model_copy(deep=True) for match, _ in lexical.chunks}
        self.hash = lexical.hash
        self.candidate_version = CANDIDATE_VERSION

    @property
    def corpus(self) -> NationalCorpus:
        return self._lexical.corpus.model_copy(deep=True)

    @property
    def embedding_model(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return self._dimension

    def search(self, request: NationalKnowledgeRequest, *, arm: Arm,
               query_vector: Sequence[float] | None = None,
               query_model: str | None = None) -> SemanticResponse:
        if type(arm) is not str or arm not in METHODS:
            raise ValueError("Explicit supported offline retrieval arm required")
        _exact_fields(request, NationalKnowledgeRequest)
        # Pydantic model_copy/update does not revalidate; do not trust it here.
        request = NationalKnowledgeRequest.model_validate_json(request.model_dump_json())
        self._lexical.validate_request(request)
        if arm == "lexical":
            if query_vector is not None or query_model is not None:
                raise ValueError("Lexical arm must not receive query vectors")
            original = self._lexical.search(request)
        else:
            if query_vector is None or query_model != self._model:
                raise ValueError("Matching explicit offline query vector/model required")
            query = _safe_vector(query_vector, self._dimension)
            if arm == "hybrid":
                # Pass the original values: the old seam performs its own single
                # normalization, preserving its exact scores and ordering.
                original = self._hybrid.search(request, query_vector=query_vector,
                                              query_model=query_model)
            else:
                hits: list[NationalMatch] = []
                for chunk_id, match in self._matches.items():
                    if match.source.venue_id != request.venue_id:
                        continue
                    score = sum(a * b for a, b in zip(query, self._vectors[chunk_id]))
                    if score > 0:
                        hits.append(match.model_copy(deep=True, update={"score": float(score),
                            "matched_terms": [], "applicability_check": applicability(match.source, request.visit_date)}))
                hits.sort(key=lambda match: (-match.score, match.chunk_id))
                original = NationalKnowledgeResponse(corpus_version=self._lexical.corpus.corpus_version,
                    corpus_hash=self.hash, method=self._hybrid.method,
                    document_count=len(self._lexical.corpus.documents), chunk_count=len(self._matches),
                    request=request, city=self._lexical.cities[request.city_id],
                    venue=self._lexical.venues[request.venue_id], status="matches" if hits else "no_match",
                    matches=hits[:request.limit])
        values = original.model_dump()
        values.update(schema_version="knowledge-semantic-offline-v1", candidate_version=CANDIDATE_VERSION,
            arm=arm, method=METHODS[arm], embedding_model=None if arm == "lexical" else self._model,
            embedding_dimension=None if arm == "lexical" else self._dimension)
        result = SemanticResponse.model_validate(values)
        _bounded_model(result, MAX_SEARCH_BYTES)
        return result.model_copy(deep=True)
