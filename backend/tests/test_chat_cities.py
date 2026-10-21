"""No external service: exact city identity and source-derived membership."""
import copy
import hashlib
import json
from pathlib import Path
import unittest

from core.chat_cities import (CATALOG_VERSION, CITIES, city_for, legacy_city_id, load_catalog,
                             region_belongs_to, resolve_city, search_cities)
from core.destinations import DESTINATIONS, PlaceRegion
from tools.chat_city_catalog import SOURCE_SHA256, source_rows


class ChatCitiesTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[2]
        self.catalog = json.loads((self.root / "shared/chat-cities.json").read_text())

    def test_source_and_reproducible_counts(self):
        raw = (self.root / "shared/chat-cities.json").read_bytes()
        source = json.loads((self.root / "shared/chat-cities-source.json").read_text())
        self.assertEqual(CATALOG_VERSION, self.catalog["schema_version"])
        self.assertEqual(SOURCE_SHA256, source["archive_sha256"])
        self.assertEqual(hashlib.sha256(raw).hexdigest(), source["catalog_sha256"])
        self.assertEqual(369, len(CITIES))
        self.assertEqual(3182, sum(len(c.district_adcodes) for c in CITIES.values()))
        self.assertEqual(3240, source["source_rows"])
        self.assertEqual(58, len(source["excluded"]))
        self.assertEqual("2026-10-21", source["retrieved_at"])
        with self.assertRaisesRegex(ValueError, "official_source_hash_mismatch"):
            source_rows(b"not the official workbook")

    def test_old_six_cities_have_exact_members_without_changing_legacy(self):
        for identifier, old in DESTINATIONS.items():
            new = city_for(legacy_city_id(identifier))
            self.assertEqual(old.adcode, new.id)
            self.assertEqual(old.citycode, new.citycode)
            self.assertEqual(set(old.district_adcodes), set(new.district_adcodes))
        self.assertEqual(6, len(DESTINATIONS))

    def test_local_search_and_exact_resolution_do_not_infer_mixed_cities(self):
        self.assertEqual("310000", resolve_city("  Shanghai ").id)
        self.assertEqual("610100", resolve_city("XI’AN").id)
        self.assertEqual("110000", resolve_city("北京市").id)
        for unknown in (None, "", "上海北京", "去北京玩", "上", "香港", "台湾省"):
            self.assertIsNone(resolve_city(unknown))
        self.assertEqual(["310000"], [c.id for c in search_cities("上海")])
        self.assertLessEqual(len(search_cities("")), 20)
        self.assertEqual([], search_cities("not-a-city"))
        self.assertEqual(search_cities("市", 3), search_cities("市", 3))
        for query, limit in (("x" * 81, 20), ("", 21), ("", True)):
            with self.assertRaises(ValueError):
                search_cities(query, limit)

    def test_nonzero_citycodes_and_province_direct_places_are_explicit(self):
        self.assertEqual("1433", city_for("222400").citycode)
        self.assertEqual("1558", city_for("341200").citycode)
        self.assertEqual("province_direct", city_for("419001").level)
        self.assertEqual(("419001",), city_for("419001").district_adcodes)
        self.assertEqual("province_direct", city_for("659012").level)
        self.assertEqual("municipality", city_for("500000").level)
        for unknown in ("999999", "810000", "820000", "710000", "440499", "440000", "广州", "shanghai"):
            with self.assertRaises(ValueError):
                city_for(unknown)

    def test_membership_rejects_same_prefix_unlisted_and_contradictory_fields(self):
        valid = PlaceRegion(citycode="020", cityname="广州市", adcode="440106")
        self.assertTrue(region_belongs_to(valid, "440100"))
        for changes in ({"adcode": "440199"}, {"citycode": "021"}, {"cityname": "深圳市"}, {"adcode": "440303"}):
            self.assertFalse(region_belongs_to(valid.model_copy(update=changes), "440100"))

    def test_structural_drift_alias_collisions_and_invalid_members_rejected(self):
        for mutate in (
            lambda x: x.update(schema_version="chat-city-catalog-v0"),
            lambda x: x.update(unexpected=True),
            lambda x: x["cities"].append(copy.deepcopy(x["cities"][0])),
            lambda x: x["cities"][1].update(citycode=x["cities"][0]["citycode"]),
            lambda x: x["cities"][1]["aliases"].append(x["cities"][0]["aliases"][0]),
            lambda x: x["cities"][0]["district_adcodes"].append("999999"),
            lambda x: x["cities"][0].update(level="province_direct"),
            lambda x: x["cities"][0].update(province_name="错误省份"),
            lambda x: x["cities"][0].update(extra="x"),
        ):
            with self.subTest(mutate=mutate):
                value = copy.deepcopy(self.catalog)
                mutate(value)
                with self.assertRaises(ValueError):
                    load_catalog(value)


if __name__ == "__main__":
    unittest.main()
