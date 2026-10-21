"""Offline same-query/opt-in contract and drawing bounds; no external services."""
import json
import unittest
from unittest.mock import AsyncMock, patch

from tests.business_client import BusinessTestClient as TestClient

from api.routes import get_walking_client
from core.schemas.route import WalkingRoute, WalkingRouteResponse
from tooling.travel.schedule import build_schedule
from tooling.travel.schedule_routes import ScheduleRouteCollector, required_edges
from tooling.travel.schedule_drawing import drawing_for, within_edge_limit, ScheduleDelivery
from tests.test_schedule import NOW, request, payload, edges_for
from main import app


def route_for(edge) -> WalkingRoute:
    return WalkingRoute(distance_meters=edge.distance_meters, duration_seconds=edge.duration_seconds,
                        segments=[[(edge.origin.longitude, edge.origin.latitude),
                                   (edge.destination.longitude, edge.destination.latitude)]])


def contract() -> dict:
    req = request()
    edges = edges_for(req)
    core = build_schedule(req, edges, NOW)
    drawings = {e.id: route_for(e) for e in edges}
    return ScheduleDelivery(schedule=core, drawing=drawing_for(core, drawings)).model_dump(mode="json")


class ScheduleDrawingTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_query_preserves_directed_geometry_and_metrics_without_extra_calls(self):
        req = request(1)
        expected = edges_for(req)
        client = AsyncMock()
        client.walking.side_effect = [WalkingRouteResponse(status="ok", queried_at=NOW, route=route_for(e)) for e in expected]
        with patch("tooling.travel.schedule_routes.MIN_ROUTE_START_INTERVAL_SECONDS", 0):
            collector = ScheduleRouteCollector(client)
            edges = await collector.collect(required_edges(req))
        core = build_schedule(req, edges, NOW)
        drawing = drawing_for(core, collector.walking_drawings)
        self.assertEqual(client.walking.await_count, 2)
        for edge, attachment in zip(edges, drawing.entries):
            self.assertEqual(attachment.origin, edge.origin)
            self.assertEqual(attachment.destination, edge.destination)
            self.assertEqual(attachment.queried_at, edge.queried_at.isoformat())
            self.assertEqual(attachment.route.duration_seconds, edge.duration_seconds)
            self.assertEqual(attachment.route.distance_meters, edge.distance_meters)
            self.assertEqual(attachment.route.segments, route_for(edge).segments)
        self.assertEqual(drawing.entries[0].origin, drawing.entries[1].destination)
        self.assertNotIn("drawing", core.model_dump())

    async def test_opt_in_envelope_default_exact_core_and_unknown_negotiation_rejected(self):
        client = AsyncMock()
        client.walking.return_value = WalkingRouteResponse(status="ok", queried_at=NOW, route=route_for(edges_for(request(1))[0]))
        app.dependency_overrides[get_walking_client] = lambda: client
        try:
            # This route test does not run application DB initialization/lifespan.
            http = TestClient(app)
            with patch("tooling.travel.schedule_routes.MIN_ROUTE_START_INTERVAL_SECONDS", 0):
                legacy = http.post("/trips/schedule-preview", json=payload(1))
                extended = http.post("/trips/schedule-preview?drawing=walking-v1", json=payload(1))
                invalid = http.post("/trips/schedule-preview?drawing=anything", json=payload(1))
            http.close()
            self.assertEqual(legacy.status_code, 200)
            self.assertEqual(set(legacy.json()), set(extended.json()["schedule"]))
            self.assertEqual(set(extended.json()), {"schedule", "drawing"})
            self.assertEqual(client.walking.await_count, 4)
            self.assertEqual(invalid.status_code, 422)
        finally:
            app.dependency_overrides.pop(get_walking_client, None)

    def test_only_used_successful_walking_edges_and_missing_never_changes_core(self):
        data = contract()
        self.assertEqual(len(data["schedule"]["edges"]), 5)
        self.assertEqual(len(data["drawing"]["entries"]), 3)
        self.assertTrue(all(next(e for e in data["schedule"]["edges"] if e["id"] == d["edge_id"])["used"] for d in data["drawing"]["entries"]))
        core = build_schedule(request(), edges_for(request()), NOW)
        before = core.model_dump_json()
        absent = drawing_for(core, {})
        self.assertTrue(all(e.status == "missing" and e.route is None for e in absent.entries))
        self.assertEqual(core.model_dump_json(), before)

    def test_same_place_has_no_attachment_or_upstream_call(self):
        req = request(1)
        req.must_visit_places[0] = req.accommodation_place
        req.duration_settings[0].place_id = req.accommodation_place.id
        core = build_schedule(req, edges_for(req), NOW)
        self.assertEqual(drawing_for(core, {}).entries, [])
        self.assertTrue(all(e.status == "same_place" for e in core.edges))

    def test_limits_do_not_truncate_or_alter_schedule(self):
        core = build_schedule(request(), edges_for(request()), NOW)
        route = route_for(core.edges[0])
        route.segments = [route.segments[0]] * 257
        self.assertFalse(within_edge_limit(route))
        entries = drawing_for(core, {e.id: route for e in core.edges}).entries
        self.assertTrue(all(e.status == "limit_exceeded" and e.route is None for e in entries))
        route.segments = [[(121.1 + n / 1_000_000, 31.2) for n in range(6001)]]
        self.assertFalse(within_edge_limit(route))
        valid = {e.id: route_for(e) for e in core.edges}
        with patch("tooling.travel.schedule_drawing.MAX_BATCH_POINTS", 4):
            bounded = drawing_for(core, valid)
            self.assertEqual([e.status for e in bounded.entries], ["available", "available", "limit_exceeded"])
        with patch("tooling.travel.schedule_drawing.MAX_DRAWING_BYTES", 1000):
            bounded = drawing_for(core, valid)
            self.assertLessEqual(len(bounded.model_dump_json().encode()), 1000)
            self.assertIn("limit_exceeded", [e.status for e in bounded.entries])


if __name__ == "__main__":
    print(json.dumps(contract()))
