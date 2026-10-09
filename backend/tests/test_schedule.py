import asyncio
import json
import math
import os
import unittest
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from time import monotonic
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from app.api.places import get_http_client
from app.config import Settings, get_settings
from app.integrations.amap import AmapError
from app.integrations.amap_walking import AmapWalkingClient, INVALID_DATA
from app.schemas.route import WalkingRoute, WalkingRouteResponse
from app.schemas.schedule import ScheduleEdge, ScheduleRequest, clock_minutes
from app.services.schedule import build_schedule
from app.services.schedule_routes import (
    MIN_ROUTE_START_INTERVAL_SECONDS, ROUTE_BATCH_TIMEOUT_SECONDS, collect_schedule_edges, required_edges,
)
from main import app


NOW = datetime(2026, 10, 6, 10, tzinfo=timezone.utc)
TEST_KEY = "schedule-test-key-not-real"


def place(identifier: str, index: int = 0, **updates: object) -> dict[str, object]:
    return {"id": identifier, "name": f"测试地点-{identifier}", "address": "测试地址",
            "longitude": 121.4 + index * 0.01, "latitude": 31.2,
            "category": "上游原始分类", "source": "amap"} | updates


def payload(count: int = 2, **updates: object) -> dict[str, object]:
    return {
        "start_date": "2026-10-10", "end_date": "2026-10-11",
        "daily_start_time": "09:00", "daily_end_time": "18:00",
        "accommodation_place": place("stay"),
        "must_visit_places": [place(f"p{n}", n) for n in range(1, count + 1)],
        "duration_settings": [{"place_id": f"p{n}", "minutes": 60, "source": "default"}
                              for n in range(1, count + 1)],
        "lunch": {"enabled": True, "start_time": "12:00", "end_time": "13:00"},
    } | updates


def request(count: int = 2, **updates: object) -> ScheduleRequest:
    return ScheduleRequest.model_validate(payload(count, **updates))


def edges_for(req: ScheduleRequest, overrides: dict[tuple[str, str], object] | None = None) -> list[ScheduleEdge]:
    results = []
    for index, (origin, destination) in enumerate(required_edges(req)):
        value = (overrides or {}).get((origin.place_id, destination.place_id), 600.0)
        same = origin.place_id == destination.place_id or (
            origin.longitude == destination.longitude and origin.latitude == destination.latitude
        )
        fields: dict[str, object] = dict(id=f"e{index}", origin=origin, destination=destination, queried_at=NOW)
        if isinstance(value, str):
            fields.update(status=value, message="安全测试错误")
        else:
            seconds = 0 if same else float(value)
            fields.update(status="same_place" if same else "ok", duration_seconds=seconds,
                          duration_minutes=math.ceil(seconds / 60), distance_meters=0 if same else 600,
                          source="same_place" if same else "amap")
        results.append(ScheduleEdge(**fields))
    return results


