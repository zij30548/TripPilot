"""Reproduce the separate chat city catalog from a pinned official offline table.

No HTTP, credentials, database or output-file mutation. `render` writes JSON to
stdout; maintainers review/apply the generated files. Runtime POI membership
uses materialized members, never guessed adcode prefixes.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

SOURCE_URL = "https://a.amap.com/lbs/static/code_resource/AMap_adcode_citycode.zip"
SOURCE_PAGE = "https://lbs.amap.com/api/webservice/download"
SOURCE_VERSION = "2026-01-07"
SOURCE_SHA256 = "a5f190378c7dd5e592b0e95ca81ae7d454116293d5ac77472f96c20d2fa184e3"
CATALOG_VERSION = "chat-city-catalog-v1"
ROOT = Path(__file__).resolve().parents[2]
NAMESPACE = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
ENGLISH = {"110000": ("Beijing",), "310000": ("Shanghai",), "330100": ("Hangzhou",),
           "440100": ("Guangzhou",), "510100": ("Chengdu",), "610100": ("Xian", "Xi'an", "Xi’an")}
MUNICIPALITIES = {"110000", "120000", "310000", "500000"}


def encoded(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


def source_rows(raw: bytes) -> tuple[list[tuple[str, str, str]], str]:
    if not 0 < len(raw) <= 4 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise ValueError("official_source_hash_mismatch")
    with ZipFile(BytesIO(raw)) as archive:
        if archive.namelist() != ["AMap_adcode_citycode.xlsx"]:
            raise ValueError("unexpected_official_archive")
        if archive.infolist()[0].file_size > 4 * 1024 * 1024:
            raise ValueError("official_archive_limit")
        workbook = archive.read("AMap_adcode_citycode.xlsx")
    with ZipFile(BytesIO(workbook)) as archive:
        if any(info.file_size > 8 * 1024 * 1024 for info in archive.infolist()):
            raise ValueError("official_workbook_limit")
        strings = ["".join(node.itertext()) for node in ET.fromstring(archive.read("xl/sharedStrings.xml")).findall("s:si", NAMESPACE)]
        rows = []
        for row in ET.fromstring(archive.read("xl/worksheets/sheet1.xml")).findall("s:sheetData/s:row", NAMESPACE):
            values = {}
            for cell in row.findall("s:c", NAMESPACE):
                value = cell.find("s:v", NAMESPACE)
                content = value.text if value is not None else ""
                values[re.sub(r"[0-9]", "", cell.attrib["r"])] = strings[int(content)] if cell.attrib.get("t") == "s" and content else content
            rows.append(tuple(values.get(column, "") for column in ("A", "B", "C")))
    if rows[0] != ("中文名", "adcode", "citycode") or len(rows) != 3241:
        raise ValueError("unexpected_official_sheet")
    rows = rows[1:]
    if len({row[1] for row in rows}) != len(rows) or any(not re.fullmatch(r"[0-9]{6}", row[1]) for row in rows):
        raise ValueError("invalid_official_rows")
    return rows, hashlib.sha256(workbook).hexdigest()


def build_catalog(raw: bytes, retrieved_at: str) -> tuple[dict, dict]:
    if date.fromisoformat(retrieved_at).isoformat() != retrieved_at:
        raise ValueError("invalid_retrieval_date")
    rows, workbook_hash = source_rows(raw)
    by_code = {row[1]: row for row in rows}
    groups: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    excluded = []
    for name, adcode, citycode in rows:
        if adcode[:2] in {"71", "81", "82"}:
            excluded.append({"name": name, "adcode": adcode, "reason": "outside_mainland_scope"})
        elif citycode == "\\N":
            excluded.append({"name": name, "adcode": adcode, "reason": "missing_citycode"})
        elif not re.fullmatch(r"[0-9]{3,4}", citycode):
            raise ValueError("invalid_official_citycode")
        else:
            groups[citycode].append((name, adcode, citycode))
    cities = []
    for citycode, members in groups.items():
        parents = [row for row in members if row[1].endswith("00")]
        if len(parents) == 1:
            name, identifier, _ = parents[0]
            level = "municipality" if identifier in MUNICIPALITIES else "prefecture"
        elif not parents and len(members) == 1:
            name, identifier, _ = members[0]
            level = "province_direct"
        else:
            raise ValueError("ambiguous_citycode_group")
        # Source-code hierarchy is used only while generating explicit metadata.
        # Actual POIs are checked against the generated exact member collection.
        province = by_code[identifier[:2] + "0000"][0]
        if any(row[1][:2] != identifier[:2] for row in members):
            raise ValueError("cross_province_citycode_group")
        short = name[:-1] if name.endswith("市") else name
        aliases = list(dict.fromkeys([name, short, *ENGLISH.get(identifier, ())]))
        cities.append({"id": identifier, "name": short, "official_name": name, "province_name": province,
                       "citycode": citycode, "level": level,
                       "district_adcodes": sorted(row[1] for row in members), "aliases": aliases})
    catalog = {"schema_version": CATALOG_VERSION, "source_version": SOURCE_VERSION, "cities": sorted(cities, key=lambda c: c["id"])}
    metadata = {"schema_version": "chat-city-source-v1", "source_page": SOURCE_PAGE, "source_url": SOURCE_URL,
                "source_version": SOURCE_VERSION, "retrieved_at": retrieved_at, "archive_sha256": SOURCE_SHA256,
                "workbook_sha256": workbook_hash, "catalog_sha256": hashlib.sha256(encoded(catalog)).hexdigest(),
                "source_rows": len(rows), "city_count": len(cities),
                "member_count": sum(len(c["district_adcodes"]) for c in cities),
                "excluded": sorted(excluded, key=lambda c: c["adcode"]),
                "scope": "Offline mainland city/region selection and explicit membership reference; not current upstream certification or nationwide travel acceptance."}
    return catalog, metadata


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("render", "verify"))
    parser.add_argument("--source-zip", type=Path, required=True)
    parser.add_argument("--retrieved-at", required=True)
    args = parser.parse_args()
    try:
        catalog, metadata = build_catalog(args.source_zip.read_bytes(), args.retrieved_at)
        if args.action == "verify":
            if ((ROOT / "shared/chat-cities.json").read_bytes() != encoded(catalog)
                    or (ROOT / "shared/chat-cities-source.json").read_bytes() != encoded(metadata)):
                raise ValueError("catalog_drift")
            print(json.dumps({"result": "verified", "cities": len(catalog["cities"]), "business_calls": 0}))
        else:
            print(json.dumps({"catalog": catalog, "source": metadata}, ensure_ascii=False))
        return 0
    except (ValueError, KeyError, BadZipFile, OSError, ET.ParseError):
        print("Official catalog verification stopped; do not update hashes to bypass a mismatch.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
