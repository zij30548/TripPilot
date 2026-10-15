"""Closed product destinations; codes checked against AMap's 2026-01-07 table.

Membership checks retained upstream administrative fields, not a name heuristic
or an independent verification of client-submitted POI snapshots. No extra HTTP.
"""
import json
import re
from pathlib import Path
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict

DestinationId = Literal["shanghai", "hangzhou", "beijing", "guangzhou", "chengdu", "xian"]


class Destination(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: DestinationId
    name: str
    official_name: str
    adcode: str
    citycode: str
    district_level: Literal["province", "city"]
    district_adcodes: tuple[str, ...]


def load_destinations(values: object) -> dict[DestinationId, Destination]:
    """Fail closed on incomplete/ambiguous configuration, before dict deduplication.

    Structural checks do not replace checking the official source during onboarding.
    POI membership still uses the explicit district list, never this prefix check.
    """
    if not isinstance(values, list):
        raise ValueError("目的地定义必须为列表。")
    rows = [Destination.model_validate(item) for item in values]
    if {d.id for d in rows} != set(get_args(DestinationId)):
        raise ValueError("目的地定义与允许值不一致。")
    for field in ("id", "name", "official_name", "adcode", "citycode"):
        if len({getattr(d, field) for d in rows}) != len(rows):
            raise ValueError("目的地定义存在重复标识。")
    seen: set[str] = set()
    for d in rows:
        prefix = d.adcode[:2] if d.district_level == "province" else d.adcode[:4]
        if (not re.fullmatch(r"\d{6}", d.adcode) or not re.fullmatch(r"0\d{2,3}", d.citycode)
                or d.official_name != d.name + "市" or not d.name.strip()
                or not d.adcode.endswith("0000" if d.district_level == "province" else "00")
                or d.adcode not in d.district_adcodes or len(set(d.district_adcodes)) != len(d.district_adcodes)
                or any(not re.fullmatch(r"\d{6}", code) or not code.startswith(prefix) for code in d.district_adcodes)
                or seen.intersection(d.district_adcodes)):
            raise ValueError("目的地行政归属配置无效。")
        seen.update(d.district_adcodes)
    return {d.id: d for d in rows}


DESTINATIONS = load_destinations(json.loads(
    (Path(__file__).resolve().parents[2] / "shared/destinations.json").read_text()))


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
