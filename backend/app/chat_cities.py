"""Versioned, offline-only chat city reference, independent of legacy destinations.

Directory membership is not proof of current AMap data or service coverage.
Unknown/ambiguous names never fall back to a city. POI checks use explicit
official members plus citycode/name, not an adcode prefix inference.
"""
from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from typing import Annotated

from app.destinations import PlaceRegion

CATALOG_VERSION = "chat-city-catalog-v1"
ROOT = Path(__file__).resolve().parents[2] / "shared"
CITY_ID = re.compile(r"[0-9]{6}")
LEGACY_CITIES = {"shanghai": "310000", "hangzhou": "330100", "beijing": "110000",
                 "guangzhou": "440100", "chengdu": "510100", "xian": "610100"}
MUNICIPALITIES = {"110000", "120000", "310000", "500000"}
CityText = Annotated[str, StringConstraints(min_length=1, max_length=80)]


class ChatCity(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    id: Annotated[str, StringConstraints(pattern=r"^[0-9]{6}$")]
    name: CityText
    official_name: CityText
    province_name: CityText
    citycode: Annotated[str, StringConstraints(pattern=r"^[0-9]{3,4}$")]
    level: Literal["municipality", "prefecture", "province_direct"]
    district_adcodes: tuple[str, ...] = Field(min_length=1, max_length=100)
    aliases: tuple[CityText, ...] = Field(min_length=1, max_length=10)

    def belongs_to(self, region: PlaceRegion) -> bool:
        return (region.citycode == self.citycode
                and region.cityname in (self.name, self.official_name)
                and region.adcode in self.district_adcodes)


def _json(raw: bytes) -> object:
    def unique(items: list[tuple[str, object]]) -> dict:
        output = {}
        for key, value in items:
            if key in output:
                raise ValueError("duplicate_city_catalog_key")
            output[key] = value
        return output
    def nonfinite(_value: str) -> None:
        raise ValueError("invalid_city_catalog_number")
    if not 0 < len(raw) <= 256 * 1024:
        raise ValueError("city_catalog_size")
    return json.loads(raw, object_pairs_hook=unique, parse_constant=nonfinite)


def load_catalog(value: object) -> dict[str, ChatCity]:
    if (not isinstance(value, dict) or set(value) != {"schema_version", "source_version", "cities"}
            or value["schema_version"] != CATALOG_VERSION or value["source_version"] != "2026-01-07"
            or not isinstance(value["cities"], list) or not 1 <= len(value["cities"]) <= 500):
        raise ValueError("invalid_chat_city_catalog")
    cities = [ChatCity.model_validate_json(json.dumps(row, ensure_ascii=False)) for row in value["cities"]]
    if len({city.id for city in cities}) != len(cities) or len({city.citycode for city in cities}) != len(cities):
        raise ValueError("duplicate_chat_city")
    members: set[str] = set()
    aliases: dict[str, str] = {}
    provinces: dict[str, str] = {}
    for city in cities:
        if (city.id[:2] in {"71", "81", "82"} or city.id not in city.district_adcodes
                or len(set(city.district_adcodes)) != len(city.district_adcodes)
                or any(CITY_ID.fullmatch(code) is None or code[:2] != city.id[:2] for code in city.district_adcodes)
                or members.intersection(city.district_adcodes)
                or (city.level == "municipality") != (city.id in MUNICIPALITIES)
                or city.level == "prefecture" and (not city.id.endswith("00") or city.id.endswith("0000"))
                or city.level == "province_direct" and city.id.endswith("00")
                or city.level == "municipality" and city.province_name != city.official_name
                or city.id[:2] in provinces and provinces[city.id[:2]] != city.province_name
                or city.name not in city.aliases or city.official_name not in city.aliases):
            raise ValueError("inconsistent_chat_city_membership")
        provinces[city.id[:2]] = city.province_name
        members.update(city.district_adcodes)
        for alias in city.aliases:
            normalized = alias.casefold()
            if alias != alias.strip() or normalized in aliases:
                raise ValueError("ambiguous_chat_city_alias")
            aliases[normalized] = city.id
    return {city.id: city for city in cities}


def _read_catalog() -> dict[str, ChatCity]:
    raw = (ROOT / "chat-cities.json").read_bytes()
    source = _json((ROOT / "chat-cities-source.json").read_bytes())
    expected_keys = {"schema_version", "source_page", "source_url", "source_version", "retrieved_at", "archive_sha256",
                     "workbook_sha256", "catalog_sha256", "source_rows", "city_count", "member_count", "excluded", "scope"}
    if (not isinstance(source, dict) or set(source) != expected_keys
            or source["schema_version"] != "chat-city-source-v1" or source["source_version"] != "2026-01-07"
            or source["source_page"] != "https://lbs.amap.com/api/webservice/download"
            or source["source_url"] != "https://a.amap.com/lbs/static/code_resource/AMap_adcode_citycode.zip"
            or source["archive_sha256"] != "a5f190378c7dd5e592b0e95ca81ae7d454116293d5ac77472f96c20d2fa184e3"
            or any(not isinstance(source[key], str) or re.fullmatch(r"[0-9a-f]{64}", source[key]) is None
                   for key in ("workbook_sha256", "catalog_sha256"))
            or hashlib.sha256(raw).hexdigest() != source["catalog_sha256"]
            or not isinstance(source["retrieved_at"], str)
            or date.fromisoformat(source["retrieved_at"]).isoformat() != source["retrieved_at"]):
        raise ValueError("unverified_chat_city_source")
    cities = load_catalog(_json(raw))
    if (source["city_count"] != len(cities) or source["member_count"] != sum(len(c.district_adcodes) for c in cities.values())
            or not isinstance(source["excluded"], list) or len(source["excluded"]) > 100
            or source["source_rows"] != source["member_count"] + len(source["excluded"])):
        raise ValueError("inconsistent_chat_city_source")
    excluded = set()
    for row in source["excluded"]:
        if (not isinstance(row, dict) or set(row) != {"name", "adcode", "reason"}
                or not isinstance(row["name"], str) or not 1 <= len(row["name"]) <= 80
                or not isinstance(row["adcode"], str) or CITY_ID.fullmatch(row["adcode"]) is None
                or row["adcode"] in excluded or row["reason"] not in {"outside_mainland_scope", "missing_citycode"}):
            raise ValueError("invalid_chat_city_exclusion")
        excluded.add(row["adcode"])
    return cities


CITIES = _read_catalog()
_ALIASES = {alias.casefold(): city.id for city in CITIES.values() for alias in city.aliases}


def city_for(city_id: str) -> ChatCity:
    if not isinstance(city_id, str) or city_id not in CITIES:
        raise ValueError("chat_city_unsupported")
    return CITIES[city_id]


def legacy_city_id(value: str) -> str | None:
    return LEGACY_CITIES.get(value) if isinstance(value, str) else None


def region_belongs_to(region: PlaceRegion, city_id: str) -> bool:
    return isinstance(region, PlaceRegion) and city_for(city_id).belongs_to(region)


def resolve_city(value: object) -> ChatCity | None:
    if not isinstance(value, str) or not 1 <= len(value) <= 80:
        return None
    identifier = _ALIASES.get(value.strip().casefold())
    return CITIES[identifier] if identifier is not None else None


def search_cities(query: str, limit: int = 20) -> list[ChatCity]:
    if not isinstance(query, str) or len(query) > 80 or type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError("invalid_chat_city_query")
    text = query.strip().casefold()
    rows = [city for city in CITIES.values() if not text or any(text in alias.casefold() for alias in city.aliases)]
    rows.sort(key=lambda city: (0 if text in {alias.casefold() for alias in city.aliases} else 1, city.id))
    return rows[:limit]
