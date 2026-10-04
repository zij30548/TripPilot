import copy
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
from app.schemas.route import WalkingRouteRequest, WalkingRouteResponse
from main import app


TEST_KEY = "mock-walking-key-not-a-real-credential"


def sample_request() -> dict[str, object]:
    # Fictional test fixtures; never used for real-service acceptance.
    return {
        "origin": {"place_id": "mock-poi-a", "longitude": 121.4000001, "latitude": 31.2000001},
        "destination": {"place_id": "mock-poi-b", "longitude": 121.4200008, "latitude": 31.2200008},
    }


def sample_upstream() -> dict[str, object]:
    return {
        "status": "1", "count": "1", "info": "OK",
        "route": {"paths": [{
            "distance": "1250.5", "duration": "901",
            "steps": [
                {"polyline": "121.4,31.2;121.401,31.201"},
                # Deliberate gap verifies that steps are not joined by a fake line.
                {"polyline": "121.419,31.219;121.42,31.22"},
            ],
        }]},
    }


class WalkingRouteApiTests(unittest.TestCase):
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
            get_settings: lambda: self.settings,
            get_http_client: mock_http_client,
        }))
        self.client = self.enterContext(TestClient(app))

    def walking(self, payload: object | None = None) -> httpx.Response:
        return self.client.post("/routes/walking", json=sample_request() if payload is None else payload)

    def test_conversion_preserves_meters_seconds_and_independent_segments(self) -> None:
        before = datetime.now(timezone.utc)
        response = self.walking()
        after = datetime.now(timezone.utc)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(set(data), {"status", "source", "queried_at", "route"})
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["source"], "amap")
        queried_at = datetime.fromisoformat(data["queried_at"])
        self.assertEqual(queried_at.utcoffset(), timedelta(0))
        self.assertLessEqual(before, queried_at)
        self.assertLessEqual(queried_at, after)
        self.assertEqual(data["route"], {
            "distance_meters": 1250.5,
            "duration_seconds": 901,
            "segments": [[[121.4, 31.2], [121.401, 31.201]], [[121.419, 31.219], [121.42, 31.22]]],
        })
        WalkingRouteResponse.model_validate(data)
        self.assertNotIn(TEST_KEY, response.text)
        self.assertNotIn("info", data)
        self.assertTrue(all(client.is_closed for client in self.clients))

    def test_fixed_upstream_order_precision_ids_and_timeout(self) -> None:
        self.assertEqual(self.walking().status_code, 200)
        request = self.requests[0]
        self.assertEqual(request.method, "GET")
        self.assertEqual(str(request.url).split("?")[0], "https://restapi.amap.com/v3/direction/walking")
        self.assertEqual(dict(request.url.params), {
            "key": TEST_KEY,
            "origin": "121.400000,31.200000",
            "destination": "121.420001,31.220001",
            "origin_id": "mock-poi-a", "destination_id": "mock-poi-b", "output": "JSON",
        })
        self.assertEqual(request.extensions["timeout"], {
            "connect": 3.0, "read": 10.0, "write": 10.0, "pool": 10.0,
        })

    def test_only_first_returned_proposal_is_selected(self) -> None:
        data = sample_upstream()
        data["route"]["paths"].append({"distance": "99", "duration": "60", "steps": []})
        self.response = httpx.Response(200, json=data)
        response = self.walking()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["route"]["distance_meters"], 1250.5)

    def test_empty_paths_is_explicit_no_route_without_fabricated_values(self) -> None:
        self.response = httpx.Response(200, json={"status": "1", "count": "0", "route": {"paths": []}})
        response = self.walking()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "no_route")
        self.assertIsNone(response.json()["route"])
        self.assertEqual(response.json()["source"], "amap")

    def test_same_id_or_coordinates_does_not_call_upstream(self) -> None:
        for same_field in ["place_id", "coordinates", "rounded_coordinates"]:
            with self.subTest(same_field=same_field):
                payload = sample_request()
                if same_field == "place_id":
                    payload["destination"]["place_id"] = payload["origin"]["place_id"]
                else:
                    payload["destination"]["longitude"] = payload["origin"]["longitude"]
                    payload["destination"]["latitude"] = payload["origin"]["latitude"]
                    if same_field == "rounded_coordinates":
                        payload["destination"]["longitude"] += 0.0000001
                        payload["destination"]["latitude"] += 0.0000001
                response = self.walking(payload)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["status"], "same_place")
                self.assertIsNone(response.json()["route"])
        self.assertEqual(self.requests, [])

    def test_invalid_input_shapes_and_ids_do_not_call_upstream(self) -> None:
        payloads = [{}, [], {"origin": sample_request()["origin"]}, {"origin": None, "destination": None}]
        for field, value in [
            ("place_id", ""), ("place_id", " "), ("place_id", "a" * 129),
            ("place_id", "https://example.com/target"), ("place_id", "a?key=value"),
            ("place_id", True), ("place_id", 123), ("latitude", "31.2"),
            ("longitude", "121.4"), ("latitude", True), ("longitude", False),
            ("latitude", None), ("longitude", []), ("latitude", 90.01),
            ("latitude", -90.01), ("longitude", 180.01), ("longitude", -180.01),
        ]:
            payload = sample_request()
            payload["origin"][field] = value
            payloads.append(payload)
        for payload in payloads:
            with self.subTest(payload=payload):
                response = self.walking(payload)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["detail"], "步行路线请求参数无效，请检查地点和坐标。")
        self.assertEqual(self.requests, [])

    def test_extra_target_key_and_security_code_cannot_be_supplied(self) -> None:
        for field, value in [
            ("url", "https://example.com"), ("upstream", "https://example.com"),
            ("key", "attacker-key"), ("jscode", "attacker-code"), ("mode", "driving"),
        ]:
            for at_endpoint in [True, False]:
                with self.subTest(field=field, at_endpoint=at_endpoint):
                    payload = sample_request()
                    target = payload["origin"] if at_endpoint else payload
                    target[field] = value
                    self.assertEqual(self.walking(payload).status_code, 422)
        self.assertEqual(self.requests, [])

    def test_non_finite_raw_json_is_safe_422_not_a_server_crash(self) -> None:
        for value in [float("nan"), float("inf"), float("-inf")]:
            with self.subTest(value=value):
                payload = sample_request()
                payload["origin"]["longitude"] = value
                response = self.client.post("/routes/walking", content=json.dumps(payload), headers={
                    "Content-Type": "application/json",
                })
                self.assertEqual(response.status_code, 422)
                self.assertNotIn("NaN", response.text)
                self.assertNotIn("Infinity", response.text)
        self.assertEqual(self.requests, [])

    def test_malformed_json_is_safe_422(self) -> None:
        response = self.client.post("/routes/walking", content="{not valid", headers={
            "Content-Type": "application/json",
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.requests, [])

    def test_input_boundaries_accept_numbers_without_coordinate_conversion(self) -> None:
        payload = sample_request()
        payload["origin"] = {"place_id": "mock-min", "longitude": -180, "latitude": -90}
        payload["destination"] = {"place_id": "mock-max", "longitude": 180, "latitude": 90}
        self.assertEqual(self.walking(payload).status_code, 200)
        self.assertEqual(self.requests[0].url.params["origin"], "-180.000000,-90.000000")
        self.assertEqual(self.requests[0].url.params["destination"], "180.000000,90.000000")

    def test_upstream_business_error_does_not_expose_raw_info(self) -> None:
        self.response = httpx.Response(200, json={
            "status": "0", "info": f"sensitive={TEST_KEY}", "infocode": "10001",
        })
        response = self.walking()
        self.assertEqual(response.status_code, 502)
        self.assertNotIn(TEST_KEY, response.text)
        self.assertNotIn("10001", response.text)
        self.assertEqual(len(self.requests), 1)

    def test_redirects_and_http_errors_are_not_followed_or_retried(self) -> None:
        for status in [301, 302, 307, 400, 403, 429, 500]:
            with self.subTest(status=status):
                self.requests.clear()
                self.response = httpx.Response(status, text=TEST_KEY, headers={
                    "Location": "https://example.com/should-not-follow",
                })
                with self.assertLogs("app.integrations.amap_walking", level="WARNING"):
                    response = self.walking()
                self.assertEqual(response.status_code, 502)
                self.assertEqual(len(self.requests), 1)
                self.assertNotIn(TEST_KEY, response.text)

    def test_network_errors_and_timeouts_are_safe_and_bounded(self) -> None:
        for failure, status, attempts in [
            (httpx.ConnectError, 502, 2), (httpx.ConnectTimeout, 504, 2),
            (httpx.ReadTimeout, 504, 1), (httpx.WriteTimeout, 504, 1), (httpx.PoolTimeout, 504, 1),
        ]:
            with self.subTest(failure=failure):
                self.requests.clear()
                self.failure = failure
                with self.assertLogs("app.integrations.amap_walking", level="WARNING") as logs:
                    response = self.walking()
                self.assertEqual(response.status_code, status)
                self.assertEqual(len(self.requests), attempts)
                output = " ".join(logs.output)
                self.assertIn(failure.__name__, output)
                for secret in [TEST_KEY, "restapi.amap.com", "Sensitive upstream URL"]:
                    self.assertNotIn(secret, output)
                    self.assertNotIn(secret, response.text)
                self.assertTrue(all(client.is_closed for client in self.clients))

    def test_one_transient_connection_retry_can_recover(self) -> None:
        for failure in [httpx.ConnectError, httpx.ConnectTimeout]:
            with self.subTest(failure=failure):
                self.requests.clear()
                self.failures = [failure, None]
                with self.assertLogs("app.integrations.amap_walking", level="WARNING"):
                    self.assertEqual(self.walking().status_code, 200)
                self.assertEqual(len(self.requests), 2)
                self.assertEqual(self.requests[0].url, self.requests[1].url)

    def test_missing_key_preserves_health_and_never_calls_upstream(self) -> None:
        for key in ["", "  "]:
            with self.subTest(key=key):
                self.settings = Settings(_env_file=None, amap_web_key=key)
                self.assertEqual(self.walking().status_code, 503)
                self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(self.requests, [])

    def test_malformed_response_shapes_are_not_treated_as_no_route(self) -> None:
        for data in [None, [], {}, {"status": "1"}, {"status": "1", "route": []},
                     {"status": "1", "route": {}}, {"status": "1", "route": {"paths": None}},
                     {"status": "1", "route": {"paths": [None]}}]:
            with self.subTest(data=data):
                self.response = httpx.Response(200, json=data)
                self.assertEqual(self.walking().status_code, 502)
        self.response = httpx.Response(200, text=f"not-json {TEST_KEY}")
        response = self.walking()
        self.assertEqual(response.status_code, 502)
        self.assertNotIn(TEST_KEY, response.text)

    def test_bad_distance_and_duration_fail_without_mock_fallback(self) -> None:
        for field in ["distance", "duration"]:
            for value in [None, "", "bad", [], {}, True, "NaN", "Infinity", "-Infinity", "-1", -1, "0", 0]:
                with self.subTest(field=field, value=value):
                    data = sample_upstream()
                    data["route"]["paths"][0][field] = value
                    self.response = httpx.Response(200, json=data)
                    self.assertEqual(self.walking().status_code, 502)

    def test_bad_geometry_is_rejected_instead_of_skipped_or_fabricated(self) -> None:
        for polyline in [None, "", [], "121.4,31.2", "121.4,31.2;", "121.4,31.2;bad",
                         "121.4,31.2;121.5,31.3,1", "181,31;121.5,31.3", "121,91;121.5,31.3",
                         "-181,31;121.5,31.3", "121,-91;121.5,31.3", "NaN,31;121.5,31.3",
                         "121,Infinity;121.5,31.3", "121.4,31.2;121.4,31.2"]:
            with self.subTest(polyline=polyline):
                data = sample_upstream()
                data["route"]["paths"][0]["steps"] = [{"polyline": polyline}]
                self.response = httpx.Response(200, json=data)
                self.assertEqual(self.walking().status_code, 502)

    def test_documented_walking_distance_limit(self) -> None:
        for distance, status in [(100_000, 200), (100_000.01, 502), (1e100, 502)]:
            with self.subTest(distance=distance):
                data = sample_upstream()
                data["route"]["paths"][0]["distance"] = distance
                self.response = httpx.Response(200, json=data)
                self.assertEqual(self.walking().status_code, status)

    def test_missing_steps_or_polyline_fail_as_abnormal_upstream(self) -> None:
        for steps in [None, [], {}, [None], [{}], [{"polyline": "121,31;121.1,31.1"}, {}]]:
            with self.subTest(steps=steps):
                data = sample_upstream()
                data["route"]["paths"][0]["steps"] = steps
                self.response = httpx.Response(200, json=data)
                self.assertEqual(self.walking().status_code, 502)

    def test_valid_numeric_scalars_work_and_sensitive_extra_fields_are_dropped(self) -> None:
        data = sample_upstream()
        data["route"]["paths"][0]["distance"] = 1250.5
        data["route"]["paths"][0]["duration"] = 901
        data["info"] = TEST_KEY
        data["route"]["paths"][0]["steps"][0]["instruction"] = TEST_KEY
        self.response = httpx.Response(200, json=data)
        response = self.walking()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(TEST_KEY, response.text)

    def test_httpx_log_redaction_applies_to_walking(self) -> None:
        with self.assertLogs("httpx", level="INFO") as logs:
            self.assertEqual(self.walking().status_code, 200)
        output = " ".join(logs.output)
        self.assertIn("[REDACTED]", output)
        self.assertNotIn(TEST_KEY, output)
        self.assertNotIn("origin=", output)

    def test_post_cors_and_other_methods_remain_restricted(self) -> None:
        for origin in ["http://localhost:3000", "http://127.0.0.1:3000"]:
            with self.subTest(origin=origin):
                preflight = self.client.options("/routes/walking", headers={
                    "Origin": origin, "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "Content-Type",
                })
                self.assertEqual(preflight.status_code, 200)
                self.assertEqual(preflight.headers["access-control-allow-origin"], origin)
                response = self.client.post("/routes/walking", json=sample_request(), headers={"Origin": origin})
                self.assertEqual(response.headers["access-control-allow-origin"], origin)
        denied = self.client.options("/routes/walking", headers={
            "Origin": "https://example.com", "Access-Control-Request-Method": "POST",
        })
        self.assertEqual(denied.status_code, 400)
        for method in ["GET", "PUT", "DELETE"]:
            with self.subTest(method=method):
                self.assertEqual(self.client.request(method, "/routes/walking").status_code, 405)

    def test_response_model_disallows_inconsistent_success_and_naive_timestamp(self) -> None:
        valid = self.walking().json()
        for patch_value in [{"status": "ok", "route": None}, {"status": "no_route"},
                            {"queried_at": "2026-10-05T12:00:00"}]:
            with self.subTest(patch_value=patch_value), self.assertRaises(ValidationError):
                WalkingRouteResponse.model_validate(valid | patch_value)

    def test_model_validation_does_not_mutate_input(self) -> None:
        payload = sample_request()
        before = copy.deepcopy(payload)
        WalkingRouteRequest.model_validate(payload)
        self.assertEqual(payload, before)


if __name__ == "__main__":
    unittest.main()
