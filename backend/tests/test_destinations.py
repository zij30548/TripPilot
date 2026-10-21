"""Closed-city contracts; synthetic region fixtures, no real upstream or user DB."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
from tests.business_client import BusinessTestClient as TestClient

from api.places import get_http_client
from api.amap_proxy import get_proxy_http_client
from core.config import Settings, get_settings
from core.destinations import DESTINATIONS, PlaceRegion, load_destinations
from tooling.providers.amap import AmapClient, AmapError
from tooling.providers.amap_weather import convert_forecast
from core.schemas.destination import CityScheduleRequest, CityScheduleResponse, CityCandidateRequest
from core.schemas.explanation import ScheduleSnapshot
from core.schemas.saved_trip import SavedSnapshot, snapshot_adapter
from core.schemas.weather import CityWeatherRequest
from tooling.travel.explanation import compact_schedule, validate_snapshot
from memory.saved_trips import database_path, engine_for
from tests.archive_owner import seed_owner
from tests.test_saved_trips import snapshot
from tests.test_weather import sample_upstream as weather_data, sample_request, QUERY_TIME
from tests.test_transit import sample_upstream as transit_data
from main import app


def modern_snapshot(city="hangzhou", sample="complete_walk"):
    value = snapshot(sample)
    value["schema_version"] = "saved-trip-v2"
    d = DESTINATIONS[city]
    for req in [value["requirements"], value["schedule"]["request"]]:
        req["destination_id"] = city
        for place in [req["accommodation_place"], *req["must_visit_places"], *req.get("optional_places", [])]:
            place["region"] = {"citycode": d.citycode, "cityname": d.official_name, "adcode": d.district_adcodes[1]}
    value["requirements"]["daily_start_time"] += ":00"
    value["requirements"]["daily_end_time"] += ":00"
    return value


class DestinationContracts(unittest.TestCase):
    def test_codes_districts_and_no_prefix_or_name_guess(self):
        self.assertEqual({k: (d.adcode, d.citycode) for k, d in DESTINATIONS.items()},
                         {"shanghai": ("310000", "021"), "hangzhou": ("330100", "0571"), "beijing": ("110000", "010"),
                          "guangzhou": ("440100", "020"), "chengdu": ("510100", "028"), "xian": ("610100", "029")})
        for key, d in DESTINATIONS.items():
            for district in d.district_adcodes:
                r = PlaceRegion(citycode=d.citycode, cityname=d.official_name, adcode=district)
                self.assertTrue(r.belongs_to(key))
                self.assertFalse(r.model_copy(update={"adcode": d.adcode[:2]+"9999"}).belongs_to(key))
                self.assertFalse(r.model_copy(update={"cityname": "名字含"+d.name}).belongs_to(key))

    def test_definition_validation_rejects_enum_drift_duplicates_and_bad_membership(self):
        rows = [d.model_dump(mode="json") for d in DESTINATIONS.values()]
        self.assertEqual(load_destinations(rows), DESTINATIONS)
        mutations = [lambda r: r.pop(), lambda r: r.append(copy.deepcopy(r[0])),
                     lambda r: r[0].update(id="unknown"), lambda r: r[0].update(citycode=r[1]["citycode"]),
                     lambda r: r[0].update(adcode=r[1]["adcode"]), lambda r: r[0].update(citycode="21"),
                     lambda r: r[3].update(district_level="province"),
                     lambda r: r[3]["district_adcodes"].append("510104"),
                     lambda r: r[3]["district_adcodes"].append("440103"),
                     lambda r: r[3]["district_adcodes"].remove("440100")]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                broken = copy.deepcopy(rows); mutate(broken)
                with self.assertRaises(ValueError): load_destinations(broken)

    def test_v1_unchanged_v2_replay_both_modes_and_strict_extra_fields(self):
        for sample in ["complete_walk", "transit_unknown_legs", "required_prefix", "three_days_blank", "no_visits"]:
            old = snapshot(sample)
            self.assertEqual(SavedSnapshot.model_validate_json(json.dumps(old)).schedule.model_dump(mode="json"), old["schedule"])
            for city in DESTINATIONS:
                raw = modern_snapshot(city, sample)
                parsed = snapshot_adapter.validate_json(json.dumps(raw), strict=True)
                self.assertEqual(parsed.model_dump(mode="json"), raw)
                compact = ScheduleSnapshot.model_validate_json(json.dumps(compact_schedule(parsed.schedule)))
                validate_snapshot(compact)
        for mutate in [lambda d: d["requirements"].update(destination_id="unknown"),
                       lambda d: d["requirements"].pop("destination_id"),
                       lambda d: d["schedule"]["request"].update(destination_id="beijing"),
                       lambda d: d["schedule"]["request"]["accommodation_place"]["region"].update(adcode="310101"),
                       lambda d: d["schedule"]["request"]["accommodation_place"].update(verified=True),
                       lambda d: d["schedule"]["days"][0].update(return_time="23:59")]:
            raw = modern_snapshot(); mutate(raw)
            with self.assertRaises(ValueError): snapshot_adapter.validate_json(json.dumps(raw), strict=True)

    def test_weather_identity_never_falls_back_to_shanghai(self):
        for city, d in DESTINATIONS.items():
            req = CityWeatherRequest.model_validate({**sample_request(), "destination_id": city})
            payload = weather_data(); payload["forecasts"][0].update(city=d.official_name, adcode=d.adcode)
            response = convert_forecast(payload, req, QUERY_TIME)
            self.assertEqual(response.city, d.official_name); self.assertEqual(response.request.destination_id, city)
            payload["forecasts"][0]["adcode"] = "000000"
            with self.assertRaises(AmapError): convert_forecast(payload, req, QUERY_TIME)


class DestinationAPIs(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name)/"city.sqlite3"
        seed_owner(engine_for(str(self.path)))
        self.requests = []
        self.city = "hangzhou"
        self.mutation = {}
        def handle(req):
            self.requests.append(req)
            d = DESTINATIONS[self.city]
            if req.url.path.endswith("/text"):
                return httpx.Response(200, json={"status": "1", "pois": [{"id": "fixture-poi", "name": "同名受控地点", "address": "受控地址", "location": "120.16,30.27", "type": "上游原始类别", "cityname": d.official_name, "citycode": d.citycode, "adcode": d.district_adcodes[1], **self.mutation}]})
            if req.url.path.endswith("/weatherInfo"):
                data = weather_data(); data["forecasts"][0].update(city=d.official_name, adcode=d.adcode)
                return httpx.Response(200, json=data)
            if req.url.path.endswith("/integrated"):
                return httpx.Response(200, json=transit_data())
            return httpx.Response(200, json={"status": "1", "districts": []})
        async def client():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as c: yield c
        settings = Settings(_env_file=None, amap_web_key="fixture-web", amap_js_key="fixture-js", amap_js_security_code="fixture-security")
        self.enterContext(patch.dict(app.dependency_overrides, {get_http_client: client, get_proxy_http_client: client, get_settings: lambda: settings, database_path: lambda: self.path}))
        self.client = self.enterContext(TestClient(app))
        self.enterContext(patch("tooling.travel.schedule_routes.MIN_ROUTE_START_INTERVAL_SECONDS", 0))

    def tearDown(self):
        engine_for(str(self.path)).dispose(); engine_for.cache_clear(); self.directory.cleanup()

    def test_search_candidates_transit_and_weather_use_selected_city_without_added_queries(self):
        for city, d in DESTINATIONS.items():
            self.city = city; self.requests.clear()
            res = self.client.get("/places/search", params={"keyword": "同名地点", "destination_id": city})
            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual(res.json()[0]["region"]["adcode"], d.district_adcodes[1])
            self.assertEqual(self.requests[-1].url.params["region"], d.adcode)
            req = modern_snapshot(city)["schedule"]["request"]
            candidates = {k: req[k] for k in ("destination_id", "accommodation_place", "must_visit_places")}
            candidates["interests"] = ["摄影", "摄影"]
            res = self.client.post("/places/candidates", json=candidates)
            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual(res.json()["destination_id"], city)
            self.assertTrue(all("region" in c["place"] for c in res.json()["candidates"]))
            req["transport_mode"] = "transit"
            res = self.client.post("/trips/schedule-preview?drawing=walking-v1", json=req)
            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual(res.json()["schedule"]["request"]["destination_id"], city)
            routed = [r for r in self.requests if r.url.path.endswith("/integrated")]
            self.assertEqual(len(routed), 5)  # two required places: four home edges + one directed adjacency
            self.assertTrue(all(r.url.params["city"] == d.citycode and r.url.params["cityd"] == d.citycode for r in routed))
            res = self.client.post("/weather/forecast", json={**sample_request(), "destination_id": city})
            self.assertEqual(res.status_code, 200, res.text); self.assertEqual(res.json()["adcode"], d.adcode)
            self.assertEqual(len(self.requests), 8)

    def test_missing_contradictory_or_foreign_region_fails_explicitly(self):
        for city in DESTINATIONS:
            self.city = city
            other = DESTINATIONS["beijing" if city != "beijing" else "shanghai"]
            for mutation in [{"citycode": None}, {"adcode": other.district_adcodes[1]}, {"cityname": other.official_name}]:
                self.mutation = mutation
                res = self.client.get("/places/search", params={"keyword": "上海路", "destination_id": city})
                self.assertEqual(res.status_code, 502); self.assertIn("归属", res.json()["detail"])
                req = modern_snapshot(city)["schedule"]["request"]
                req = {k: req[k] for k in ("destination_id", "accommodation_place", "must_visit_places")}
                req["interests"] = ["摄影"]
                response = self.client.post("/places/candidates", json=req)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["status"], "failed")
                self.assertTrue(all(c["role"] == "must_visit" for c in response.json()["candidates"]))
        self.requests.clear()
        for dest in [None, "shenzhen", ""]:
            req = modern_snapshot()["schedule"]["request"]
            if dest is None: req.pop("destination_id")
            else: req["destination_id"] = dest
            self.assertEqual(self.client.post("/trips/schedule-preview", json=req).status_code, 422)
        self.assertEqual(self.requests, [])

    def test_proxy_six_city_allowlist_no_open_target_or_district(self):
        for city, d in DESTINATIONS.items():
            for name in [d.name, d.official_name, d.adcode]:
                res = self.client.get("/_AMapService/v3/config/district", params={"key": "fixture-js", "keywords": name, "level": d.district_level})
                self.assertEqual(res.status_code, 200, res.text)
        for params in [{"keywords": "深圳"}, {"keywords": "440103"}, {"keywords": "成都", "level": "province"}, {"keywords": "北京", "url": "https://bad.invalid"}, {"keywords": "北京", "key": "wrong"}]:
            before = len(self.requests)
            self.assertIn(self.client.get("/_AMapService/v3/config/district", params={"key": "fixture-js", **params}).status_code, (400, 403))
            self.assertEqual(len(self.requests), before)

    def test_v1_v2_disk_reopen_idempotency_never_rewrites_old_row(self):
        old = {"operation_id": uuid4().hex, "name": "旧上海", "snapshot": snapshot()}
        first = self.client.post("/saved-trips", json=old); self.assertEqual(first.status_code, 200, first.text)
        for city in DESTINATIONS:
            body = {"operation_id": uuid4().hex, "name": city, "snapshot": modern_snapshot(city)}
            res = self.client.post("/saved-trips", json=body); self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual(res.json(), self.client.post("/saved-trips", json=body).json())
            self.assertEqual(res.json()["snapshot"], body["snapshot"])
        engine_for(str(self.path)).dispose(); engine_for.cache_clear()
        self.assertEqual(self.client.get("/saved-trips/"+first.json()["id"]).json(), first.json())
        self.assertEqual(len(self.client.get("/saved-trips").json()["items"]), len(DESTINATIONS) + 1)
        self.assertEqual(self.requests, [])
