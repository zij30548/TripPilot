"""Closed product destinations; codes checked against AMap's 2026-01-07 table.

Membership checks retained upstream administrative fields, not a name heuristic
or an independent verification of client-submitted POI snapshots. No extra HTTP.
"""
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

DestinationId = Literal["shanghai", "hangzhou", "beijing"]


class Destination(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: DestinationId
    name: str
    official_name: str
    adcode: str
    citycode: str
    district_level: Literal["province", "city"]
    district_adcodes: tuple[str, ...]


DESTINATIONS = {d.id: d for d in (
    Destination.model_validate(item) for item in
    json.loads((Path(__file__).resolve().parents[2] / "shared/destinations.json").read_text())
)}


def destination_for(value: str) -> Destination:
    if value not in DESTINATIONS:
        raise ValueError("请选择支持的目的地。")
    return DESTINATIONS[value]


class PlaceRegion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    citycode: str
    cityname: str
    adcode: str

    def belongs_to(self, destination_id: DestinationId) -> bool:
        city = destination_for(destination_id)
        return (self.citycode == city.citycode and self.cityname in (city.name, city.official_name)
                and self.adcode in city.district_adcodes)
