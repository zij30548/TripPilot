import json
import os
import unittest
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.places import get_http_client
from app.config import Settings, get_settings
from app.schemas.transit import TransitRouteRequest, TransitRouteResponse
from main import app


TEST_KEY = "mock-transit-key-not-a-real-credential"


def sample_request() -> dict:
    # Fictional fixtures for MockTransport only, never live-service evidence.
    return {
        "origin": {"place_id": "mock-poi-a", "longitude": 121.4000001, "latitude": 31.2000001},
        "destination": {"place_id": "mock-poi-b", "longitude": 121.4200008, "latitude": 31.2200008},
    }


def sample_line() -> dict:
    return {
        "name": "测试地铁线(甲站--乙站)", "type": "地铁线路",
        "departure_stop": {"name": "甲站", "id": "mock-stop-a"},
        "arrival_stop": {"name": "乙站", "id": "mock-stop-b"},
        "distance": "3500", "duration": "601",
        "polyline": "121.402,31.202;121.403,31.203;121.417,31.217",
    }


def sample_walk() -> dict:
    return {
        "distance": "250.5", "duration": "181",
        "steps": [
            {"instruction": "沿测试道路步行", "polyline": "121.4,31.2;121.401,31.201"},
            # Deliberate real-geometry gap in a fictional fixture, never joined.
            {"instruction": "进入测试站", "polyline": "121.4015,31.2015;121.402,31.202"},
        ],
    }


def sample_proposal() -> dict:
    return {
        "duration": "1201", "walking_distance": "501", "cost": "4.5",
        "segments": [
            {"walking": sample_walk(), "bus": {"buslines": [sample_line()]}, "railway": {}, "taxi": []},
            {"walking": {"distance": "250.5", "duration": "181", "steps": [
                {"instruction": "离站后步行", "polyline": "121.418,31.218;121.42,31.22"},
            ]}, "bus": {"buslines": []}, "entrance": [], "exit": {}},
        ],
    }


def sample_upstream() -> dict:
    return {"status": "1", "info": "OK", "count": "1", "route": {
        # This distance is direct walking distance, never transit route length.
        "distance": "9999999", "taxi_cost": "300", "transits": [sample_proposal()],
    }}


class TransitRouteApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(patch.dict(os.environ, {}, clear=True))
        self.requests: list[httpx.Request] = []
        self.clients: list[httpx.AsyncClient] = []
        self.response = httpx.Response(200, json=sample_upstream())
        self.failure: type[httpx.RequestError] | None = None
        self.failures: list[type[httpx.RequestError] | None] = []
        self.settings = Settings(_env_file=None, amap_web_key=TEST_KEY)

        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            failure = self.failures.pop(0) if self.failures else self.failure
            if failure:
                raise failure(f"Sensitive upstream URL: {request.url}", request=request)
            return self.response

        async def mock_http_client() -> AsyncIterator[httpx.AsyncClient]:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
                self.clients.append(client)
                yield client

        self.enterContext(patch.dict(app.dependency_overrides, {
            get_settings: lambda: self.settings, get_http_client: mock_http_client,
        }))
        self.client = self.enterContext(TestClient(app))

    def transit(self, payload: object | None = None) -> httpx.Response:
        return self.client.post("/routes/transit", json=sample_request() if payload is None else payload)

    def set_proposals(self, proposals: list) -> None:
        data = sample_upstream()
        data["route"]["transits"] = proposals
        self.response = httpx.Response(200, json=data)

    def test_walk_ride_walk_conversion_and_timestamp(self) -> None:
        before = datetime.now(timezone.utc)
        response = self.transit()
        after = datetime.now(timezone.utc)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(set(data), {"status", "source", "queried_at", "selection_rule", "route"})
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["source"], "amap")
        self.assertEqual(data["selection_rule"], "first_supported_complete")
        query_time = datetime.fromisoformat(data["queried_at"])
        self.assertEqual(query_time.utcoffset(), timedelta(0))
        self.assertLessEqual(before, query_time)
        self.assertLessEqual(query_time, after)
        route = data["route"]
        self.assertEqual(route["duration_seconds"], 1201)
        self.assertEqual(route["walking_distance_meters"], 501)
        self.assertEqual(route["fare_cny"], 4.5)
        self.assertTrue(route["geometry_complete"])
        self.assertEqual([leg["mode"] for leg in route["legs"]], ["walking", "subway", "walking"])
        self.assertEqual(route["legs"][1], {
            "mode": "subway", "distance_meters": 3500, "duration_seconds": 601,
            "instruction": None, "line_name": "测试地铁线(甲站--乙站)",
            "departure_stop": "甲站", "arrival_stop": "乙站",
            "geometry": [[[121.402, 31.202], [121.403, 31.203], [121.417, 31.217]]],
            "geometry_complete": True,
        })
        self.assertEqual(route["legs"][0]["instruction"], "沿测试道路步行；进入测试站")
        self.assertIsNone(route["legs"][0]["line_name"])
        TransitRouteResponse.model_validate(data)
        self.assertTrue(all(client.is_closed for client in self.clients))

    def test_fixed_upstream_city_default_conditions_precision_and_timeout(self) -> None:
        self.assertEqual(self.transit().status_code, 200)
        request = self.requests[0]
        self.assertEqual(request.method, "GET")
        self.assertEqual(str(request.url).split("?")[0], "https://restapi.amap.com/v3/direction/transit/integrated")
        self.assertEqual(dict(request.url.params), {
            "key": TEST_KEY, "origin": "121.400000,31.200000", "destination": "121.420001,31.220001",
            "city": "上海", "cityd": "上海", "extensions": "all", "output": "JSON",
        })
        self.assertEqual(request.extensions["timeout"], {
            "connect": 3.0, "read": 10.0, "write": 10.0, "pool": 10.0,
        })
        for field in ["date", "time", "strategy", "nightflag", "origin_id", "destination_id"]:
            self.assertNotIn(field, request.url.params)

    def test_total_is_not_double_counted_and_route_distance_is_not_mileage(self) -> None:
        result = self.transit().json()["route"]
        self.assertEqual(result["duration_seconds"], 1201)
        self.assertNotEqual(result["duration_seconds"], 1201 + 181 + 601 + 181)
        self.assertEqual(result["walking_distance_meters"], 501)
        self.assertNotIn("distance_meters", result)
        self.assertNotIn("taxi_cost", result)
        self.assertNotIn("total_fare", result)

    def test_separate_geometry_steps_and_lng_lat_order_are_preserved(self) -> None:
        result = self.transit().json()["route"]
        self.assertEqual(result["legs"][0]["geometry"], [
            [[121.4, 31.2], [121.401, 31.201]],
            [[121.4015, 31.2015], [121.402, 31.202]],
        ])
        self.assertNotEqual(result["legs"][0]["geometry"][-1][-1], result["legs"][1]["geometry"][-1][-1])

    def test_transfer_preserves_order_without_merging_ride_legs(self) -> None:
        proposal = sample_proposal()
        bus = sample_line()
        bus.update(name="测试公交99路", type="普通公交", polyline="121.418,31.218;121.419,31.219")
        bus["departure_stop"] = {"name": "丙站"}
        bus["arrival_stop"] = {"name": "丁站"}
        proposal["segments"].insert(1, {"walking": sample_walk(), "bus": {"buslines": [bus]}})
        self.set_proposals([proposal])
        route = self.transit().json()["route"]
        self.assertEqual([leg["mode"] for leg in route["legs"]], ["walking", "subway", "walking", "bus", "walking"])
        self.assertEqual(route["legs"][3]["departure_stop"], "丙站")
        self.assertEqual(route["duration_seconds"], 1201)

    def test_documented_ordinary_bus_type_names_are_supported(self) -> None:
        for kind in ["普通公交", "普通公交线路", "公交线路"]:
            with self.subTest(kind=kind):
                proposal = sample_proposal()
                proposal["segments"][0]["bus"]["buslines"][0]["type"] = kind
                self.set_proposals([proposal])
                response = self.transit()
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["route"]["legs"][1]["mode"], "bus")

    def test_positive_walking_total_requires_access_walking_information(self) -> None:
        incomplete = sample_proposal()
        incomplete["segments"] = [{"bus": {"buslines": [sample_line()]}, "walking": {}}]
        self.set_proposals([incomplete])
        self.assertEqual(self.transit().status_code, 502)
        valid = sample_proposal()
        valid["cost"] = "7"
        self.set_proposals([incomplete, valid])
        response = self.transit()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["route"]["fare_cny"], 7)
        incomplete["walking_distance"] = "0"
        self.set_proposals([incomplete])
        self.assertEqual(self.transit().status_code, 200)

    def test_buslines_are_alternatives_first_complete_supported_line_only(self) -> None:
        proposal = sample_proposal()
        other = sample_line()
        other.update(name="备选线路", type="普通公交", polyline="120,30;120.1,30.1")
        other["departure_stop"] = {"name": "其他上车站"}
        other["arrival_stop"] = {"name": "其他下车站"}
        proposal["segments"][0]["bus"]["buslines"].append(other)
        self.set_proposals([proposal])
        route = self.transit().json()["route"]
        self.assertEqual(len(route["legs"]), 3)
        self.assertEqual(route["legs"][1]["line_name"], "测试地铁线(甲站--乙站)")
        self.assertNotIn("其他", json.dumps(route, ensure_ascii=False))
        self.assertNotIn([120.0, 30.0], route["legs"][1]["geometry"][0])

    def test_invalid_or_unsupported_alternative_can_fall_back_without_mixing(self) -> None:
        for first in [dict(sample_line(), type="轮渡"), dict(sample_line(), departure_stop={}),
                      dict(sample_line(), polyline="bad")]:
            with self.subTest(first=first):
                second = sample_line()
                second["name"] = "第二备选线"
                second["departure_stop"] = {"name": "第二上车站"}
                proposal = sample_proposal()
                proposal["segments"][0]["bus"]["buslines"] = [first, second]
                self.set_proposals([proposal])
                response = self.transit()
                self.assertEqual(response.status_code, 200)
                ride = response.json()["route"]["legs"][1]
                self.assertEqual(ride["line_name"], "第二备选线")
                self.assertEqual(ride["departure_stop"], "第二上车站")

    def test_first_complete_supported_proposal_selected_in_upstream_order(self) -> None:
        unsupported = sample_proposal()
        unsupported["segments"][0]["taxi"] = {"distance": "20"}
        malformed = sample_proposal()
        malformed["duration"] = "invalid"
        valid = sample_proposal()
        valid["cost"] = "5"
        faster = sample_proposal()
        faster.update(duration="100", cost="2")
        self.set_proposals([unsupported, malformed, valid, faster])
        route = self.transit().json()["route"]
        self.assertEqual(route["duration_seconds"], 1201)
        self.assertEqual(route["fare_cny"], 5)

    def test_missing_fare_stays_null_and_explicit_zero_is_valid(self) -> None:
        for value in [None, "", []]:
            with self.subTest(value=value):
                proposal = sample_proposal()
                proposal["cost"] = value
                self.set_proposals([proposal])
                self.assertIsNone(self.transit().json()["route"]["fare_cny"])
        proposal = sample_proposal()
        del proposal["cost"]
        self.set_proposals([proposal])
        self.assertIsNone(self.transit().json()["route"]["fare_cny"])
        proposal["cost"] = "0"
        self.set_proposals([proposal])
        self.assertEqual(self.transit().json()["route"]["fare_cny"], 0)

    def test_invalid_fare_is_not_zero_or_unknown(self) -> None:
        for value in [False, True, "NaN", "Infinity", "-1", -1, {}, "free", [1]]:
            with self.subTest(value=value):
                proposal = sample_proposal()
                proposal["cost"] = value
                self.set_proposals([proposal])
                self.assertEqual(self.transit().status_code, 502)

    def test_leg_metrics_may_be_zero_or_missing_and_have_no_walking_length_cap(self) -> None:
        for value in [0, "0", None, "", []]:
            with self.subTest(value=value):
                proposal = sample_proposal()
                for part in [proposal["segments"][0]["walking"], proposal["segments"][0]["bus"]["buslines"][0]]:
                    part["duration"] = value
                    part["distance"] = value
                self.set_proposals([proposal])
                response = self.transit()
                self.assertEqual(response.status_code, 200)
                expected = 0 if value in (0, "0") else None
                self.assertEqual(response.json()["route"]["legs"][1]["duration_seconds"], expected)
        proposal = sample_proposal()
        proposal["walking_distance"] = "100001"
        proposal["segments"][0]["bus"]["buslines"][0]["distance"] = "200001"
        self.set_proposals([proposal])
        self.assertEqual(self.transit().status_code, 200)

    def test_plan_required_metrics_must_be_finite_and_valid(self) -> None:
        for field in ["duration", "walking_distance"]:
            for value in [None, "", [], {}, False, True, "bad", "NaN", "Infinity", "-1"]:
                with self.subTest(field=field, value=value):
                    proposal = sample_proposal()
                    proposal[field] = value
                    self.set_proposals([proposal])
                    self.assertEqual(self.transit().status_code, 502)
        proposal = sample_proposal()
        proposal["duration"] = "0"
        self.set_proposals([proposal])
        self.assertEqual(self.transit().status_code, 502)
        proposal["duration"] = "100"
        proposal["walking_distance"] = "0"
        self.set_proposals([proposal])
        self.assertEqual(self.transit().status_code, 200)

    def test_invalid_optional_leg_metrics_reject_whole_candidate(self) -> None:
        for leg_type in ["walking", "bus"]:
            for field in ["duration", "distance"]:
                for value in [True, "NaN", "Infinity", "-1", {}, "bad"]:
                    with self.subTest(leg_type=leg_type, field=field, value=value):
                        proposal = sample_proposal()
                        leg = proposal["segments"][0][leg_type]
                        if leg_type == "bus":
                            leg = leg["buslines"][0]
                        leg[field] = value
                        self.set_proposals([proposal])
                        self.assertEqual(self.transit().status_code, 502)

    def test_missing_partial_geometry_preserves_known_parts_and_explains_incompleteness(self) -> None:
        for missing in [None, "", []]:
            with self.subTest(missing=missing):
                proposal = sample_proposal()
                proposal["segments"][0]["walking"]["steps"][0]["polyline"] = missing
                proposal["segments"][0]["bus"]["buslines"][0]["polyline"] = missing
                self.set_proposals([proposal])
                response = self.transit()
                self.assertEqual(response.status_code, 200)
                route = response.json()["route"]
                self.assertFalse(route["geometry_complete"])
                self.assertFalse(route["legs"][0]["geometry_complete"])
                self.assertEqual(len(route["legs"][0]["geometry"]), 1)
                self.assertEqual(route["legs"][1]["geometry"], [])
                self.assertEqual(route["legs"][1]["departure_stop"], "甲站")

    def test_all_geometry_missing_can_still_explain_complete_text_plan(self) -> None:
        proposal = sample_proposal()
        for segment in proposal["segments"]:
            segment["walking"]["steps"] = []
        del proposal["segments"][0]["bus"]["buslines"][0]["polyline"]
        self.set_proposals([proposal])
        response = self.transit()
        self.assertEqual(response.status_code, 200)
        route = response.json()["route"]
        self.assertFalse(route["geometry_complete"])
        self.assertTrue(all(leg["geometry"] == [] for leg in route["legs"]))

    def test_present_malformed_geometry_cannot_be_silently_skipped(self) -> None:
        for geometry in ["bad", {}, "121,31", "121,31;", "121,31;121,31", "181,31;122,32",
                         "121,91;122,32", "NaN,31;122,32", "121,31;122,32,1"]:
            for kind in ["walking", "bus"]:
                with self.subTest(geometry=geometry, kind=kind):
                    proposal = sample_proposal()
                    target = proposal["segments"][0][kind]
                    target = target["steps"][0] if kind == "walking" else target["buslines"][0]
                    target["polyline"] = geometry
                    self.set_proposals([proposal])
                    self.assertEqual(self.transit().status_code, 502)

    def test_missing_ride_name_type_or_stops_is_abnormal_not_a_complete_plan(self) -> None:
        for field, value in [("name", None), ("type", None), ("departure_stop", {}),
                             ("arrival_stop", []), ("arrival_stop", {"name": ""}),
                             ("departure_stop", {"name": 42})]:
            with self.subTest(field=field, value=value):
                proposal = sample_proposal()
                proposal["segments"][0]["bus"]["buslines"][0][field] = value
                self.set_proposals([proposal])
                self.assertEqual(self.transit().status_code, 502)

    def test_empty_transits_is_no_route_not_unsupported(self) -> None:
        self.set_proposals([])
        response = self.transit()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "no_route")
        self.assertIsNone(response.json()["route"])

    def test_unsupported_required_modes_invalidate_entire_proposal(self) -> None:
        for field in ["taxi", "railway", "ferry", "unknown_vehicle"]:
            with self.subTest(field=field):
                proposal = sample_proposal()
                proposal["segments"][0][field] = {"name": "不支持的必要路段"}
                self.set_proposals([proposal])
                response = self.transit()
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["status"], "unsupported")
                self.assertIsNone(response.json()["route"])

    def test_observed_v3_empty_railway_shell_is_not_a_required_train_leg(self) -> None:
        proposal = sample_proposal()
        for segment in proposal["segments"]:
            segment["railway"] = {"via_stops": [], "alters": [], "spaces": []}
            segment["taxi"] = []
        self.set_proposals([proposal])
        response = self.transit()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual([leg["mode"] for leg in response.json()["route"]["legs"]],
                         ["walking", "subway", "walking"])

    def test_real_or_unknown_railway_content_cannot_hide_in_an_empty_shell(self) -> None:
        for railway in [
            {"via_stops": [{"name": "铁路站"}], "alters": [], "spaces": []},
            {"via_stops": [], "alters": [{"name": "铁路方案"}], "spaces": []},
            {"via_stops": [], "alters": [], "spaces": [{"cost": "10"}]},
            {"via_stops": [], "alters": [], "spaces": [], "name": "必要铁路"},
            {"via_stops": [], "alters": [], "spaces": [], "unknown": {}},
            {"via_stops": {}, "alters": [], "spaces": []},
        ]:
            with self.subTest(railway=railway):
                proposal = sample_proposal()
                proposal["segments"][0]["railway"] = railway
                self.set_proposals([proposal])
                self.assertEqual(self.transit().json()["status"], "unsupported")
        proposal = sample_proposal()
        proposal["segments"][0]["unknown_mode"] = {"via_stops": [], "alters": [], "spaces": []}
        self.set_proposals([proposal])
        self.assertEqual(self.transit().json()["status"], "unsupported")

    def test_unsupported_bus_types_are_not_mislabelled_as_bus_or_subway(self) -> None:
        for kind in ["轮渡", "磁悬浮列车", "机场大巴", "长途汽车", "高速铁路", "unknown"]:
            with self.subTest(kind=kind):
                proposal = sample_proposal()
                proposal["segments"][0]["bus"]["buslines"][0]["type"] = kind
                self.set_proposals([proposal])
                self.assertEqual(self.transit().json()["status"], "unsupported")

    def test_pure_walking_is_unsupported_not_a_transit_success(self) -> None:
        proposal = sample_proposal()
        proposal["segments"][0]["bus"] = {"buslines": []}
        self.set_proposals([proposal])
        response = self.transit()
        self.assertEqual(response.json()["status"], "unsupported")
        self.assertIsNone(response.json()["route"])

    def test_no_mixing_across_invalid_proposals(self) -> None:
        first, second = sample_proposal(), sample_proposal()
        first["segments"][0]["bus"]["buslines"][0]["arrival_stop"] = {}
        second["segments"][0]["bus"]["buslines"][0]["departure_stop"] = {}
        self.set_proposals([first, second])
        self.assertEqual(self.transit().status_code, 502)

    def test_malformed_payloads_are_not_reported_as_empty(self) -> None:
        for data in [None, [], {}, {"status": "1"}, {"status": "1", "route": []},
                     {"status": "1", "route": {}}, {"status": "1", "route": {"transits": None}}]:
            with self.subTest(data=data):
                self.response = httpx.Response(200, json=data)
                self.assertEqual(self.transit().status_code, 502)
        for proposal in [None, [], {}, dict(sample_proposal(), segments=[]),
                         dict(sample_proposal(), segments=[None]), dict(sample_proposal(), segments=[{}])]:
            with self.subTest(proposal=proposal):
                self.set_proposals([proposal])
                self.assertEqual(self.transit().status_code, 502)

    def test_invalid_walking_and_bus_structures_fail_safely(self) -> None:
        for field, value in [("walking", "bad"), ("walking", {"steps": {}}),
                             ("walking", {"steps": [None]}), ("bus", "bad"),
                             ("bus", {"buslines": None}), ("bus", {"buslines": [None]})]:
            with self.subTest(field=field, value=value):
                proposal = sample_proposal()
                proposal["segments"][0][field] = value
                self.set_proposals([proposal])
                self.assertEqual(self.transit().status_code, 502)

    def test_same_id_or_serialized_coordinate_avoids_upstream(self) -> None:
        for mode in ["id", "coords", "rounded"]:
            with self.subTest(mode=mode):
                payload = sample_request()
                if mode == "id":
                    payload["destination"]["place_id"] = payload["origin"]["place_id"]
                else:
                    payload["destination"]["longitude"] = payload["origin"]["longitude"]
                    payload["destination"]["latitude"] = payload["origin"]["latitude"]
                    if mode == "rounded":
                        payload["destination"]["longitude"] += 0.0000001
                self.assertEqual(self.transit(payload).json()["status"], "same_place")
        self.assertEqual(self.requests, [])

    def test_strict_endpoint_validation_is_reused(self) -> None:
        payloads = [{}, [], {"origin": None, "destination": None}]
        for field, value in [("place_id", ""), ("place_id", "https://example.com"), ("place_id", True),
                             ("place_id", "a" * 129), ("longitude", "121"), ("latitude", True),
                             ("longitude", 180.01), ("latitude", -90.01), ("latitude", None)]:
            payload = sample_request()
            payload["origin"][field] = value
            payloads.append(payload)
        for payload in payloads:
            with self.subTest(payload=payload):
                response = self.transit(payload)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["detail"], "公交／地铁路线请求参数无效，请检查地点和坐标。")
        self.assertEqual(self.requests, [])

    def test_client_cannot_override_city_target_keys_or_query_conditions(self) -> None:
        for field in ["city", "cityd", "key", "jscode", "url", "upstream", "date", "time", "strategy"]:
            for nested in [True, False]:
                with self.subTest(field=field, nested=nested):
                    payload = sample_request()
                    target = payload["origin"] if nested else payload
                    target[field] = "not-allowed"
                    self.assertEqual(self.transit(payload).status_code, 422)
        self.assertEqual(self.requests, [])

    def test_non_finite_and_malformed_json_yield_safe_422(self) -> None:
        bodies = ["{not valid"]
        for value in [float("nan"), float("inf"), float("-inf")]:
            payload = sample_request()
            payload["origin"]["longitude"] = value
            bodies.append(json.dumps(payload))
        for body in bodies:
            with self.subTest(body=body):
                response = self.client.post("/routes/transit", content=body, headers={"Content-Type": "application/json"})
                self.assertEqual(response.status_code, 422)
                self.assertNotIn("NaN", response.text)
                self.assertNotIn("Infinity", response.text)
        self.assertEqual(self.requests, [])

    def test_upstream_errors_and_non_json_never_expose_sensitive_payload(self) -> None:
        for response in [httpx.Response(200, json={"status": "0", "info": TEST_KEY, "infocode": "10001"}),
                         httpx.Response(200, text=f"not-json {TEST_KEY}")]:
            self.response = response
            result = self.transit()
            self.assertEqual(result.status_code, 502)
            self.assertNotIn(TEST_KEY, result.text)
            self.assertNotIn("10001", result.text)

    def test_sensitive_extra_fields_dropped_and_reflected_key_in_allowed_text_rejected(self) -> None:
        data = sample_upstream()
        data["info"] = TEST_KEY
        data["route"]["secret"] = TEST_KEY
        self.response = httpx.Response(200, json=data)
        response = self.transit()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(TEST_KEY, response.text)
        proposal = sample_proposal()
        proposal["segments"][0]["bus"]["buslines"][0]["name"] = TEST_KEY
        self.set_proposals([proposal])
        response = self.transit()
        self.assertEqual(response.status_code, 502)
        self.assertNotIn(TEST_KEY, response.text)

    def test_redirects_and_http_errors_are_not_followed_or_retried(self) -> None:
        for status in [301, 302, 307, 400, 403, 429, 500]:
            with self.subTest(status=status):
                self.requests.clear()
                self.response = httpx.Response(status, text=TEST_KEY, headers={"Location": "https://example.com"})
                with self.assertLogs("app.integrations.amap_transit", level="WARNING"):
                    response = self.transit()
                self.assertEqual(response.status_code, 502)
                self.assertEqual(len(self.requests), 1)
                self.assertNotIn(TEST_KEY, response.text)

    def test_bounded_retry_only_for_connection_failures_and_logs_are_safe(self) -> None:
        for failure, status, attempts in [
            (httpx.ConnectError, 502, 2), (httpx.ConnectTimeout, 504, 2),
            (httpx.ReadTimeout, 504, 1), (httpx.WriteTimeout, 504, 1), (httpx.PoolTimeout, 504, 1),
            (httpx.ReadError, 502, 1),
        ]:
            with self.subTest(failure=failure):
                self.requests.clear()
                self.failure = failure
                with self.assertLogs("app.integrations.amap_transit", level="WARNING") as logs:
                    response = self.transit()
                self.assertEqual(response.status_code, status)
                self.assertEqual(len(self.requests), attempts)
                output = " ".join(logs.output)
                self.assertIn(failure.__name__, output)
                for secret in [TEST_KEY, "restapi.amap.com", "Sensitive upstream URL"]:
                    self.assertNotIn(secret, output)
                    self.assertNotIn(secret, response.text)

    def test_single_connection_retry_can_recover(self) -> None:
        for failure in [httpx.ConnectError, httpx.ConnectTimeout]:
            with self.subTest(failure=failure):
                self.requests.clear()
                self.failures = [failure, None]
                with self.assertLogs("app.integrations.amap_transit", level="WARNING"):
                    self.assertEqual(self.transit().status_code, 200)
                self.assertEqual(len(self.requests), 2)
                self.assertEqual(self.requests[0].url, self.requests[1].url)

    def test_missing_configuration_is_safe_and_health_still_works(self) -> None:
        self.settings = Settings(_env_file=None, amap_web_key=" ")
        self.assertEqual(self.transit().status_code, 503)
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(self.requests, [])

    def test_http_logs_redact_transit_key_and_cors_is_unchanged(self) -> None:
        with self.assertLogs("httpx", level="INFO") as logs:
            self.assertEqual(self.transit().status_code, 200)
        self.assertNotIn(TEST_KEY, " ".join(logs.output))
        allowed = self.client.options("/routes/transit", headers={
            "Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        })
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.headers["access-control-allow-origin"], "http://localhost:3000")
        disallowed = self.client.options("/routes/transit", headers={
            "Origin": "https://example.com", "Access-Control-Request-Method": "POST",
        })
        self.assertNotIn("access-control-allow-origin", disallowed.headers)


class TransitSchemaTests(unittest.TestCase):
    def test_request_reuses_endpoint_contract_without_arbitrary_options(self) -> None:
        request = TransitRouteRequest.model_validate(sample_request())
        self.assertEqual(request.origin.longitude, 121.4000001)
        with self.assertRaises(ValidationError):
            TransitRouteRequest.model_validate(dict(sample_request(), city="北京"))

    def test_response_status_and_complete_geometry_are_consistent(self) -> None:
        base = {"source": "amap", "queried_at": "2026-10-05T00:00:00Z", "route": None}
        for status in ["no_route", "unsupported", "same_place"]:
            TransitRouteResponse.model_validate(dict(base, status=status))
        with self.assertRaises(ValidationError):
            TransitRouteResponse.model_validate(dict(base, status="ok"))
        with self.assertRaises(ValidationError):
            TransitRouteResponse.model_validate(dict(base, status="no_route", queried_at="2026-10-05"))


if __name__ == "__main__":
    unittest.main()