class PureScheduleTests(unittest.TestCase):
    def preview(self, req: ScheduleRequest, overrides: dict[tuple[str, str], object] | None = None):
        return build_schedule(req, edges_for(req, overrides), NOW)

    def test_complete_original_order_roundtrip_and_empty_requested_days(self) -> None:
        req = request(end_date="2026-10-12")
        result = self.preview(req)
        self.assertEqual(result.status, "complete")
        self.assertEqual([day.date.isoformat() for day in result.days], ["2026-10-10", "2026-10-11", "2026-10-12"])
        items = result.days[0].items
        self.assertEqual([item.kind for item in items], ["walk", "visit", "walk", "visit", "walk"])
        self.assertEqual([item.place_id for item in items if item.kind == "visit"], ["p1", "p2"])
        self.assertEqual(items[0].from_place_id, "stay")
        self.assertEqual(items[-1].to_place_id, "stay")
        self.assertEqual(result.days[0].return_time, "11:30")
        self.assertTrue(all(day.items == [] and day.return_time is None for day in result.days[1:]))

    def test_exact_daily_end_is_accepted(self) -> None:
        result = self.preview(request(1, daily_end_time="10:20", lunch={"enabled": False}))
        self.assertEqual(result.status, "complete")
        self.assertEqual(result.days[0].return_time, "10:20")

    def test_one_minute_over_end_moves_same_place_to_next_day_not_next_poi(self) -> None:
        req = request(2, daily_end_time="11:29", lunch={"enabled": False})
        result = self.preview(req)
        self.assertEqual(result.status, "complete")
        self.assertEqual([[i.place_id for i in day.items if i.kind == "visit"] for day in result.days], [["p1"], ["p2"]])
        self.assertEqual([day.return_time for day in result.days], ["10:20", "10:20"])
        self.assertEqual(result.days[1].items[0].from_place_id, "stay")

    def test_return_is_checked_before_committing_first_visit(self) -> None:
        result = self.preview(request(1, end_date="2026-10-10", daily_end_time="10:19", lunch={"enabled": False}))
        self.assertEqual(result.status, "unscheduled")
        self.assertEqual(result.days[0].items, [])
        self.assertEqual(result.unscheduled[0].reason, "time_window")
        self.assertTrue(all(not edge.used for edge in result.edges))

    def test_previous_return_is_kept_when_next_visit_cannot_fit(self) -> None:
        req = request(3, end_date="2026-10-10", daily_end_time="11:00", lunch={"enabled": False})
        result = self.preview(req)
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.days[0].return_time, "10:20")
        self.assertEqual([u.reason for u in result.unscheduled], ["time_window", "current_order_not_continued"])
        self.assertEqual([u.place_id for u in result.unscheduled], ["p2", "p3"])
        self.assertEqual(result.days[0].items[-1].from_place_id, "p1")

    def test_visit_that_crosses_lunch_waits_and_reserves_whole_lunch(self) -> None:
        result = self.preview(request(3))
        items = result.days[0].items
        lunch = next(item for item in items if item.kind == "lunch")
        wait = next(item for item in items if item.kind == "wait")
        visit = next(item for item in items if item.place_id == "p3")
        self.assertEqual((wait.start_time, wait.end_time), ("11:30", "12:00"))
        self.assertEqual((lunch.start_time, lunch.end_time), ("12:00", "13:00"))
        self.assertEqual((visit.start_time, visit.end_time), ("13:00", "14:00"))

    def test_walk_crossing_lunch_is_delayed_not_split(self) -> None:
        req = request(1, daily_start_time="11:50")
        result = self.preview(req, {("stay", "p1"): 1200})
        self.assertEqual([item.kind for item in result.days[0].items[:3]], ["wait", "lunch", "walk"])
        walk = next(item for item in result.days[0].items if item.kind == "walk")
        self.assertEqual((walk.start_time, walk.end_time, walk.duration_minutes), ("13:00", "13:20", 20))

    def test_return_crossing_lunch_is_delayed(self) -> None:
        result = self.preview(request(1, daily_start_time="10:50"))
        self.assertEqual([item.kind for item in result.days[0].items], ["walk", "visit", "lunch", "walk"])
        self.assertEqual(result.days[0].return_time, "13:10")

    def test_end_at_lunch_start_has_no_fake_lunch(self) -> None:
        result = self.preview(request(1, daily_start_time="10:40"))
        self.assertEqual(result.days[0].return_time, "12:00")
        self.assertFalse(any(item.kind == "lunch" for item in result.days[0].items))

    def test_disabled_lunch_does_not_apply_time_constraints(self) -> None:
        req = request(3, lunch={"enabled": False, "start_time": "22:00", "end_time": "01:00"})
        result = self.preview(req)
        self.assertEqual(result.days[0].return_time, "12:40")
        self.assertFalse(any(item.kind in ("wait", "lunch") for item in result.days[0].items))

    def test_custom_lunch_and_stay_provenance_are_preserved(self) -> None:
        req = request(1, daily_start_time="10:30", lunch={"enabled": True, "start_time": "11:00", "end_time": "11:45"},
                      duration_settings=[{"place_id": "p1", "minutes": 90, "source": "user"}])
        result = self.preview(req)
        visit = next(item for item in result.days[0].items if item.kind == "visit")
        self.assertEqual((visit.start_time, visit.end_time, visit.duration_source), ("11:45", "13:15", "user"))
        self.assertEqual(result.request.duration_settings[0].minutes, 90)

    def test_same_place_keeps_visit_and_zero_walks(self) -> None:
        req = request(1, must_visit_places=[place("stay")],
                      duration_settings=[{"place_id": "stay", "minutes": 60, "source": "default"}])
        result = self.preview(req)
        self.assertEqual([i.duration_minutes for i in result.days[0].items], [0, 60, 0])
        self.assertEqual(result.days[0].return_time, "10:00")
        self.assertEqual(len(result.edges), 1)
        self.assertEqual(result.edges[0].source, "same_place")

    def test_different_ids_same_normalized_coords_are_zero_move(self) -> None:
        req = request(1, must_visit_places=[place("p1", longitude=121.40000001, latitude=31.20000001)])
        result = self.preview(req)
        self.assertTrue(all(edge.status == "same_place" for edge in result.edges))
        self.assertEqual(result.days[0].return_time, "10:00")

    def test_rounding_seconds_up_matches_displayed_minutes(self) -> None:
        result = self.preview(request(1), {("stay", "p1"): 60.01, ("p1", "stay"): 59.99})
        self.assertEqual(result.days[0].items[0].duration_minutes, 2)
        self.assertEqual(result.days[0].items[-1].duration_minutes, 1)
        self.assertEqual(result.days[0].return_time, "10:03")
        self.assertEqual(result.edges[0].duration_seconds, 60.01)

    def test_required_failure_stops_prefix_without_claiming_later_places_impossible(self) -> None:
        for status, reason in [("timeout", "route_timeout"), ("no_route", "no_route"),
                               ("data_error", "route_data_error"), ("failed", "route_failed")]:
            with self.subTest(status=status):
                result = self.preview(request(3), {("p1", "p2"): status})
                self.assertEqual(result.status, "partial")
                self.assertEqual(result.days[0].return_time, "10:20")
                self.assertEqual([u.reason for u in result.unscheduled], [reason, "current_order_not_continued"])
                self.assertEqual(result.days[1].items, [])

    def test_missing_edge_is_data_error_without_fabricated_time(self) -> None:
        req = request(1)
        result = build_schedule(req, [], NOW)
        self.assertEqual(result.status, "unscheduled")
        self.assertEqual(result.unscheduled[0].reason, "route_data_error")
        self.assertEqual(result.days[0].items, [])

    def test_failed_required_return_does_not_commit_visit(self) -> None:
        result = self.preview(request(1), {("p1", "stay"): "no_route"})
        self.assertEqual(result.status, "unscheduled")
        self.assertEqual(result.days[0].items, [])

    def test_unused_failure_does_not_invalidate_complete_result(self) -> None:
        result = self.preview(request(), {("stay", "p2"): "failed"})
        self.assertEqual(result.status, "complete")
        failed = next(edge for edge in result.edges if edge.status == "failed")
        self.assertFalse(failed.used)
        self.assertEqual(result.unscheduled, [])

    def test_known_no_time_moves_days_before_considering_unused_failed_adjacent_edge(self) -> None:
        req = request(daily_end_time="11:00", lunch={"enabled": False})
        result = self.preview(req, {("p1", "p2"): "timeout"})
        self.assertEqual(result.status, "complete")
        self.assertEqual([d.return_time for d in result.days], ["10:20", "10:20"])

    def test_oversized_first_stay_is_not_skipped_and_all_dates_remain_empty(self) -> None:
        req = request(2, end_date="2026-10-12", daily_end_time="11:00", lunch={"enabled": False},
                      duration_settings=[{"place_id": "p1", "minutes": 180, "source": "user"},
                                         {"place_id": "p2", "minutes": 15, "source": "user"}])
        result = self.preview(req)
        self.assertEqual(result.status, "unscheduled")
        self.assertTrue(all(day.items == [] for day in result.days))
        self.assertEqual([u.reason for u in result.unscheduled], ["time_window", "current_order_not_continued"])

    def test_pure_deterministic_and_input_snapshots_unchanged(self) -> None:
        req = request(3)
        edges = edges_for(req)
        before = (req.model_dump_json(), [edge.model_dump_json() for edge in edges])
        first = build_schedule(req, edges, NOW)
        second = build_schedule(req, edges, NOW)
        self.assertEqual(first.model_dump_json(), second.model_dump_json())
        self.assertEqual(before, (req.model_dump_json(), [edge.model_dump_json() for edge in edges]))
        self.assertTrue(all(not edge.used for edge in edges))

    def test_timeline_properties_across_windows_and_stay_sizes(self) -> None:
        for minutes in [15, 60, 120, 240, 480]:
            for day_end in ["13:00", "17:00", "23:59"]:
                req = request(6, end_date="2026-10-12", daily_end_time=day_end,
                              duration_settings=[{"place_id": f"p{n}", "minutes": minutes, "source": "user"} for n in range(1, 7)])
                result = self.preview(req)
                seen = []
                for day in result.days:
                    previous = clock_minutes(req.daily_start_time)
                    for item in day.items:
                        start, end = clock_minutes(item.start_time), clock_minutes(item.end_time)
                        self.assertGreaterEqual(start, previous)
                        self.assertEqual(end - start, item.duration_minutes)
                        self.assertLessEqual(end, clock_minutes(req.daily_end_time))
                        if item.kind in ("walk", "visit") and item.duration_minutes:
                            self.assertTrue(end <= 720 or start >= 780)
                        previous = end
                        if item.kind == "visit":
                            seen.append(item.place_id)
                    if day.items:
                        self.assertEqual(day.items[-1].to_place_id, "stay")
                        self.assertEqual(day.items[-1].end_time, day.return_time)
                self.assertEqual(seen + [u.place_id for u in result.unscheduled], [f"p{n}" for n in range(1, 7)])


