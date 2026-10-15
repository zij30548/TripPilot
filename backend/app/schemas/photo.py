"""Recognition is a clue, never a confirmed Place or an executable action."""
from typing import Annotated, Literal, Self
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from app.destinations import DestinationId
from app.schemas.explanation import ExplanationUsage

Short = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=80, strip_whitespace=True)]


class LandmarkClue(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: Short
    city: Annotated[str, StringConstraints(min_length=1, max_length=40, strip_whitespace=True)] | None
    basis: Annotated[str, StringConstraints(min_length=1, max_length=160, strip_whitespace=True)]


class PhotoSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["possible", "uncertain", "unrecognized"]
    clues: list[LandmarkClue] = Field(max_length=3)

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if (self.status == "unrecognized" and self.clues) or (self.status == "possible" and not self.clues):
            raise ValueError("inconsistent recognition")
        if len({(c.name, c.city) for c in self.clues}) != len(self.clues):
            raise ValueError("duplicate clue")
        return self


class PhotoResponse(PhotoSelection):
    contract_version: Literal["photo-landmark-v1"] = "photo-landmark-v1"
    request_id: str
    destination_id: DestinationId
    model: str
    usage: ExplanationUsage
