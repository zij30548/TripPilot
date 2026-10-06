from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.place import ConfirmedPlace, Place


Interest = Literal["摄影", "Citywalk", "美食", "建筑", "博物馆", "购物"]


class CandidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    accommodation_place: ConfirmedPlace
    must_visit_places: list[ConfirmedPlace] = Field(default_factory=list)
    interests: list[Interest] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_must_visit_ids(self) -> Self:
        ids = [place.id for place in self.must_visit_places]
        if len(ids) != len(set(ids)):
            raise ValueError("必去地点不能包含重复的地点 ID。")
        return self


class RetrievalSource(BaseModel):
    interest: Interest | None
    keyword: str


class PlaceCandidate(BaseModel):
    place: Place
    role: Literal["must_visit", "optional"]
    retrieval_sources: list[RetrievalSource]


class CandidateQuery(BaseModel):
    keyword: str
    interest: Interest | None
    status: Literal["success", "failed", "timeout"]
    result_count: int = Field(ge=0, le=20)
    message: str | None = None


class CandidateResponse(BaseModel):
    status: Literal["success", "partial", "failed"]
    queried_at: datetime
    keywords: list[str]
    queries: list[CandidateQuery]
    candidates: list[PlaceCandidate]
