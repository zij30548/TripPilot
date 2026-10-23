"""Reviewed multi-city retrieval contracts; independent of frozen knowledge-v1.

IDs and source membership come from the deployed reviewed registry, never from
arbitrary request URLs. This contract describes reference retrieval, not an
admission decision, generated answer or expansion of the planning contract.
"""
from __future__ import annotations

from datetime import date
import ipaddress
import re
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from core.chat_cities import city_for

Id = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,79}$")]
CityId = Annotated[str, StringConstraints(pattern=r"^[0-9]{6}$")]
Hash = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Version = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")]
Short = Annotated[str, StringConstraints(min_length=1, max_length=160)]
Long = Annotated[str, StringConstraints(min_length=1, max_length=700)]
Topic = Literal["opening", "reservation", "ticket", "children", "accessibility", "luggage", "transport", "safety", "services", "other"]
METHOD = "cjk-bigram-bm25-national-v1"
CONTROLLED_METHOD = "offline-controlled-retrieval-v1"
ApplicabilityStatus = Literal["no_visit_date", "historical_reference", "outside_declared_range", "within_declared_range_unverified", "current_applicability_unknown"]
NOTICES = {
    "historical_reference": "历史资料，仅用于了解当时规定，不代表当前入馆或购票条件。",
    "no_visit_date": "尚未指定参观日期；相关资料不等于当前规则或入场许可。",
    "outside_declared_range": "所选日期不在资料明确的适用日期范围内；不能据此判断当日可用。",
    "within_declared_range_unverified": "所选日期位于资料明确列出的日期范围内，但未联网核验当前状态；请向官方确认。",
    "current_applicability_unknown": "资料未给出完整适用日期，无法确认所选日期是否仍适用。",
}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("*", mode="after")
    @classmethod
    def plain_text(cls, value):
        if isinstance(value, str) and (value != value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value)):
            raise ValueError("Trimmed plain text required")
        return value


def approved_https_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if (len(value) > 500 or not value.isascii() or any(char.isspace() for char in value)
                or any(char in value for char in "\\#") or parsed.scheme != "https"
                or not value.startswith("https://") or parsed.port is not None
                or parsed.username is not None or parsed.password is not None
                or parsed.netloc != host or "." not in host or len(host) > 253
                or any(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) is None for label in host.split("."))
                or not parsed.path.startswith("/")):
            raise ValueError()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError()
        # A public official page may have a query. Exact full-URL membership is
        # still mandatory in ReviewedSources; no request supplies this value.
        return value
    except (ValueError, UnicodeError):
        raise ValueError("Reviewed public HTTPS source required") from None


class NationalCity(Strict):
    id: CityId
    name: Annotated[str, StringConstraints(min_length=1, max_length=80)]

    @model_validator(mode="after")
    def city_identity(self):
        city = city_for(self.id)
        if self.name not in {city.name, city.official_name}:
            raise ValueError("City identity mismatch")
        return self


class NationalVenue(Strict):
    id: Id
    city_id: CityId
    name: Short
    kind: Literal["museum", "amusement_park"]
    aliases: list[Short] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def aliases_unique(self):
        if (any(alias != alias.strip() or any(ord(char) < 32 for char in alias) for alias in self.aliases)
                or len({alias.casefold() for alias in self.aliases}) != len(self.aliases)):
            raise ValueError("Duplicate or invalid aliases")
        return self


class NationalSection(Strict):
    id: Id
    heading: Short
    excerpt: Annotated[str, StringConstraints(max_length=80)]
    summary: Long
    topics: list[Topic] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def topics_unique(self):
        if len(set(self.topics)) != len(self.topics):
            raise ValueError("Duplicate topic")
        return self


class NationalSource(Strict):
    id: Id
    title: Short
    venue_id: Id
    url: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    publisher: Short
    kind: Literal["standing", "temporary", "historical", "catalog"]
    fetched_at: AwareDatetime
    published_at: date | None
    updated_at: date | None
    effective_from: date | None
    effective_until: date | None
    content_version: Version
    reviewed_text_sha256: Hash
    applicability: Long
    reuse_basis: Long

    _url = field_validator("url")(approved_https_url)

    @model_validator(mode="after")
    def chronology(self):
        if self.fetched_at.utcoffset().total_seconds() != 0:
            raise ValueError("Acquisition time must be UTC")
        if self.effective_from and self.effective_until and self.effective_until < self.effective_from:
            raise ValueError("Invalid applicability interval")
        if self.updated_at and self.published_at and self.updated_at < self.published_at:
            raise ValueError("Invalid publication interval")
        if any(value and value > self.fetched_at.date() for value in (self.published_at, self.updated_at)):
            raise ValueError("Publication later than acquisition")
        return self


class NationalDocument(NationalSource):
    sections: list[NationalSection] = Field(min_length=1, max_length=32)


class NationalCorpus(Strict):
    schema_version: Literal["knowledge-corpus-v2"]
    corpus_version: Version
    cities: list[NationalCity] = Field(min_length=1, max_length=100)
    venues: list[NationalVenue] = Field(min_length=1, max_length=200)
    documents: list[NationalDocument] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def identities(self):
        city_ids, venue_ids = {city.id for city in self.cities}, {venue.id for venue in self.venues}
        if (len(city_ids) != len(self.cities) or len(venue_ids) != len(self.venues)
                or len({doc.id for doc in self.documents}) != len(self.documents)
                or {venue.city_id for venue in self.venues} != city_ids
                or {doc.venue_id for doc in self.documents} != venue_ids
                or len({(doc.venue_id, doc.url, doc.content_version) for doc in self.documents}) != len(self.documents)
                or sum(len(doc.sections) for doc in self.documents) > 3000):
            raise ValueError("Duplicate, unscoped or uncovered identity")
        for doc in self.documents:
            if len({section.id for section in doc.sections}) != len(doc.sections):
                raise ValueError("Duplicate section")
        return self


