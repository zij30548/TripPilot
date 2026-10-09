import asyncio
import math
import unittest
from collections import Counter
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import httpx

from app.api.places import get_http_client
from app.integrations.amap_walking import AmapWalkingClient
from app.schemas.route import WalkingRoute, WalkingRouteResponse
from app.schemas.schedule import ScheduleEdge, ScheduleRequest
from app.services.schedule import add_optional_schedule, build_required_schedule, build_schedule
from app.services.schedule_routes import (
    MAX_LOGICAL_EDGES, ScheduleRouteCollector, optional_edges, required_edges, unique_pairs,
)
from main import app
from tests import test_schedule as fixtures


NOW = fixtures.NOW


def payload(required: int = 1, optional: int = 2, **updates: object) -> dict[str, object]:
    data = fixtures.payload(required)
    places = [fixtures.place(f"o{i}", 10 + i) for i in range(1, optional + 1)]
    data["optional_places"] = places
    data["duration_settings"] += [{"place_id": place["id"], "minutes": 60, "source": "default"} for place in places]
    return data | updates


def request(required: int = 1, optional: int = 2, **updates: object) -> ScheduleRequest:
    return ScheduleRequest.model_validate(payload(required, optional, **updates))


def complete_edges(req: ScheduleRequest, overrides: dict[tuple[str, str], object] | None = None) -> list[ScheduleEdge]:
    required = fixtures.edges_for(req, overrides)
    baseline = build_required_schedule(req, required, NOW)
    pairs = unique_pairs(required_edges(req) + optional_edges(req, baseline.days))
    results = []
    for index, (origin, destination) in enumerate(pairs):
        value = (overrides or {}).get((origin.place_id, destination.place_id), 600.0)
        fields = dict(id=f"e{index}", origin=origin, destination=destination, queried_at=NOW)
        if isinstance(value, str):
            fields.update(status=value, message="受控测试原因")
        else:
            same = origin.place_id == destination.place_id or (
                origin.longitude == destination.longitude and origin.latitude == destination.latitude
            )
            seconds = 0 if same else float(value)
            fields.update(status="same_place" if same else "ok", source="same_place" if same else "amap",
                          duration_seconds=seconds, duration_minutes=math.ceil(seconds / 60),
                          distance_meters=0 if same else 600)
        results.append(ScheduleEdge(**fields))
    return results


def ok_route() -> WalkingRouteResponse:
    return WalkingRouteResponse(status="ok", queried_at=NOW, route=WalkingRoute(
        distance_meters=600, duration_seconds=600, segments=[[(121.4, 31.2), (121.41, 31.2)]],
    ))


