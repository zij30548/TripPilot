"""Reviewed multi-city local retrieval; no fetch, model, embedding or database.

The live baseline is Chinese BM25. Explicit offline vector/rerank injection is
available for controlled comparisons only; absent/mismatched inputs fail, never
download a model or silently fall back. Scores rank references, not truth.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from functools import lru_cache
import json
import math
from pathlib import Path
from typing import Any
import unicodedata

from core.chat_cities import city_for
from core.schemas.knowledge_national import (
    CatalogSource, CONTROLLED_METHOD, METHOD, NOTICES,
    NationalCatalogResponse, NationalCorpus, NationalKnowledgeRequest,
    NationalKnowledgeResponse, NationalMatch, NationalSource,
    OfflineVectorArtifact, ReviewedSources,
    applicability_check_for,
)
from tooling.providers.qwen import strict_json
from tooling.travel.knowledge import fingerprint, tokens

DATA = Path(__file__).resolve().parents[2] / "knowledge"
CORPUS_PATH = DATA / "national-corpus-v2.json"
MANIFEST_PATH = DATA / "national-sources-v2.json"
MAX_CORPUS_BYTES = 1024 * 1024
MAX_CATALOG_BYTES = 1024 * 1024
MAX_SEARCH_BYTES = 128 * 1024
MAX_VECTOR_BYTES = 16 * 1024 * 1024
MAX_RETRIEVAL_CANDIDATES = 20
Reranker = Callable[[NationalKnowledgeRequest, tuple[NationalMatch, ...]], Sequence[str]]

applicability = applicability_check_for


def _bounded_model(value: Any, maximum: int) -> None:
    raw = json.dumps(value.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    if len(raw) > maximum:
        raise ValueError("National retrieval output exceeds safe capacity")


def validate_registry(corpus: NationalCorpus, manifest: ReviewedSources) -> None:
    if corpus.corpus_version != manifest.corpus_version or len(corpus.documents) != len(manifest.sources):
        raise ValueError("Reviewed registry version or coverage mismatch")
    registry = {source.document_id: source for source in manifest.sources}
    for doc in corpus.documents:
        source = registry.get(doc.id)
        if (source is None or source.venue_id != doc.venue_id or source.url != doc.url
                or source.content_version != doc.content_version
                or source.reviewed_text_sha256 != doc.reviewed_text_sha256
                or source.document_sha256 != fingerprint(doc.model_dump(mode="json"))):
            raise ValueError("Unreviewed source, content or identity")


def scope_tokens(text: str, venue, city) -> list[str]:
    """Identity is a hard filter, not evidence that a topic was found.

    Remove only the explicitly selected venue/city names and reviewed aliases
    before the unchanged v1 lexical tokenizer. This prevents a venue name by
    itself from retrieving arbitrary policy text in a nationwide catalog.
    """
    normalized = unicodedata.normalize("NFKC", text).casefold()
    names = {venue.name, *venue.aliases, city.name, *city_for(city.id).aliases}
    names = {unicodedata.normalize("NFKC", value).casefold() for value in names if len(value) >= 2}
    for name in sorted(names, key=lambda value: (-len(value), value)):
        normalized = normalized.replace(name, " ")
    return tokens(normalized)


def _vector(values: Sequence[float], dimension: int) -> tuple[float, ...]:
    if (not isinstance(values, (list, tuple)) or len(values) != dimension
            or any(type(value) not in (int, float) or not math.isfinite(value) for value in values)):
        raise ValueError("Invalid offline vector")
    norm = math.sqrt(sum(value * value for value in values))
    if not math.isfinite(norm) or norm == 0:
        raise ValueError("Zero or unsafe offline vector")
    return tuple(value / norm for value in values)


class NationalKnowledgeIndex:
    def __init__(self, corpus: NationalCorpus, manifest: ReviewedSources, *,
                 vectors: OfflineVectorArtifact | None = None,
                 expected_embedding_model: str | None = None,
                 reranker: Reranker | None = None, reranker_version: str | None = None):
        # Keep independently owned validated objects. A caller editing its input
        # cannot mutate this published index or its already checked registry.
        corpus = NationalCorpus.model_validate_json(corpus.model_dump_json())
        manifest = ReviewedSources.model_validate_json(manifest.model_dump_json())
        _bounded_model(corpus, MAX_CORPUS_BYTES)
        _bounded_model(manifest, MAX_CORPUS_BYTES)
        validate_registry(corpus, manifest)
        self.corpus, self.manifest = corpus, manifest
        self.hash = fingerprint(corpus.model_dump(mode="json"))
        self.cities = {city.id: city for city in corpus.cities}
        self.venues = {venue.id: venue for venue in corpus.venues}
        self.chunks: list[tuple[NationalMatch, Counter[str]]] = []
        self.unit_hashes: dict[str, str] = {}
        for doc in corpus.documents:
            venue = self.venues[doc.venue_id]
            city = self.cities[venue.city_id]
            source_values = doc.model_dump(mode="json", exclude={"sections"})
            source = NationalSource.model_validate_json(json.dumps(source_values, ensure_ascii=False))
            digest = fingerprint(doc.model_dump(mode="json"))
            for section in doc.sections:
                chunk_id = f"{doc.id}/{section.id}"
                match = NationalMatch(chunk_id=chunk_id, content_hash=digest, section=section,
                    source=source, score=1.0, matched_terms=["init"],
                    applicability_check=applicability(source, None))
                self.chunks.append((match, Counter(scope_tokens(section.heading + " " + section.summary + " " + section.excerpt, venue, city))))
                self.unit_hashes[chunk_id] = fingerprint({"source_hash": digest, "section": section.model_dump(mode="json")})
        self._vectors: dict[str, tuple[float, ...]] | None = None
        self._vector_model, self._dimension = expected_embedding_model, None
        if vectors is not None:
            vectors = OfflineVectorArtifact.model_validate_json(vectors.model_dump_json())
            _bounded_model(vectors, MAX_VECTOR_BYTES)
            if (not expected_embedding_model or vectors.model != expected_embedding_model
                    or vectors.corpus_hash != self.hash or len(vectors.units) != len(self.unit_hashes)
                    or len({unit.chunk_id for unit in vectors.units}) != len(vectors.units)
                    or {unit.chunk_id: unit.unit_hash for unit in vectors.units} != self.unit_hashes):
                raise ValueError("Offline vectors do not match corpus, units or model")
            self._dimension = vectors.dimension
            self._vectors = {unit.chunk_id: _vector(unit.values, vectors.dimension) for unit in vectors.units}
        elif expected_embedding_model is not None:
            raise ValueError("An explicitly bound offline vector artifact is required")
        if (reranker is None) != (reranker_version is None) or reranker is not None and (
                not callable(reranker) or not isinstance(reranker_version, str)
                or not 1 <= len(reranker_version) <= 100 or reranker_version != reranker_version.strip()):
            raise ValueError("Explicit versioned offline reranker required")
        self._reranker, self.reranker_version = reranker, reranker_version
        self.method = CONTROLLED_METHOD if vectors is not None or reranker is not None else METHOD

    def catalog(self) -> NationalCatalogResponse:
        # The production catalog advertises only the live lexical method. An
        # offline injected index is not a production endpoint configuration.
        if self.method != METHOD:
            raise ValueError("Offline comparison index has no production catalog")
        result = NationalCatalogResponse(corpus_version=self.corpus.corpus_version,
            corpus_hash=self.hash, document_count=len(self.corpus.documents), chunk_count=len(self.chunks),
            cities=self.corpus.cities, venues=self.corpus.venues,
            sources=[CatalogSource.model_validate({key: getattr(source, key) for key in
                ("id", "venue_id", "url", "content_version", "reviewed_text_sha256")})
                for source in self.corpus.documents])
        _bounded_model(result, MAX_CATALOG_BYTES)
        return result.model_copy(deep=True)

    def validate_request(self, request: NationalKnowledgeRequest) -> None:
        if (request.city_id not in self.cities or request.venue_id not in self.venues
                or self.venues[request.venue_id].city_id != request.city_id):
            raise ValueError("Select an explicitly covered city and matching venue")

    def _lexical(self, request: NationalKnowledgeRequest) -> list[NationalMatch]:
        rows = [(match, terms) for match, terms in self.chunks if match.source.venue_id == request.venue_id]
        query = set(scope_tokens(request.question, self.venues[request.venue_id], self.cities[request.city_id]))
        average = sum(sum(terms.values()) for _, terms in rows) / len(rows)
        frequencies = Counter(term for _, terms in rows for term in terms)
        hits = []
        if not average:
            return hits
        for match, terms in rows:
            common = sorted(query & terms.keys())
            if not common:
                continue
            score = sum(math.log(1 + (len(rows) - frequencies[term] + .5) / (frequencies[term] + .5)) *
                        terms[term] * 2.2 / (terms[term] + 1.2 * (.25 + .75 * sum(terms.values()) / average))
                        for term in common)
            hits.append(match.model_copy(update={"score": round(score, 6), "matched_terms": common[:24],
                "applicability_check": applicability(match.source, request.visit_date)}))
        hits.sort(key=lambda match: (-match.score, match.chunk_id))
        return hits

    def search(self, request: NationalKnowledgeRequest, *, query_vector: Sequence[float] | None = None,
               query_model: str | None = None) -> NationalKnowledgeResponse:
        if not isinstance(request, NationalKnowledgeRequest):
            raise ValueError("Validated national retrieval request required")
        self.validate_request(request)
        hits = self._lexical(request)
        if self._vectors is not None:
            if query_vector is None or query_model != self._vector_model:
                raise ValueError("Matching explicit offline query vector/model required")
            query = _vector(query_vector, self._dimension)
            vector_hits = []
            by_id = {match.chunk_id: match for match, _ in self.chunks if match.source.venue_id == request.venue_id}
            for chunk_id, match in by_id.items():
                cosine = sum(a * b for a, b in zip(query, self._vectors[chunk_id]))
                if cosine > 0:
                    vector_hits.append((chunk_id, cosine))
            vector_hits.sort(key=lambda item: (-item[1], item[0]))
            # Explicit reciprocal rank fusion, not a probability or confidence.
            weights: Counter[str] = Counter()
            lexical_terms = {match.chunk_id: match.matched_terms for match in hits}
            for ranking in ([match.chunk_id for match in hits[:MAX_RETRIEVAL_CANDIDATES]],
                            [identifier for identifier, _ in vector_hits[:MAX_RETRIEVAL_CANDIDATES]]):
                for rank, chunk_id in enumerate(ranking, 1):
                    weights[chunk_id] += 1 / (60 + rank)
            hits = [by_id[chunk_id].model_copy(update={"score": float(score),
                "matched_terms": lexical_terms.get(chunk_id, []),
                "applicability_check": applicability(by_id[chunk_id].source, request.visit_date)})
                for chunk_id, score in weights.items()]
            hits.sort(key=lambda match: (-match.score, match.chunk_id))
        elif query_vector is not None or query_model is not None:
            raise ValueError("No offline vector artifact configured")
        if self._reranker is not None and hits:
            candidates = hits[:MAX_RETRIEVAL_CANDIDATES]
            # The trusted caller injects a pure controlled test double. No
            # transport/client/model construction exists in this module.
            order = self._reranker(request.model_copy(deep=True), tuple(match.model_copy(deep=True) for match in candidates))
            ids = {match.chunk_id for match in candidates}
            if (not isinstance(order, (list, tuple)) or len(order) != len(candidates)
                    or any(type(item) is not str for item in order) or len(set(order)) != len(order) or set(order) != ids):
                raise ValueError("Offline reranker must return a complete valid permutation")
            lookup = {match.chunk_id: match for match in candidates}
            hits = [lookup[chunk_id].model_copy(update={"score": float(len(order) - rank)})
                    for rank, chunk_id in enumerate(order)]
        response = NationalKnowledgeResponse(corpus_version=self.corpus.corpus_version, corpus_hash=self.hash,
            method=self.method, document_count=len(self.corpus.documents), chunk_count=len(self.chunks),
            request=request.model_copy(deep=True), city=self.cities[request.city_id], venue=self.venues[request.venue_id],
            status="matches" if hits else "no_match", matches=hits[:request.limit])
        _bounded_model(response, MAX_SEARCH_BYTES)
        return response.model_copy(deep=True)


def _load(path: Path, maximum: int) -> bytes:
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    if not raw or len(raw) > maximum:
        raise ValueError("National knowledge artifact exceeds reviewed capacity")
    strict_json(raw)
    return raw


def build_national_index(corpus_path: Path = CORPUS_PATH, manifest_path: Path = MANIFEST_PATH,
                         **offline_injections: Any) -> NationalKnowledgeIndex:
    corpus = NationalCorpus.model_validate_json(_load(corpus_path, MAX_CORPUS_BYTES))
    manifest = ReviewedSources.model_validate_json(_load(manifest_path, MAX_CORPUS_BYTES))
    return NationalKnowledgeIndex(corpus, manifest, **offline_injections)


build_index = build_national_index


@lru_cache(maxsize=1)
def get_index() -> NationalKnowledgeIndex:
    # No environment-configurable vector/model URLs or implicit model download.
    return build_national_index()