class ScheduleApiTests(unittest.TestCase):
    def setUp(self) -> None:
        # Pacing has dedicated controlled-clock coverage below; unrelated API
        # tests need not spend real seconds waiting between fixture requests.
        self.enterContext(patch("app.services.schedule_routes.MIN_ROUTE_START_INTERVAL_SECONDS", 0))
        self.enterContext(patch.dict(os.environ, {}, clear=True))
        self.settings = Settings(_env_file=None, amap_web_key=TEST_KEY)
        self.requests: list[httpx.Request] = []
        self.failures: dict[tuple[str, str], type[httpx.RequestError]] = {}
        self.responses: dict[tuple[str, str], object] = {}
        self.clients: list[httpx.AsyncClient] = []
        self.active = 0
        self.max_active = 0
        self.delay = 0.0

        async def handler(req: httpx.Request) -> httpx.Response:
            self.requests.append(req)
            pair = (req.url.params["origin_id"], req.url.params["destination_id"])
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                await asyncio.sleep(self.delay)
                if pair in self.failures:
                    raise self.failures[pair](f"{TEST_KEY} {req.url}", request=req)
                data = self.responses.get(pair, {"status": "1", "route": {"paths": [{
                    "distance": "600", "duration": "600", "steps": [{"polyline": "121.4,31.2;121.41,31.2"}],
                }]}})
                return httpx.Response(200, json=data)
            finally:
                self.active -= 1

        async def client_dependency() -> AsyncIterator[httpx.AsyncClient]:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                self.clients.append(client)
                yield client

        self.enterContext(patch.dict(app.dependency_overrides, {
            get_settings: lambda: self.settings, get_http_client: client_dependency,
        }))
        self.client = self.enterContext(TestClient(app))

    def post(self, data: dict[str, object] | None = None) -> httpx.Response:
        return self.client.post("/trips/schedule-preview", content=json.dumps(payload() if data is None else data),
                                headers={"Content-Type": "application/json"})

    def test_endpoint_calls_fixed_walking_client_and_echoes_input(self) -> None:
        data = payload()
        response = self.post(data)
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["request"], data | {"optional_places": [], "transport_mode": "walking"})
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(self.requests), 5)
        self.assertEqual(result["edges"][0]["duration_seconds"], 600)
        self.assertEqual(result["edges"][0]["duration_minutes"], 10)
        self.assertEqual(result["edges"][0]["distance_meters"], 600)
        self.assertNotIn("segments", result["edges"][0])
        for req in self.requests:
            self.assertEqual(req.method, "GET")
            self.assertEqual(str(req.url).split("?")[0], "https://restapi.amap.com/v3/direction/walking")
            self.assertEqual(req.url.params["key"], TEST_KEY)
            self.assertEqual(req.extensions["timeout"]["connect"], 3)
        self.assertTrue(all(client.is_closed for client in self.clients))
        self.assertNotIn(TEST_KEY, response.text)

    def test_default_lunch_is_added_to_echo(self) -> None:
        data = payload()
        del data["lunch"]
        response = self.post(data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["request"]["lunch"], {"enabled": True, "start_time": "12:00", "end_time": "13:00"})

    def test_dates_one_to_three_days_and_strict_clock_validation(self) -> None:
        for updates in [{"end_date": "2026-10-09"}, {"end_date": "2026-10-13"}, {"start_date": 0},
                        {"start_date": "2026-10-10T00:00:00"}, {"start_date": "2026-02-30"},
                        {"daily_start_time": "9:00"}, {"daily_end_time": "24:00"},
                        {"daily_start_time": "09:00:30"}, {"daily_start_time": 900},
                        {"daily_start_time": "18:00"}, {"daily_end_time": "08:00"}]:
            with self.subTest(updates=updates):
                self.assertEqual(self.post(payload(**updates)).status_code, 422)
        self.assertEqual(self.requests, [])

    def test_enabled_lunch_requires_inside_daily_window_and_order(self) -> None:
        for lunch in [{"enabled": True, "start_time": "08:00", "end_time": "09:00"},
                      {"enabled": True, "start_time": "17:00", "end_time": "19:00"},
                      {"enabled": True, "start_time": "13:00", "end_time": "12:00"},
                      {"enabled": True, "start_time": "12:00", "end_time": "12:00"},
                      {"enabled": "false"}, {"enabled": True, "start_time": "12:00:00"}]:
            self.assertEqual(self.post(payload(lunch=lunch)).status_code, 422)
        self.assertEqual(self.requests, [])

    def test_stay_settings_have_strict_integer_bounds_source_and_matching_ids(self) -> None:
        for settings in [[], [{"place_id": "p1", "minutes": 14, "source": "user"}],
                         [{"place_id": "p1", "minutes": 481, "source": "user"}],
                         [{"place_id": "p1", "minutes": "60", "source": "default"}],
                         [{"place_id": "p1", "minutes": True, "source": "user"}],
                         [{"place_id": "p1", "minutes": 60.0, "source": "default"}],
                         [{"place_id": "p1", "minutes": 30, "source": "default"}],
                         [{"place_id": "p1", "minutes": 60, "source": "amap"}],
                         [{"place_id": "wrong", "minutes": 60, "source": "default"}],
                         [{"place_id": "p1", "minutes": 60, "source": "default"}] * 2]:
            with self.subTest(settings=settings):
                self.assertEqual(self.post(payload(1, duration_settings=settings)).status_code, 422)
        self.assertEqual(self.requests, [])

    def test_must_cardinality_identity_and_extra_controls_are_rejected(self) -> None:
        invalid = [payload(0), payload(7), payload(must_visit_places=[place("p1", 1), place("p1", 2)]),
                   payload(accommodation_place=None), payload(must_visit_places=None)]
        invalid.extend(payload(**{key: TEST_KEY}) for key in ["key", "city", "upstream_url", "routes", "route_minutes"])
        for data in invalid:
            response = self.post(data)
            self.assertEqual(response.status_code, 422)
            self.assertNotIn(TEST_KEY, response.text)
        self.assertEqual(self.requests, [])

    def test_confirmed_places_coordinates_and_walking_ids_are_strict_safe_422(self) -> None:
        for update in [{"id": "x?key=private"}, {"id": "x" * 129}, {"id": " "}, {"name": ""},
                       {"latitude": "31.2"}, {"latitude": True}, {"longitude": float("nan")},
                       {"latitude": float("inf")}, {"longitude": 181}, {"source": "other"}, {"price": 0}]:
            with self.subTest(update=update):
                response = self.post(payload(accommodation_place=place("stay", **update)))
                self.assertEqual(response.status_code, 422)
                self.assertNotIn("NaN", response.text)
                self.assertNotIn("private", response.text)
        self.assertEqual(self.requests, [])

    def test_same_place_sends_no_upstream_request_but_keeps_stay(self) -> None:
        data = payload(1, must_visit_places=[place("stay", 9)],
                       duration_settings=[{"place_id": "stay", "minutes": 60, "source": "default"}])
        result = self.post(data).json()
        self.assertEqual(result["status"], "complete")
        self.assertEqual(self.requests, [])
        self.assertTrue(all(edge["source"] == "same_place" for edge in result["edges"]))
        self.assertEqual(result["days"][0]["return_time"], "10:00")

    def test_directed_edges_are_not_reversed_or_full_matrix(self) -> None:
        self.post(payload(6))
        pairs = [(r.url.params["origin_id"], r.url.params["destination_id"]) for r in self.requests]
        self.assertEqual(len(pairs), 17)
        self.assertEqual(len(set(pairs)), 17)
        self.assertIn(("stay", "p1"), pairs)
        self.assertIn(("p1", "stay"), pairs)
        self.assertIn(("p1", "p2"), pairs)
        self.assertNotIn(("p2", "p1"), pairs)
        self.assertNotIn(("p1", "p3"), pairs)

    def test_concurrency_is_three_at_most(self) -> None:
        self.delay = 0.01
        self.assertEqual(self.post(payload(6)).status_code, 200)
        self.assertEqual(self.max_active, 3)
        self.assertEqual(self.active, 0)

    def test_connection_retry_budget_is_at_most_thirty_four(self) -> None:
        for a, b in required_edges(request(6)):
            self.failures[(a.place_id, b.place_id)] = httpx.ConnectError
        with self.assertLogs("app.integrations.amap_walking", level="WARNING") as logs:
            response = self.post(payload(6))
        self.assertEqual(len(self.requests), 34)
        self.assertEqual(response.json()["status"], "unscheduled")
        self.assertNotIn(TEST_KEY, response.text + " ".join(logs.output))
        self.assertNotIn("restapi.amap.com", response.text + " ".join(logs.output))

    def test_business_failure_invalid_data_no_route_timeout_are_distinct_and_safe(self) -> None:
        cases = [({"status": "0", "info": TEST_KEY}, "failed", "route_failed"),
                 ({"status": "1", "route": {"paths": []}}, "no_route", "no_route"),
                 ({"status": "1", "route": {"paths": [{"duration": "NaN"}]}}, "data_error", "route_data_error")]
        for data, status, reason in cases:
            with self.subTest(status=status):
                self.responses[("stay", "p1")] = data
                self.requests.clear()
                response = self.post(payload(1))
                result = response.json()
                self.assertEqual(result["edges"][0]["status"], status)
                self.assertEqual(result["unscheduled"][0]["reason"], reason)
                self.assertEqual(len(self.requests), 2)
                self.assertNotIn(TEST_KEY, response.text)
        self.responses.clear()
        self.failures[("stay", "p1")] = httpx.ReadTimeout
        self.requests.clear()
        result = self.post(payload(1)).json()
        self.assertEqual(result["edges"][0]["status"], "timeout")
        self.assertEqual(result["unscheduled"][0]["reason"], "route_timeout")
        self.assertEqual(len(self.requests), 2)

    def test_missing_key_and_malformed_json_are_safe(self) -> None:
        self.settings = Settings(_env_file=None, amap_web_key="")
        self.assertEqual(self.post().status_code, 503)
        self.settings = Settings(_env_file=None, amap_web_key=TEST_KEY)
        response = self.client.post("/trips/schedule-preview", content='{"bad":', headers={"Content-Type": "application/json"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.requests, [])

    def test_existing_cors_is_reused_without_widening(self) -> None:
        for origin, expected in [("http://localhost:3000", 200), ("http://127.0.0.1:3000", 200),
                                 ("https://untrusted.example", 400)]:
            response = self.client.options("/trips/schedule-preview", headers={
                "Origin": origin, "Access-Control-Request-Method": "POST",
            })
            self.assertEqual(response.status_code, expected)


class ScheduleBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.enterContext(patch("app.services.schedule_routes.MIN_ROUTE_START_INTERVAL_SECONDS", 0))

    async def test_twenty_second_search_budget_includes_queue_and_cleans_all_tasks(self) -> None:
        self.assertEqual(ROUTE_BATCH_TIMEOUT_SECONDS, 20.0)
        started = []
        cancelled = []

        async def walking(req):
            started.append(req)
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.append(req)
                raise

        amap = AsyncMock(spec=AmapWalkingClient)
        amap.walking.side_effect = walking
        before = monotonic()
        with patch("app.services.schedule_routes.ROUTE_BATCH_TIMEOUT_SECONDS", 0.035):
            result = await collect_schedule_edges(request(6), amap)
        self.assertLess(monotonic() - before, 0.5)
        self.assertEqual(len(started), 3)
        self.assertEqual(len(cancelled), 3)
        self.assertEqual(len(result), 17)
        self.assertEqual(sum(edge.status == "timeout" for edge in result), 3)
        self.assertEqual(sum(edge.status == "budget_exhausted" for edge in result), 14)
        self.assertEqual(len(asyncio.all_tasks()), 1)

    async def test_parent_cancellation_drains_child_requests(self) -> None:
        entered = asyncio.Event()
        cancelled = []

        async def walking(req):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.append(req)
                raise

        amap = AsyncMock(spec=AmapWalkingClient)
        amap.walking.side_effect = walking
        task = asyncio.create_task(collect_schedule_edges(request(6), amap))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(len(cancelled), 3)
        self.assertEqual(len(asyncio.all_tasks()), 1)

    async def test_same_normalized_point_avoids_client_call(self) -> None:
        req = request(1, must_visit_places=[place("p1", longitude=121.40000001, latitude=31.2)])
        amap = AsyncMock(spec=AmapWalkingClient)
        results = await collect_schedule_edges(req, amap)
        self.assertTrue(all(edge.status == "same_place" for edge in results))
        amap.walking.assert_not_called()

    async def test_data_error_sentinel_and_unexpected_failure_do_not_leak(self) -> None:
        amap = AsyncMock(spec=AmapWalkingClient)
        amap.walking.side_effect = [AmapError(INVALID_DATA), RuntimeError(TEST_KEY)]
        results = await collect_schedule_edges(request(1), amap)
        self.assertEqual([edge.status for edge in results], ["data_error", "failed"])
        self.assertNotIn(TEST_KEY, " ".join(edge.model_dump_json() for edge in results))


class SchedulePacingTests(unittest.IsolatedAsyncioTestCase):
    async def test_fast_routes_are_paced_to_avoid_controlled_qps_failure(self) -> None:
        self.assertEqual(MIN_ROUTE_START_INTERVAL_SECONDS, 0.4)
        starts: list[float] = []

        async def walking(req):
            now = asyncio.get_running_loop().time()
            if starts and now - starts[-1] < 0.009:
                raise AmapError("controlled rate limit")
            starts.append(now)
            return WalkingRouteResponse(status="ok", queried_at=NOW, route=WalkingRoute(
                distance_meters=600, duration_seconds=600,
                segments=[[(121.4, 31.2), (121.41, 31.2)]],
            ))

        amap = AsyncMock(spec=AmapWalkingClient)
        amap.walking.side_effect = walking
        with patch("app.services.schedule_routes.MIN_ROUTE_START_INTERVAL_SECONDS", 0.01):
            results = await collect_schedule_edges(request(2), amap)
        self.assertEqual(len(starts), 5)
        self.assertTrue(all(edge.status == "ok" for edge in results))
        self.assertTrue(all(b - a >= 0.009 for a, b in zip(starts, starts[1:])))
        self.assertEqual(amap.walking.call_count, 5)  # no extra retry at this layer

    async def test_deadline_cancels_pacing_sleep_running_and_queued_tasks(self) -> None:
        started = []
        cancelled = []

        async def walking(req):
            started.append(req)
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.append(req)
                raise

        amap = AsyncMock(spec=AmapWalkingClient)
        amap.walking.side_effect = walking
        with patch("app.services.schedule_routes.MIN_ROUTE_START_INTERVAL_SECONDS", 1), \
                patch("app.services.schedule_routes.ROUTE_BATCH_TIMEOUT_SECONDS", 0.025):
            result = await collect_schedule_edges(request(6), amap)
        self.assertEqual(len(started), 1)
        self.assertEqual(len(cancelled), 1)
        self.assertEqual(len(result), 17)
        self.assertEqual(sum(edge.status == "timeout" for edge in result), 1)
        self.assertEqual(sum(edge.status == "budget_exhausted" for edge in result), 16)
        self.assertEqual(len(asyncio.all_tasks()), 1)

    async def test_parent_cancel_while_pacer_waits_drains_everything(self) -> None:
        entered = asyncio.Event()

        async def walking(req):
            entered.set()
            await asyncio.Event().wait()

        amap = AsyncMock(spec=AmapWalkingClient)
        amap.walking.side_effect = walking
        with patch("app.services.schedule_routes.MIN_ROUTE_START_INTERVAL_SECONDS", 1):
            task = asyncio.create_task(collect_schedule_edges(request(6), amap))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(amap.walking.call_count, 1)
        self.assertEqual(len(asyncio.all_tasks()), 1)

    async def test_same_place_edges_bypass_pacer_and_do_not_consume_a_start(self) -> None:
        req = request(1, must_visit_places=[place("p1", longitude=121.4, latitude=31.2)])
        amap = AsyncMock(spec=AmapWalkingClient)
        with patch("app.services.schedule_routes.asyncio.sleep", new_callable=AsyncMock) as sleep:
            results = await collect_schedule_edges(req, amap)
        self.assertTrue(all(edge.status == "same_place" for edge in results))
        amap.walking.assert_not_called()
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
