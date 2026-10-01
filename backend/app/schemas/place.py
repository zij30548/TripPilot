from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints


NonBlankText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Place(BaseModel):
    id: NonBlankText
    name: NonBlankText
    address: str | None
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    category: str | None
    source: Literal["amap"] = "amap"