class OptionalPureTests(unittest.TestCase):
    def preview(self, req, overrides=None):
        return build_schedule(req, complete_edges(req, overrides), NOW)

    def test_no_optional_preserves_required_timeline_exactly(self) -> None:
        req = request(3, 0)
        edges = fixtures.edges_for(req)
        baseline = build_required_schedule(req, edges, NOW)
        result = build_schedule(req, edges, NOW)
        self.assertEqual(result.model_dump(), baseline.model_dump())
        self.assertEqual(result.optional_results, [])

    def test_tail_insertion_keeps_required_visits_byte_identical(self) -> None:
        req = request(3, 2)
        edges = complete_edges(req)
        baseline = build_required_schedule(req, edges, NOW)
        result = build_schedule(req, edges, NOW)
        required_ids = {place.id for place in req.must_visit_places}
        before = [(day.date, item.model_dump_json()) for day in baseline.days for item in day.items if item.kind == "visit"]
        after = [(day.date, item.model_dump_json()) for day in result.days for item in day.items
                 if item.kind == "visit" and item.place_id in required_ids]
        self.assertEqual(before, after)
        self.assertEqual(result.status, "complete")
        self.assertEqual([item.scheduled_date for item in result.optional_results], [req.start_date, req.end_date])
        self.assertEqual([a.outcome for a in result.optional_results[1].attempts], ["day_slot_used", "scheduled"])

    def test_optional_only_uses_actual_dates_and_one_per_day(self) -> None:
        req = request(0, 3, end_date="2026-10-12")
        baseline = build_required_schedule(req, [], NOW)
        self.assertTrue(all(day.items == [] and day.return_time is None for day in baseline.days))
        result = self.preview(req)
        self.assertEqual(result.status, "complete")
        self.assertEqual([[i.place_id for i in day.items if i.kind == "visit"] for day in result.days], [["o1"], ["o2"], ["o3"]])
        self.assertEqual([a.outcome for a in result.optional_results[2].attempts], ["day_slot_used", "day_slot_used", "scheduled"])

    def test_selected_priority_and_capacity_do_not_skip_tracking(self) -> None:
        result = self.preview(request(1, 3))
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.unscheduled, [])
        self.assertIsNone(result.optional_results[2].scheduled_date)
        self.assertEqual([a.outcome for a in result.optional_results[2].attempts], ["day_slot_used", "day_slot_used"])

    def test_failed_first_optional_can_succeed_later_day_after_second_selected(self) -> None:
        req = request(1, 2)
        result = self.preview(req, {("p1", "o1"): "no_route"})
        self.assertEqual(result.status, "complete")
        self.assertEqual([a.outcome for a in result.optional_results[0].attempts], ["no_route", "scheduled"])
        self.assertEqual([a.outcome for a in result.optional_results[1].attempts], ["scheduled"])
        self.assertEqual(result.optional_results[0].scheduled_date, req.end_date)

    def test_same_failed_return_is_recorded_for_each_date_without_inventing_success(self) -> None:
        result = self.preview(request(1, 1), {("o1", "stay"): "timeout"})
        self.assertEqual(result.status, "partial")
        self.assertEqual([a.outcome for a in result.optional_results[0].attempts], ["timeout", "timeout"])
        self.assertIsNone(result.optional_results[0].scheduled_date)

    def test_optional_failure_falls_back_to_exact_baseline_including_return_lunch_wait(self) -> None:
        req = request(1, 1, daily_start_time="10:40")
        overrides = {("p1", "stay"): 1200, ("p1", "o1"): "failed", ("stay", "o1"): "failed"}
        edges = complete_edges(req, overrides)
        baseline = build_required_schedule(req, edges, NOW)
        result = build_schedule(req, edges, NOW)
        self.assertEqual([day.model_dump_json() for day in result.days], [day.model_dump_json() for day in baseline.days])
        self.assertEqual([item.kind for item in result.days[0].items], ["walk", "visit", "wait", "lunch", "walk"])

    def test_old_return_suffix_lunch_is_removed_then_recomputed_without_old_wait(self) -> None:
        req = request(1, 1, daily_start_time="10:40",
                      duration_settings=[{"place_id": "p1", "minutes": 60, "source": "default"},
                                         {"place_id": "o1", "minutes": 15, "source": "user"}])
        edges = complete_edges(req, {("p1", "stay"): 1200})
        baseline = build_required_schedule(req, edges, NOW)
        result = build_schedule(req, edges, NOW)
        self.assertEqual([i.model_dump_json() for i in baseline.days[0].items[:2]],
                         [i.model_dump_json() for i in result.days[0].items[:2]])
        self.assertEqual([i.kind for i in result.days[0].items], ["walk", "visit", "walk", "lunch", "visit", "walk"])
        self.assertEqual(result.days[0].return_time, "13:25")

    def test_lunch_already_in_required_prefix_is_not_duplicated(self) -> None:
        req = request(3, 1)
        edges = complete_edges(req)
        baseline = build_required_schedule(req, edges, NOW)
        result = build_schedule(req, edges, NOW)
        prefix = baseline.days[0].items[:-1]
        self.assertEqual([i.model_dump_json() for i in prefix], [i.model_dump_json() for i in result.days[0].items[:len(prefix)]])
        self.assertEqual(sum(i.kind == "lunch" for i in result.days[0].items), 1)

    def test_required_incomplete_skips_every_optional_without_attempts(self) -> None:
        for overrides in [{("p1", "p2"): "no_route"}, {("stay", "p1"): "timeout"}]:
            req = request(2, 3)
            result = self.preview(req, overrides)
            self.assertTrue(result.unscheduled)
            self.assertTrue(all(r.not_attempted_reason == "must_incomplete" and r.attempts == [] for r in result.optional_results))

    def test_too_long_optional_tries_next_candidate_and_next_date(self) -> None:
        req = request(1, 2, daily_end_time="15:00",
                      duration_settings=[{"place_id": "p1", "minutes": 60, "source": "default"},
                                         {"place_id": "o1", "minutes": 480, "source": "user"},
                                         {"place_id": "o2", "minutes": 15, "source": "user"}])
        result = self.preview(req)
        self.assertEqual([a.outcome for a in result.optional_results[0].attempts], ["time_window", "time_window"])
        self.assertEqual(result.optional_results[1].scheduled_date, req.start_date)

    def test_empty_day_stays_empty_when_optional_cannot_fit(self) -> None:
        req = request(0, 1, daily_end_time="09:30", lunch={"enabled": False})
        result = self.preview(req)
        self.assertEqual(result.status, "unscheduled")
        self.assertTrue(all(day.items == [] and day.return_time is None for day in result.days))
        self.assertTrue(all(attempt.outcome == "time_window" for attempt in result.optional_results[0].attempts))

    def test_same_coordinates_optional_keeps_stay_and_zero_movement(self) -> None:
        req = request(0, 1, optional_places=[fixtures.place("o1", longitude=121.4, latitude=31.2)])
        result = self.preview(req)
        self.assertEqual(result.status, "complete")
        self.assertEqual([i.duration_minutes for i in result.days[0].items], [0, 60, 0])
        self.assertTrue(all(edge.source == "same_place" for edge in result.edges))

    def test_edge_failures_remain_date_specific_and_do_not_retract_must(self) -> None:
        for status in ["no_route", "timeout", "data_error", "failed", "budget_exhausted"]:
            with self.subTest(status=status):
                req = request(1, 1)
                result = self.preview(req, {("p1", "o1"): status})
                self.assertEqual(result.status, "complete")
                self.assertEqual([a.outcome for a in result.optional_results[0].attempts], [status, "scheduled"])
                self.assertEqual(result.days[0].return_time, "10:20")

    def test_required_budget_exhaustion_is_distinct_from_started_timeout(self) -> None:
        result = self.preview(request(1, 1), {("stay", "p1"): "budget_exhausted"})
        self.assertEqual(result.unscheduled[0].reason, "route_budget_exhausted")
        self.assertEqual(result.optional_results[0].not_attempted_reason, "must_incomplete")

    def test_pure_optional_merge_does_not_mutate_baseline_request_or_edges(self) -> None:
        req = request(1, 2)
        edges = complete_edges(req)
        baseline = build_required_schedule(req, edges, NOW)
        before = (req.model_dump_json(), baseline.model_dump_json(), [e.model_dump_json() for e in edges])
        result = add_optional_schedule(req, baseline, edges)
        self.assertEqual(result, add_optional_schedule(req, baseline, edges))
        self.assertEqual(before, (req.model_dump_json(), baseline.model_dump_json(), [e.model_dump_json() for e in edges]))
        used = {i.edge_id for d in result.days for i in d.items if i.kind == "walk"}
        self.assertEqual(used, {e.id for e in result.edges if e.used})


class OptionalApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = fixtures.ScheduleApiTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_optional_default_empty_is_backward_compatible(self) -> None:
        result = self.fixture.post(fixtures.payload()).json()
        self.assertEqual(result["request"]["optional_places"], [])
        self.assertEqual(result["optional_results"], [])
        self.assertEqual(result["status"], "complete")

    def test_optional_only_skips_required_collection_and_does_not_wait_on_empty_tasks(self) -> None:
        with patch("app.api.schedule.required_edges", side_effect=AssertionError("must phase should be skipped")):
            response = self.fixture.post(payload(0, 1))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "complete")
        self.assertEqual(len(self.fixture.requests), 2)

    def test_incomplete_required_never_collects_optional_edges(self) -> None:
        self.fixture.responses[("stay", "p1")] = {"status": "1", "route": {"paths": []}}
        with patch("app.api.schedule.optional_edges", side_effect=AssertionError("optional phase should be skipped")):
            response = self.fixture.post(payload(1, 3))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.fixture.requests), 2)
        self.assertTrue(all(r["not_attempted_reason"] == "must_incomplete" for r in response.json()["optional_results"]))

    def test_invalid_overlap_counts_shape_and_duration_coverage_are_rejected(self) -> None:
        invalid = [payload(0, 0), payload(7, 0), payload(1, 4), payload(optional_places=None),
                   payload(optional_places=[fixtures.place("o1", 11), fixtures.place("o1", 12)]),
                   payload(optional_places=[fixtures.place("p1", 11)]),
                   payload(optional_places=[fixtures.place("stay", 11)]),
                   payload(optional_places=[fixtures.place("o1", 11, role="optional")]),
                   payload(optional_places=[fixtures.place("o1", 11, longitude="121.5")]),
                   payload(duration_settings=[{"place_id": "p1", "minutes": 60, "source": "default"}])]
        for data in invalid:
            with self.subTest(data=data):
                self.assertEqual(self.fixture.post(data).status_code, 422)
        self.assertEqual(self.fixture.requests, [])

    def test_same_name_different_id_is_allowed_and_required_can_share_accommodation(self) -> None:
        data = payload(1, 1, must_visit_places=[fixtures.place("stay", name="同名")],
                       optional_places=[fixtures.place("o1", 11, name="同名")],
                       duration_settings=[{"place_id": "stay", "minutes": 60, "source": "default"},
                                          {"place_id": "o1", "minutes": 60, "source": "default"}])
        response = self.fixture.post(data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "complete")

    def test_full_twenty_nine_edges_with_connection_retry_can_reach_exact_fifty_eight_attempts(self) -> None:
        data = payload(6, 3, end_date="2026-10-12", daily_end_time="12:00", lunch={"enabled": False},
                       duration_settings=[{"place_id": f"p{i}", "minutes": 60, "source": "default"} for i in range(1, 7)] +
                                         [{"place_id": f"o{i}", "minutes": 15, "source": "user"} for i in range(1, 4)])
        calls: Counter[tuple[str, str]] = Counter()

        def handler(req: httpx.Request) -> httpx.Response:
            pair = (req.url.params["origin_id"], req.url.params["destination_id"])
            calls[pair] += 1
            if calls[pair] == 1:
                raise httpx.ConnectError("controlled connection failure", request=req)
            return httpx.Response(200, json={"status": "1", "route": {"paths": [{
                "distance": "600", "duration": "600", "steps": [{"polyline": "121.4,31.2;121.41,31.2"}],
            }]}})

        async def client_dependency() -> AsyncIterator[httpx.AsyncClient]:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                yield client

        with patch.dict(app.dependency_overrides, {get_http_client: client_dependency}), \
                self.assertLogs("app.integrations.amap_walking", level="WARNING"):
            result = self.fixture.post(data).json()
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(result["edges"]), 29)
        self.assertEqual(len(calls), 29)
        self.assertEqual(sum(calls.values()), 58)
        self.assertTrue(all(count == 2 for count in calls.values()))
        self.assertEqual(len([pair for pair in calls if pair[1].startswith("o")]), 9)
        self.assertEqual(len([pair for pair in calls if pair[0].startswith("o")]), 3)
        self.assertFalse(any(a.startswith("o") and b.startswith("o") for a, b in calls))
        required_visits = [[i["place_id"] for i in day["items"] if i["kind"] == "visit" and i["place_id"].startswith("p")]
                           for day in result["days"]]
        self.assertEqual(required_visits, [["p1", "p2"], ["p3", "p4"], ["p5", "p6"]])


class SharedRouteBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.enterContext(patch("app.services.schedule_routes.MIN_ROUTE_START_INTERVAL_SECONDS", 0))

    async def test_empty_collection_returns_without_asyncio_wait(self) -> None:
        collector = ScheduleRouteCollector(AsyncMock(spec=AmapWalkingClient))
        with patch("app.services.schedule_routes.asyncio.wait", side_effect=AssertionError("empty wait")):
            self.assertEqual(await collector.collect([]), [])

    async def test_second_phase_reuses_memo_even_when_first_failed(self) -> None:
        amap = AsyncMock(spec=AmapWalkingClient)
        amap.walking.return_value = WalkingRouteResponse(status="no_route", queried_at=NOW)
        collector = ScheduleRouteCollector(amap)
        pairs = required_edges(request(1, 0))
        first = await collector.collect(pairs)
        second = await collector.collect(pairs * 2)
        self.assertEqual(first, second)
        self.assertEqual(amap.walking.call_count, 2)

    async def test_deadline_does_not_reset_between_phases_and_no_late_start(self) -> None:
        starts = []

        async def walking(req):
            starts.append(asyncio.get_running_loop().time())
            return ok_route()

        amap = AsyncMock(spec=AmapWalkingClient)
        amap.walking.side_effect = walking
        req = request(1, 1)
        with patch("app.services.schedule_routes.ROUTE_BATCH_TIMEOUT_SECONDS", 0.02):
            collector = ScheduleRouteCollector(amap)
        first = await collector.collect(required_edges(req))
        deadline = collector.deadline
        baseline = build_required_schedule(req, first, NOW)
        await asyncio.sleep(0.025)
        second = await collector.collect(optional_edges(req, baseline.days))
        self.assertEqual(collector.deadline, deadline)
        self.assertEqual(len(starts), 2)
        self.assertTrue(all(at < deadline for at in starts))
        self.assertTrue(all(edge.status == "budget_exhausted" for edge in second[2:]))
        self.assertTrue(all(edge.duration_seconds is None for edge in second[2:]))

    async def test_pacing_is_shared_across_phases_not_restarted(self) -> None:
        starts = []

        async def walking(req):
            starts.append(asyncio.get_running_loop().time())
            return ok_route()

        amap = AsyncMock(spec=AmapWalkingClient)
        amap.walking.side_effect = walking
        pairs = required_edges(request(1, 0))
        with patch("app.services.schedule_routes.MIN_ROUTE_START_INTERVAL_SECONDS", 0.012):
            collector = ScheduleRouteCollector(amap)
            await collector.collect(pairs[:1])
            await collector.collect(pairs[1:])
        self.assertEqual(len(starts), 2)
        self.assertGreaterEqual(starts[1] - starts[0], 0.011)

    async def test_second_phase_timeout_distinguishes_started_and_queued_and_drains(self) -> None:
        entered = []
        cancelled = []

        async def walking(req):
            if req.destination.place_id.startswith("p") or req.origin.place_id.startswith("p") and req.destination.place_id == "stay":
                return ok_route()
            entered.append(req)
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.append(req)
                raise

        amap = AsyncMock(spec=AmapWalkingClient)
        amap.walking.side_effect = walking
        req = request(1, 3)
        with patch("app.services.schedule_routes.ROUTE_BATCH_TIMEOUT_SECONDS", 0.04):
            collector = ScheduleRouteCollector(amap)
        first = await collector.collect(required_edges(req))
        baseline = build_required_schedule(req, first, NOW)
        results = await collector.collect(optional_edges(req, baseline.days))
        self.assertEqual(len(entered), 3)
        self.assertEqual(len(cancelled), 3)
        self.assertEqual(sum(edge.status == "timeout" for edge in results), 3)
        self.assertGreater(sum(edge.status == "budget_exhausted" for edge in results), 0)
        self.assertEqual(len(asyncio.all_tasks()), 1)

    async def test_parent_cancel_in_second_phase_cleans_tasks(self) -> None:
        amap = AsyncMock(spec=AmapWalkingClient)
        amap.walking.return_value = ok_route()
        req = request(1, 3)
        collector = ScheduleRouteCollector(amap)
        baseline = build_required_schedule(req, await collector.collect(required_edges(req)), NOW)
        entered = asyncio.Event()

        async def waiting(req):
            entered.set()
            await asyncio.Event().wait()

        amap.walking.side_effect = waiting
        task = asyncio.create_task(collector.collect(optional_edges(req, baseline.days)))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(len(asyncio.all_tasks()), 1)

    async def test_same_points_need_no_network_even_when_deadline_passed(self) -> None:
        req = request(0, 1, optional_places=[fixtures.place("o1", longitude=121.4, latitude=31.2)])
        baseline = build_required_schedule(req, [], NOW)
        amap = AsyncMock(spec=AmapWalkingClient)
        collector = ScheduleRouteCollector(amap)
        collector.deadline = 0
        result = await collector.collect(optional_edges(req, baseline.days))
        self.assertTrue(all(edge.status == "same_place" for edge in result))
        amap.walking.assert_not_called()

    async def test_edge_cap_guard_rejects_oversized_internal_batch_before_start(self) -> None:
        self.assertEqual(MAX_LOGICAL_EDGES, 29)
        amap = AsyncMock(spec=AmapWalkingClient)
        collector = ScheduleRouteCollector(amap)
        origin = required_edges(request(1, 0))[0][0]
        destinations = [origin.model_copy(update={"place_id": f"extra{i}", "longitude": 122.0}) for i in range(30)]
        with self.assertRaises(ValueError):
            await collector.collect([(origin, destination) for destination in destinations])
        self.assertEqual(collector.pairs, [])
        amap.walking.assert_not_called()


if __name__ == "__main__":
    unittest.main()