class ReviewedSource(Strict):
    document_id: Id
    venue_id: Id
    url: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    content_version: Version
    reviewed_text_sha256: Hash
    document_sha256: Hash

    _url = field_validator("url")(approved_https_url)


class ReviewedSources(Strict):
    schema_version: Literal["knowledge-sources-v2"]
    corpus_version: Version
    sources: list[ReviewedSource] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_sources(self):
        if len({source.document_id for source in self.sources}) != len(self.sources):
            raise ValueError("Duplicate reviewed source")
        return self


class NationalKnowledgeRequest(Strict):
    city_id: CityId
    venue_id: Id
    question: Annotated[str, StringConstraints(min_length=2, max_length=160)]
    limit: int = Field(default=5, ge=1, le=5)
    visit_date: date | None = None


SearchRequest = NationalKnowledgeRequest


class ApplicabilityCheck(Strict):
    status: ApplicabilityStatus
    notice: Annotated[str, StringConstraints(min_length=1, max_length=300)]


def applicability_check_for(source: NationalSource, visit_date: date | None) -> ApplicabilityCheck:
    """A declared interval is not a claim of current applicability or admission."""
    if source.kind == "historical":
        status = "historical_reference"
    elif visit_date is None:
        status = "no_visit_date"
    elif (source.effective_from and visit_date < source.effective_from
          or source.effective_until and visit_date > source.effective_until):
        status = "outside_declared_range"
    elif source.effective_from is not None and source.effective_until is not None:
        status = "within_declared_range_unverified"
    else:
        status = "current_applicability_unknown"
    return ApplicabilityCheck(status=status, notice=NOTICES[status])


class NationalMatch(Strict):
    chunk_id: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,79}/[a-z][a-z0-9_-]{0,79}$")]
    content_hash: Hash
    section: NationalSection
    source: NationalSource
    score: float = Field(gt=0, allow_inf_nan=False)
    matched_terms: list[Short] = Field(max_length=24)
    applicability_check: ApplicabilityCheck


class CatalogSource(Strict):
    id: Id
    venue_id: Id
    url: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    content_version: Version
    reviewed_text_sha256: Hash

    _url = field_validator("url")(approved_https_url)


class NationalCatalogResponse(Strict):
    schema_version: Literal["knowledge-catalog-v2"] = "knowledge-catalog-v2"
    corpus_version: Version
    corpus_hash: Hash
    method: Literal["cjk-bigram-bm25-national-v1"] = METHOD
    document_count: int = Field(ge=1, le=500)
    chunk_count: int = Field(ge=1, le=3000)
    cities: list[NationalCity] = Field(min_length=1, max_length=100)
    venues: list[NationalVenue] = Field(min_length=1, max_length=200)
    sources: list[CatalogSource] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def coherent(self):
        if (len({city.id for city in self.cities}) != len(self.cities)
                or len({venue.id for venue in self.venues}) != len(self.venues)
                or {venue.city_id for venue in self.venues} != {city.id for city in self.cities}
                or {source.venue_id for source in self.sources} != {venue.id for venue in self.venues}
                or len({source.id for source in self.sources}) != len(self.sources)
                or self.document_count != len(self.sources)):
            raise ValueError("Catalog identity mismatch")
        return self


class NationalKnowledgeResponse(Strict):
    schema_version: Literal["knowledge-search-v2"] = "knowledge-search-v2"
    corpus_version: Version
    corpus_hash: Hash
    method: Literal["cjk-bigram-bm25-national-v1", "offline-controlled-retrieval-v1"] = METHOD
    document_count: int = Field(ge=1, le=500)
    chunk_count: int = Field(ge=1, le=3000)
    request: NationalKnowledgeRequest
    city: NationalCity
    venue: NationalVenue
    status: Literal["matches", "no_match"]
    matches: list[NationalMatch] = Field(max_length=5)

    @model_validator(mode="after")
    def coherent(self):
        if (self.city.id != self.request.city_id or self.venue.city_id != self.city.id
                or self.venue.id != self.request.venue_id
                or bool(self.matches) != (self.status == "matches") or len(self.matches) > self.request.limit
                or len({match.chunk_id for match in self.matches}) != len(self.matches)):
            raise ValueError("Response scope mismatch")
        previous = float("inf")
        for match in self.matches:
            if (match.source.venue_id != self.venue.id
                    or match.chunk_id != f"{match.source.id}/{match.section.id}"
                    or match.score > previous or self.method == METHOD and not match.matched_terms
                    or match.applicability_check != applicability_check_for(match.source, self.request.visit_date)):
                raise ValueError("Response reference or ranking mismatch")
            previous = match.score
        return self


class OfflineVectorUnit(Strict):
    chunk_id: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,79}/[a-z][a-z0-9_-]{0,79}$")]
    unit_hash: Hash
    values: list[float] = Field(min_length=1, max_length=4096)


class OfflineVectorArtifact(Strict):
    schema_version: Literal["knowledge-offline-vectors-v1"]
    corpus_hash: Hash
    model: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    dimension: int = Field(ge=1, le=4096)
    units: list[OfflineVectorUnit] = Field(min_length=1, max_length=3000)
