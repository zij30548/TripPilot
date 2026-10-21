"""Preview-only media delivery; never a Place or a saved-trip field."""
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

RequestId = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_-]{1,80}$", strict=True)]


class VideoFrame(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    index: int = Field(ge=1, le=6)
    target_seconds: float = Field(gt=0, lt=30)
    width: int = Field(gt=0, le=1280)
    height: int = Field(gt=0, le=1280)
    jpeg_base64: str = Field(min_length=4, max_length=699052, pattern=r"^[A-Za-z0-9+/]+={0,2}$")


class VideoResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    contract_version: Literal["video-frames-v1"] = "video-frames-v1"
    request_id: RequestId
    duration_seconds: float = Field(gt=0, le=30)
    width: int = Field(gt=0, le=1920)
    height: int = Field(gt=0, le=1920)
    rotation_degrees: Literal[0, 90, 180, 270]
    sample_count: int = Field(ge=1, le=6)
    frames: list[VideoFrame] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        import math
        if self.width * self.height > 2073600 or self.sample_count != min(6, math.ceil(self.duration_seconds / 5)):
            raise ValueError("invalid video limits")
        previous = 0
        for frame in self.frames:
            if frame.index <= previous or frame.index > self.sample_count:
                raise ValueError("invalid frame order")
            expected = (frame.index - .5) * self.duration_seconds / self.sample_count
            if abs(frame.target_seconds - expected) > .000001:
                raise ValueError("invalid sampling target")
            previous = frame.index
        return self
