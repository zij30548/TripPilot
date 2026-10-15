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


class PhotoSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["possible", "uncertain", "unrecognized"]
    clues: list[LandmarkClue] = Field(max_length=1)

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if len(self.clues) != (1 if self.status == "possible" else 0):
            raise ValueError("inconsistent recognition")
        return self


class PhotoResponse(PhotoSelection):
    contract_version: Literal["photo-landmark-v2"] = "photo-landmark-v2"
    request_id: str
    destination_id: DestinationId
    model: str
    usage: ExplanationUsage
