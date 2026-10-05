from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


NonBlankText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Place(BaseModel):
    id: NonBlankText
    name: NonBlankText
    address: str | None
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    category: str | None
    source: Literal["amap"] = "amap"


class ConfirmedPlace(Place):
    """A client-confirmed search result, not an independently reverified POI.

    Keep the public Place field semantics while requiring explicit provenance,
    real JSON numbers and a complete known shape at the request boundary.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    source: Literal["amap"]
