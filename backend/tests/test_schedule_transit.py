"""Controlled fixtures only; no live AMap quota or credentials."""

import asyncio
import math
import unittest
from collections import Counter
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import httpx
from pydantic import ValidationError

from app.api.places import get_http_client
from app.integrations.amap import AmapError, AmapTimeoutError
from app.integrations.amap_transit import AmapTransitClient, INVALID_DATA, _proposal
from app.integrations.amap_walking import AmapWalkingClient
from app.schemas.schedule import ScheduleEdge, ScheduleRequest
from app.schemas.transit import TransitRouteResponse
from app.services.schedule import add_optional_schedule, build_required_schedule, build_schedule
from app.services.schedule_routes import (
    ScheduleRouteCollector, collect_schedule_edges, edge_key, optional_edges, required_edges, unique_pairs,
)
from main import app
from tests import test_schedule as fixtures
from tests import test_schedule_optional as optional
from tests import test_transit as transit


NOW = fixtures.NOW


def route(seconds: float = 600) -> TransitRouteResponse:
    proposal = transit.sample_proposal()
    proposal["duration"] = seconds
    proposal["cost"] = []
    proposal["segments"][0]["bus"]["buslines"][0]["duration"] = []
    proposal["segments"][0]["bus"]["buslines"][0]["polyline"] = []
    return TransitRouteResponse(status="ok", queried_at=NOW, route=_proposal(proposal))


def request(required: int = 1, count: int = 2, **updates: object) -> ScheduleRequest:
    return optional.request(required, count, transport_mode="transit", **updates)


def edges_for(req: ScheduleRequest, overrides: dict[tuple[str, str], object] | None = None) -> list[ScheduleEdge]:
    def make(index, origin, destination):
        value = (overrides or {}).get((origin.place_id, destination.place_id), 600)
        common = dict(id=f"e{index}", origin=origin, destination=destination,
                      transport_mode="transit", queried_at=NOW)
        if isinstance(value, str):
            return ScheduleEdge(**common, status=value, message="受控测试原因")
        same = origin.place_id == destination.place_id or (
            origin.longitude == destination.longitude and origin.latitude == destination.latitude
        )
        if same:
            return ScheduleEdge(**common, status="same_place", source="same_place",
                                duration_seconds=0, duration_minutes=0)
        return ScheduleEdge(**common, status="ok", duration_seconds=value,
                            duration_minutes=math.ceil(value / 60), transit_route=route(value).route,
                            selection_rule="first_supported_complete")

    pairs = required_edges(req)
    edges = [make(i, a, b) for i, (a, b) in enumerate(pairs)]
    baseline = build_required_schedule(req, edges, NOW)
    pairs = unique_pairs(pairs + optional_edges(req, baseline.days))
    return [make(i, a, b) for i, (a, b) in enumerate(pairs)]


