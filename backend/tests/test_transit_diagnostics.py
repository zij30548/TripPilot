"""Sanitized synthetic data, not a reconstruction of the missing user request."""
import json
import unittest
from collections.abc import AsyncIterator
from unittest.mock import patch

import httpx

from app.integrations.amap_transit import TransitDataError, _convert_route
from tests.test_transit import sample_proposal, sample_upstream, TEST_KEY
from tests import test_schedule_transit


class TransitDiagnosticsTests(unittest.TestCase):
    def test_distinguishes_core_numbers_stops_and_present_invalid_drawing(self) -> None:
        cases = [
            (lambda p: p.update(duration="NaN"), "route.transits[0].duration", "number_range"),
            (lambda p: p["segments"][0]["bus"]["buslines"][0].update(arrival_stop={}),
             "route.transits[0].segments[0].bus.buslines[0].arrival_stop.name", "text_required_or_type"),
            (lambda p: p["segments"][0]["walking"]["steps"][0].update(polyline="NaN,31;121,31"),
             "route.transits[0].segments[0].walking.steps[0].polyline", "coordinate_non_finite"),
        ]
        for mutate, field, reason in cases:
            with self.subTest(field=field):
                data = sample_upstream()
                mutate(data["route"]["transits"][0])
                with self.assertLogs("app.integrations.amap_transit", "WARNING") as logs:
                    with self.assertRaises(TransitDataError) as caught:
                        _convert_route(data)
                self.assertEqual([(i.field, i.reason) for i in caught.exception.issues], [(field, reason)])
                self.assertIn(field, logs.output[0])
                self.assertNotIn("NaN", " ".join(logs.output))

    def test_degenerate_access_geometry_has_safe_incomplete_diagnostic_not_rejection(self) -> None:
        data = sample_upstream()
        data["route"]["transits"][0]["segments"][0]["walking"]["steps"][0]["polyline"] = "121.446312,31.223412;121.446312,31.223412"
        with self.assertLogs("app.integrations.amap_transit", "INFO") as logs:
            status, route = _convert_route(data)
        self.assertEqual(status, "ok")
        self.assertFalse(route.geometry_complete)
        self.assertEqual(len(logs.output), 1)
        self.assertIn("reason=degenerate_access_walk", logs.output[0])
        self.assertIn("route.transits[0].segments[0].walking.steps[0].polyline", logs.output[0])
        self.assertNotIn("121.446312", logs.output[0])
        self.assertNotIn("data rejected", logs.output[0])

    def test_bounded_paths_and_codes_never_include_values_or_unknown_field_names(self) -> None:
        data = sample_upstream()
        secret = "sensitive-key-payload-and-url"
        data[secret] = secret
        proposal = sample_proposal() | {"duration": secret}
        data["route"]["transits"] = [proposal] * 100
        with self.assertLogs("app.integrations.amap_transit", "WARNING") as logs:
            with self.assertRaises(TransitDataError) as caught:
                _convert_route(data)
        self.assertEqual(len(logs.output), 4)
        self.assertEqual(len(caught.exception.issues), 4)
        self.assertLess(len(" ".join(logs.output)), 1000)
        self.assertNotIn(secret, " ".join(logs.output) + str(caught.exception))
        unsupported = sample_proposal()
        unsupported["segments"][0]["taxi"] = {"name": secret}
        data["route"]["transits"] = [unsupported] * 1000 + [proposal]
        with self.assertLogs("app.integrations.amap_transit", "WARNING") as logs:
            with self.assertRaises(TransitDataError) as caught:
                _convert_route(data)
        self.assertEqual(caught.exception.issues[0].field, "route.transits[overflow].duration")
        self.assertNotIn(secret, " ".join(logs.output))

    def test_missing_geometry_is_not_the_same_as_malformed_geometry(self) -> None:
        proposal = sample_proposal()
        for segment in proposal["segments"]:
            segment["walking"]["steps"] = []
        proposal["segments"][0]["bus"]["buslines"][0].pop("polyline")
        data = sample_upstream()
        data["route"]["transits"] = [proposal]
        status, route = _convert_route(data)
        self.assertEqual(status, "ok")
        self.assertFalse(route.geometry_complete)
        self.assertEqual(route.duration_seconds, 1201)
        self.assertTrue(all(leg.geometry == [] for leg in route.legs))

    def test_invalid_first_alternative_does_not_change_valid_selected_proposal(self) -> None:
        invalid, valid = sample_proposal() | {"duration": "bad"}, sample_proposal()
        data = sample_upstream()
        data["route"]["transits"] = [invalid, valid]
        status, route = _convert_route(data)
        self.assertEqual(status, "ok")
        self.assertEqual(route.duration_seconds, 1201)

    def test_collector_retains_exact_direction_and_safe_rejection_reason(self) -> None:
        case = test_schedule_transit.TransitScheduleApiTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        original = case.fixture.response
        import app.api.places as places_api
        async def mock_client() -> AsyncIterator[httpx.AsyncClient]:
            def handle(req: httpx.Request) -> httpx.Response:
                case.fixture.requests.append(req)
                # Only the return edge is invalid; outward remains valid.
                if req.url.params["destination"].startswith("121.400000"):
                    data = sample_upstream()
                    data["route"]["transits"][0]["duration"] = "NaN"
                    return httpx.Response(200, json=data)
                return original
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
                yield client
        from main import app
        with patch.dict(app.dependency_overrides, {places_api.get_http_client: mock_client}):
            with self.assertLogs(level="WARNING") as logs:
                result = case.post().json()
        self.assertEqual(result["edges"][0]["status"], "ok")
        self.assertEqual(result["edges"][1]["status"], "data_error")
        self.assertEqual(result["edges"][1]["origin"]["place_id"], "p1")
        self.assertEqual(result["edges"][1]["destination"]["place_id"], "stay")
        self.assertIn("方案总耗时", result["edges"][1]["message"])
        self.assertEqual(result["unscheduled"][0]["reason"], "route_data_error")
        self.assertFalse(any(day["items"] for day in result["days"]))
        self.assertEqual(len(case.fixture.requests), 2)
        self.assertNotIn(TEST_KEY, json.dumps(result) + " ".join(logs.output))
