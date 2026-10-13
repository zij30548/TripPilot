"""Bounded local retrieval contracts; no model, URL input or travel-state fields."""
from datetime import date
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

VenueId = Literal["museum_people", "museum_east", "natural"]
Id = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,59}$")]
Text = Annotated[str, StringConstraints(min_length=1, max_length=600)]
SOURCE_URLS = frozenset([
    "https://www.shanghaimuseum.cn/mu/frontend/pg/service/visit-west",
    "https://www.shanghaimuseum.cn/mu/frontend/pg/service/visit-east",
    "https://www.snhm.org.cn/cgfw/cgzx.htm",
    "https://www.snhm.org.cn/cgfw/ttyy.htm",
    "https://www.snhm.org.cn/jyhd/index.htm",
    "https://www.meet-in-shanghai.net/tc/news/the-peoples-square-pavilion-of-the-shanghai-museum-will-be-available-for-trial-visits-without-an-appointment-from-september-1-421453/",
])
SOURCE_VENUES = {"museum-west": "museum_people", "museum-east": "museum_east",
                 "natural-visit": "natural", "natural-school": "natural", "natural-education": "natural", "people-2025": "museum_people"}
SOURCE_PATHS = {"museum-west": "/mu/frontend/pg/service/visit-west", "museum-east": "/mu/frontend/pg/service/visit-east",
                "natural-visit": "/cgfw/cgzx.htm", "natural-school": "/cgfw/ttyy.htm", "natural-education": "/jyhd/index.htm",
                "people-2025": "/tc/news/the-peoples-square-pavilion-of-the-shanghai-museum-will-be-available-for-trial-visits-without-an-appointment-from-september-1-421453/"}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class KnowledgeRequest(Strict):
    venue_id: VenueId
    question: Annotated[str, StringConstraints(min_length=2, max_length=160)]
    limit: int = Field(default=5, ge=1, le=5)

    @field_validator("question")
    @classmethod
    def clean_question(cls, value: str) -> str:
        if value != value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("Question must be trimmed plain text")
        return value


class Venue(Strict):
    id: VenueId
    name: Text
    aliases: list[Text] = Field(min_length=1, max_length=5)


class Section(Strict):
    id: Id
    heading: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    excerpt: Annotated[str, StringConstraints(max_length=40)]
    summary: Annotated[str, StringConstraints(min_length=1, max_length=360)]


class Source(Strict):
    id: Id
    title: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    venue_id: VenueId
    url: Annotated[str, StringConstraints(max_length=500)]
    publisher: Text
    kind: Literal["standing", "temporary", "historical", "catalog"]
    fetched_at: AwareDatetime
    published_at: date | None
    updated_at: date | None
    effective_from: date | None
    effective_until: date | None
    content_version: Literal["2026-10-13.1"]
    applicability: Text
    reuse_basis: Text

    @field_validator("url")
    @classmethod
    def approved_url(cls, value: str) -> str:
        if value not in SOURCE_URLS or urlsplit(value).scheme != "https":
            raise ValueError("Unreviewed source")
        return value

    @model_validator(mode="after")
    def chronology(self) -> "Source":
        if SOURCE_VENUES.get(self.id) != self.venue_id or SOURCE_PATHS.get(self.id) != urlsplit(self.url).path:
            raise ValueError("Source/campus identity mismatch")
        if self.fetched_at.utcoffset().total_seconds() != 0:
            raise ValueError("Acquisition time must be UTC")
        if self.effective_from and self.effective_until and self.effective_until < self.effective_from:
            raise ValueError("Invalid applicability interval")
        if self.updated_at and self.published_at and self.updated_at < self.published_at:
            raise ValueError("Invalid publication interval")
        if any(d and d > self.fetched_at.date() for d in [self.published_at, self.updated_at]):
            raise ValueError("Publication later than acquisition")
        return self


class Document(Source):
    sections: list[Section] = Field(min_length=1, max_length=12)


class Corpus(Strict):
    schema_version: Literal["knowledge-corpus-v1"]
    corpus_version: Literal["2026-10-13.1"]
    venues: list[Venue] = Field(min_length=3, max_length=3)
    documents: list[Document] = Field(min_length=6, max_length=10)

    @model_validator(mode="after")
    def identities(self) -> "Corpus":
        if len({v.id for v in self.venues}) != 3 or len({d.id for d in self.documents}) != len(self.documents):
            raise ValueError("Duplicate identity")
        if len({d.url for d in self.documents}) != len(self.documents):
            raise ValueError("Duplicate document URL")
        if {d.venue_id for d in self.documents} != {v.id for v in self.venues}:
            raise ValueError("Missing campus")
        for doc in self.documents:
            if len({s.id for s in doc.sections}) != len(doc.sections):
                raise ValueError("Duplicate section")
        return self


class Match(Strict):
    chunk_id: Annotated[str, StringConstraints(pattern=r"^[a-z0-9-]+/[a-z0-9-]+$")]
    content_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    section: Section
    source: Source
    score: float = Field(gt=0, allow_inf_nan=False)
    matched_terms: list[Text] = Field(min_length=1, max_length=24)


class KnowledgeResponse(Strict):
    schema_version: Literal["knowledge-search-v1"] = "knowledge-search-v1"
    corpus_version: Literal["2026-10-13.1"]
    corpus_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    document_count: int = Field(ge=6, le=10)
    chunk_count: int = Field(ge=6, le=120)
    request: KnowledgeRequest
    venue: Venue
    status: Literal["matches", "no_match"]
    matches: list[Match] = Field(max_length=5)

    @model_validator(mode="after")
    def consistent(self) -> "KnowledgeResponse":
        if self.venue.id != self.request.venue_id or bool(self.matches) != (self.status == "matches") or len(self.matches) > self.request.limit:
            raise ValueError("Inconsistent result")
        if len({m.chunk_id for m in self.matches}) != len(self.matches):
            raise ValueError("Duplicate result")
        for match in self.matches:
            if match.source.venue_id != self.venue.id or match.chunk_id != f"{match.source.id}/{match.section.id}":
                raise ValueError("Cross-campus/reference error")
        return self