class TransitSchedulePureTests(unittest.TestCase):
    def test_total_duration_is_counted_once_unknown_legs_fare_and_geometry_survive(self) -> None:
        req = request(1, 0)
        result = build_schedule(req, edges_for(req, {("stay", "p1"): 1201}), NOW)
        self.assertEqual([i.kind for i in result.days[0].items], ["transit", "visit", "transit"])
        self.assertEqual(result.days[0].items[0].duration_minutes, 21)
        self.assertEqual(result.days[0].return_time, "10:31")
        first = result.edges[0]
        self.assertEqual(first.duration_seconds, 1201)
        self.assertIsNone(first.distance_meters)
        self.assertEqual(first.transit_route.walking_distance_meters, 501)
        self.assertIsNone(first.transit_route.fare_cny)
        self.assertIsNone(first.transit_route.legs[1].duration_seconds)
        self.assertFalse(first.transit_route.geometry_complete)
        self.assertEqual(first.selection_rule, "first_supported_complete")
        self.assertTrue(all(e.used for e in result.edges))
        self.assertTrue(any("尚未验证旅行日期" in text for text in result.unknowns))

    def test_complete_traffic_block_is_delayed_past_lunch_without_splitting_legs(self) -> None:
        req = request(1, 0, daily_start_time="11:50")
        result = build_schedule(req, edges_for(req, {("stay", "p1"): 1201}), NOW)
        self.assertEqual([i.kind for i in result.days[0].items[:3]], ["wait", "lunch", "transit"])
        self.assertEqual((result.days[0].items[2].start_time, result.days[0].items[2].end_time), ("13:00", "13:21"))

    def test_same_place_keeps_visit_without_fake_line_fare_or_distance(self) -> None:
        req = request(1, 0, must_visit_places=[fixtures.place("stay")],
                      duration_settings=[{"place_id": "stay", "minutes": 60, "source": "default"}])
        result = build_schedule(req, edges_for(req), NOW)
        self.assertEqual([i.duration_minutes for i in result.days[0].items], [0, 60, 0])
        self.assertTrue(all(e.transit_route is None and e.distance_meters is None and e.selection_rule is None for e in result.edges))
        self.assertEqual(result.days[0].return_time, "10:00")

    def test_required_failure_preserves_verified_prefix_return_and_all_remaining_ids(self) -> None:
        reasons = {"unsupported": "route_unsupported", "budget_exhausted": "route_budget_exhausted",
                   "timeout": "route_timeout", "no_route": "no_route", "data_error": "route_data_error", "failed": "route_failed"}
        for status, reason in reasons.items():
            with self.subTest(status=status):
                req = request(3, 1)
                result = build_schedule(req, edges_for(req, {("p1", "p2"): status}), NOW)
                self.assertEqual(result.days[0].return_time, "10:20")
                self.assertEqual(result.days[0].items[-1].to_place_id, "stay")
                self.assertEqual([i.reason for i in result.unscheduled], [reason, "current_order_not_continued"])
                self.assertEqual(result.optional_results[0].not_attempted_reason, "must_incomplete")

    def test_optional_failure_tries_next_then_later_day_without_changing_required_visits(self) -> None:
        req = request(2, 2)
        edges = edges_for(req, {("p2", "o1"): "unsupported"})
        baseline = build_required_schedule(req, edges, NOW)
        result = add_optional_schedule(req, baseline, edges)
        before = [(d.date, i.model_dump()) for d in baseline.days for i in d.items if i.kind == "visit"]
        after = [(d.date, i.model_dump()) for d in result.days for i in d.items if i.kind == "visit" and i.place_id in {"p1", "p2"}]
        self.assertEqual(before, after)
        self.assertEqual([a.outcome for a in result.optional_results[0].attempts], ["unsupported", "scheduled"])
        self.assertEqual(result.optional_results[1].scheduled_date, req.start_date)
        used = {i.edge_id for d in result.days for i in d.items if i.kind == "transit"}
        self.assertEqual(used, {e.id for e in result.edges if e.used})

    def test_failed_optional_keeps_exact_baseline_with_lunch_in_old_return(self) -> None:
        req = request(1, 1, daily_start_time="10:40")
        edges = edges_for(req, {("p1", "stay"): 1200, ("p1", "o1"): "unsupported", ("stay", "o1"): "unsupported"})
        baseline = build_required_schedule(req, edges, NOW)
        result = add_optional_schedule(req, baseline, edges)
        self.assertEqual(result.days, baseline.days)
        self.assertEqual([i.kind for i in result.days[0].items], ["transit", "visit", "wait", "lunch", "transit"])

    def test_old_return_lunch_recomputed_and_prefix_lunch_never_duplicated(self) -> None:
        for count, start in [(1, "10:40"), (3, "09:00")]:
            req = request(count, 1, daily_start_time=start)
            edges = edges_for(req, {("p1", "stay"): 1200})
            baseline = build_required_schedule(req, edges, NOW)
            result = add_optional_schedule(req, baseline, edges)
            last_visit = max(i for i, item in enumerate(baseline.days[0].items) if item.kind == "visit")
            self.assertEqual(result.days[0].items[:last_visit + 1], baseline.days[0].items[:last_visit + 1])
            self.assertEqual(sum(i.kind == "lunch" for i in result.days[0].items), 1)

    def test_optional_only_actual_three_dates_and_exact_return_boundary(self) -> None:
        req = request(0, 3, end_date="2026-10-12", daily_end_time="10:20", lunch={"enabled": False})
        result = build_schedule(req, edges_for(req), NOW)
        self.assertEqual(result.status, "complete")
        self.assertEqual([d.date.isoformat() for d in result.days], ["2026-10-10", "2026-10-11", "2026-10-12"])
        self.assertEqual([d.return_time for d in result.days], ["10:20"] * 3)
        self.assertEqual([[i.place_id for i in d.items if i.kind == "visit"] for d in result.days], [["o1"], ["o2"], ["o3"]])

    def test_cross_mode_edges_or_baseline_are_rejected_by_pure_functions(self) -> None:
        walking_req = fixtures.request(1)
        req = request(1, 0)
        with self.assertRaises(ValueError):
            build_required_schedule(req, fixtures.edges_for(walking_req), NOW)
        with self.assertRaises(ValueError):
            add_optional_schedule(req, build_required_schedule(walking_req, fixtures.edges_for(walking_req), NOW), edges_for(req))

    def test_edge_shape_cannot_mix_units_modes_or_failed_estimates(self) -> None:
        edge = edges_for(request(1, 0))[0].model_dump()
        for change in [{"transport_mode": "walking"}, {"distance_meters": 501}, {"duration_minutes": 11},
                       {"duration_seconds": 601}, {"transit_route": None}, {"selection_rule": None},
                       {"status": "failed"}, {"source": "same_place"}]:
            with self.subTest(change=change), self.assertRaises(ValidationError):
                ScheduleEdge.model_validate(edge | change)


class TransitScheduleApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = transit.TransitRouteApiTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.enterContext(patch("app.services.schedule_routes.MIN_ROUTE_START_INTERVAL_SECONDS", 0))

    def post(self, data: dict | None = None) -> httpx.Response:
        return self.fixture.client.post("/trips/schedule-preview", json=data or optional.payload(1, 0, transport_mode="transit"))

    def test_only_fixed_transit_adapter_called_and_selected_reference_details_retained(self) -> None:
        with patch.object(AmapWalkingClient, "walking", side_effect=AssertionError("must never call walking")):
            response = self.post()
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["request"]["transport_mode"], "transit")
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["edges"][0]["duration_seconds"], 1201)
        self.assertEqual(result["edges"][0]["duration_minutes"], 21)
        self.assertEqual(result["edges"][0]["transit_route"]["fare_cny"], 4.5)
        for req in self.fixture.requests:
            self.assertEqual(req.url.path, "/v3/direction/transit/integrated")
            self.assertEqual(req.url.params["city"], "上海")
            self.assertEqual(req.url.params["cityd"], "上海")
            self.assertNotIn("date", req.url.params)
            self.assertNotIn("time", req.url.params)
        self.assertNotIn(transit.TEST_KEY, response.text)

    def test_mode_invalid_and_extra_upstream_or_departure_controls_rejected(self) -> None:
        for change in [{"transport_mode": "bus"}, {"transport_mode": None}, {"transport_mode": True},
                       {"transport_mode": ["walking", "transit"]}, {"upstream_url": "secret"},
                       {"key": "secret"}, {"departure_time": "09:00"}]:
            with self.subTest(change=change):
                response = self.post(optional.payload(1, 0, transport_mode="transit") | change)
                self.assertEqual(response.status_code, 422)
                self.assertNotIn("secret", response.text)
        self.assertEqual(self.fixture.requests, [])

    def test_unknown_fare_leg_duration_and_partial_geometry_do_not_block_schedule(self) -> None:
        proposal = transit.sample_proposal()
        proposal["cost"] = []
        line = proposal["segments"][0]["bus"]["buslines"][0]
        line["duration"] = []
        line["polyline"] = []
        self.fixture.set_proposals([proposal])
        result = self.post().json()
        self.assertEqual(result["status"], "complete")
        edge = result["edges"][0]
        self.assertIsNone(edge["distance_meters"])
        self.assertIsNone(edge["transit_route"]["fare_cny"])
        self.assertIsNone(edge["transit_route"]["legs"][1]["duration_seconds"])
        self.assertFalse(edge["transit_route"]["geometry_complete"])
        self.assertEqual(edge["duration_minutes"], 21)

    def test_no_route_unsupported_data_error_failure_and_timeout_are_distinct_without_fallback(self) -> None:
        unsupported = transit.sample_proposal()
        unsupported["segments"][0]["railway"] = {"name": "unsupported required railway"}
        malformed = transit.sample_proposal() | {"duration": "NaN"}
        cases = [([], "no_route", "no_route"), ([unsupported], "unsupported", "route_unsupported"),
                 ([malformed], "data_error", "route_data_error")]
        for proposals, status, reason in cases:
            with self.subTest(status=status):
                self.fixture.set_proposals(proposals)
                with patch.object(AmapWalkingClient, "walking", side_effect=AssertionError("no fallback")):
                    result = self.post(optional.payload(1, 2, transport_mode="transit")).json()
                self.assertEqual(result["edges"][0]["status"], status)
                self.assertEqual(result["unscheduled"][0]["reason"], reason)
                self.assertEqual(len(result["edges"]), 2)
                self.assertTrue(all(o["not_attempted_reason"] == "must_incomplete" for o in result["optional_results"]))
        self.fixture.response = httpx.Response(200, json={"status": "0", "info": transit.TEST_KEY})
        failed = self.post()
        self.assertEqual(failed.json()["edges"][0]["status"], "failed")
        self.assertNotIn(transit.TEST_KEY, failed.text)
        self.fixture.failure = httpx.ReadTimeout
        self.fixture.requests.clear()
        with self.assertLogs("app.integrations.amap_transit", level="WARNING") as logs:
            timeout = self.post()
        self.assertEqual(timeout.json()["edges"][0]["status"], "timeout")
        self.assertEqual(len(self.fixture.requests), 2)  # one per edge, no read retry
        self.assertNotIn(transit.TEST_KEY, timeout.text + " ".join(logs.output))

    def test_same_place_transit_no_adapter_and_no_fake_ride(self) -> None:
        data = optional.payload(1, 0, transport_mode="transit", must_visit_places=[fixtures.place("stay")],
                                duration_settings=[{"place_id": "stay", "minutes": 60, "source": "default"}])
        with patch.object(AmapTransitClient, "transit", side_effect=AssertionError("same place")):
            result = self.post(data).json()
        self.assertEqual(result["status"], "complete")
        self.assertEqual(self.fixture.requests, [])
        self.assertEqual(result["edges"][0]["status"], "same_place")
        self.assertIsNone(result["edges"][0]["transit_route"])
        self.assertEqual([i["kind"] for i in result["days"][0]["items"]], ["transit", "visit", "transit"])

    def test_transit_seventeen_thirtyfour_and_twenty_nine_fiftyeight_budgets(self) -> None:
        for optional_count, expected in [(0, 17), (3, 29)]:
            calls = Counter()
            proposal = transit.sample_proposal() | {"duration": "600"}

            def handler(req: httpx.Request) -> httpx.Response:
                pair = (req.url.params["origin"], req.url.params["destination"])
                calls[pair] += 1
                if calls[pair] == 1:
                    raise httpx.ConnectError("controlled", request=req)
                return httpx.Response(200, json={"status": "1", "route": {"transits": [proposal]}})

            async def dependency() -> AsyncIterator[httpx.AsyncClient]:
                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                    yield client

            data = optional.payload(6, optional_count, transport_mode="transit", end_date="2026-10-12",
                                    daily_end_time="12:00", lunch={"enabled": False})
            data["duration_settings"] = [setting | ({"minutes": 15, "source": "user"} if setting["place_id"].startswith("o") else {})
                                         for setting in data["duration_settings"]]
            with patch.dict(app.dependency_overrides, {get_http_client: dependency}), \
                    self.assertLogs("app.integrations.amap_transit", level="WARNING"):
                response = self.post(data)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "complete")
            self.assertEqual(len(response.json()["edges"]), expected)
            self.assertEqual(len(calls), expected)
            self.assertEqual(sum(calls.values()), expected * 2)
            self.assertTrue(all(count == 2 for count in calls.values()))


class TransitScheduleCollectorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.enterContext(patch("app.services.schedule_routes.MIN_ROUTE_START_INTERVAL_SECONDS", 0))

    async def test_mode_is_in_directed_memo_and_result_reused_across_phases(self) -> None:
        req = request(1, 0)
        pairs = required_edges(req)
        self.assertNotEqual(edge_key(*pairs[0], "walking"), edge_key(*pairs[0], "transit"))
        self.assertNotEqual(edge_key(*pairs[0], "transit"), edge_key(*pairs[1], "transit"))
        client = AsyncMock(spec=AmapTransitClient)
        client.transit.return_value = route()
        collector = ScheduleRouteCollector(client, "transit")
        first = await collector.collect(pairs)
        second = await collector.collect(pairs * 2)
        self.assertEqual(first, second)
        self.assertEqual(client.transit.call_count, 2)
        self.assertTrue(all(key[0] == "transit" for key in collector.indices))

    async def test_two_phases_share_deadline_no_optional_starts_after_expiry(self) -> None:
        client = AsyncMock(spec=AmapTransitClient)
        client.transit.return_value = route()
        req = request(1, 2)
        with patch("app.services.schedule_routes.ROUTE_BATCH_TIMEOUT_SECONDS", 0.02):
            collector = ScheduleRouteCollector(client, "transit")
        first = await collector.collect(required_edges(req))
        baseline = build_required_schedule(req, first, NOW)
        deadline = collector.deadline
        await asyncio.sleep(0.025)
        all_edges = await collector.collect(optional_edges(req, baseline.days))
        self.assertEqual(deadline, collector.deadline)
        self.assertEqual(client.transit.call_count, 2)
        self.assertTrue(all(edge.status == "budget_exhausted" for edge in all_edges[2:]))
        result = add_optional_schedule(req, baseline, all_edges)
        self.assertEqual(result.days, baseline.days)
        self.assertTrue(all(a.outcome == "budget_exhausted" for r in result.optional_results for a in r.attempts))

    async def test_transit_concurrency_deadline_cancels_and_drains_with_correct_outcomes(self) -> None:
        entered, cancelled = [], []

        async def query(req):
            entered.append(req)
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.append(req)
                raise

        client = AsyncMock(spec=AmapTransitClient)
        client.transit.side_effect = query
        with patch("app.services.schedule_routes.ROUTE_BATCH_TIMEOUT_SECONDS", 0.025):
            results = await collect_schedule_edges(request(6, 0), client)
        self.assertEqual(len(entered), 3)
        self.assertEqual(len(cancelled), 3)
        self.assertEqual(sum(e.status == "timeout" for e in results), 3)
        self.assertEqual(sum(e.status == "budget_exhausted" for e in results), 14)
        self.assertEqual(len(asyncio.all_tasks()), 1)

    async def test_pacer_and_parent_cancellation_apply_to_transit_without_new_retry_layer(self) -> None:
        starts = []

        async def query(req):
            starts.append(asyncio.get_running_loop().time())
            return route()

        client = AsyncMock(spec=AmapTransitClient)
        client.transit.side_effect = query
        pairs = required_edges(request(1, 0))
        with patch("app.services.schedule_routes.MIN_ROUTE_START_INTERVAL_SECONDS", 0.012):
            collector = ScheduleRouteCollector(client, "transit")
            await collector.collect(pairs[:1])
            await collector.collect(pairs[1:])
        self.assertGreaterEqual(starts[1] - starts[0], 0.011)
        self.assertEqual(client.transit.call_count, 2)
        entered = asyncio.Event()

        async def wait(req):
            entered.set()
            await asyncio.Event().wait()

        client.transit.side_effect = wait
        with patch("app.services.schedule_routes.MIN_ROUTE_START_INTERVAL_SECONDS", 1):
            task = asyncio.create_task(collect_schedule_edges(request(6, 0), client))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(len(asyncio.all_tasks()), 1)

    async def test_safe_errors_no_mode_fallback_and_total_data_failures(self) -> None:
        client = AsyncMock(spec=AmapTransitClient)
        for error, expected in [(AmapError(INVALID_DATA), "data_error"), (AmapTimeoutError("private"), "timeout"),
                                (AmapError("private"), "failed"), (RuntimeError("private"), "failed")]:
            client.transit.side_effect = error
            results = await collect_schedule_edges(request(1, 0), client)
            self.assertTrue(all(e.status == expected for e in results))
            self.assertTrue(all("private" not in e.model_dump_json() for e in results))

    async def test_same_coordinate_transit_bypasses_expired_budget_and_has_no_upstream_query(self) -> None:
        req = request(0, 1, optional_places=[fixtures.place("o1", longitude=121.4, latitude=31.2)])
        client = AsyncMock(spec=AmapTransitClient)
        collector = ScheduleRouteCollector(client, "transit")
        collector.deadline = 0
        baseline = build_required_schedule(req, [], NOW)
        results = await collector.collect(optional_edges(req, baseline.days))
        self.assertTrue(all(e.status == "same_place" and e.distance_meters is None for e in results))
        client.transit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
