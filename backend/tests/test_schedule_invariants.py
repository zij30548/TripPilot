"""Deterministic generated cases: injected route estimates, never live travel data."""

import random
import unittest
from datetime import datetime, timedelta, timezone

from app.schemas.schedule import ScheduleEdge, ScheduleRequest, clock_minutes
from app.services.schedule import build_schedule, hhmm
from app.services.schedule_routes import required_edges


class ScheduleInvariantTests(unittest.TestCase):
    def test_generated_windows_keep_prefix_lunch_return_and_input_invariants(self) -> None:
        for seed in range(150):
            with self.subTest(seed=seed):
                rng = random.Random(seed)
                count, day_count = rng.randint(1, 6), rng.randint(1, 3)
                start, end = rng.randint(6, 11) * 60, rng.randint(14, 23) * 60
                lodging = {"id": "stay", "name": "测试住宿", "address": None,
                           "longitude": 120.0, "latitude": 30.0, "category": None, "source": "amap"}
                places = [{**lodging, "id": f"p{i}", "name": f"测试地点{i}",
                           "longitude": 120 + (i + 1) / 100} for i in range(count)]
                if seed % 7 == 0:
                    places[0] = lodging.copy()
                request = ScheduleRequest.model_validate({
                    "start_date": "2026-10-10",
                    "end_date": (datetime(2026, 10, 10) + timedelta(days=day_count - 1)).date().isoformat(),
                    "daily_start_time": hhmm(start), "daily_end_time": hhmm(end),
                    "accommodation_place": lodging, "must_visit_places": places,
                    "duration_settings": [{"place_id": place["id"], "minutes": rng.randint(15, 480),
                                           "source": "user"} for place in places],
                    "lunch": {"enabled": seed % 3 != 0, "start_time": "12:00", "end_time": "13:00"},
                })
                now = datetime(2026, 10, 6, tzinfo=timezone.utc)
                edges = []
                for index, (origin, destination) in enumerate(required_edges(request)):
                    same = origin.place_id == destination.place_id
                    minutes = 0 if same else rng.randint(1, 150)
                    edges.append(ScheduleEdge(
                        id=f"e{index}", origin=origin, destination=destination,
                        status="same_place" if same else "ok", source="same_place" if same else "amap",
                        duration_seconds=0 if same else minutes * 60 - 0.5,
                        duration_minutes=minutes, distance_meters=0 if same else 1200,
                        queried_at=now,
                    ))
                request_before = request.model_dump()
                edges_before = [edge.model_dump() for edge in edges]
                result = build_schedule(request, edges, now)
                self.assertEqual(request.model_dump(), request_before)
                self.assertEqual([edge.model_dump() for edge in edges], edges_before)
                self.assertEqual(result, build_schedule(request, edges, now))
                self.assertEqual(len(result.days), day_count)
                visited, used = [], set()
                durations = {item.place_id: item.minutes for item in request.duration_settings}
                edge_map = {edge.id: edge for edge in edges}
                for index, day in enumerate(result.days):
                    self.assertEqual(day.date, request.start_date + timedelta(days=index))
                    if not day.items:
                        self.assertIsNone(day.return_time)
                        continue
                    clock, location, lunches = start, lodging["id"], 0
                    self.assertTrue(any(item.kind == "visit" for item in day.items))
                    for item in day.items:
                        begin, finish = clock_minutes(item.start_time), clock_minutes(item.end_time)
                        self.assertEqual(begin, clock)
                        self.assertEqual(finish - begin, item.duration_minutes)
                        self.assertLessEqual(finish, end)
                        if item.kind == "walk":
                            self.assertEqual(item.from_place_id, location)
                            self.assertEqual(item.duration_minutes, edge_map[item.edge_id].duration_minutes)
                            location = item.to_place_id
                            used.add(item.edge_id)
                        elif item.kind == "visit":
                            self.assertEqual(item.place_id, location)
                            self.assertEqual(item.duration_minutes, durations[item.place_id])
                            visited.append(item.place_id)
                        elif item.kind == "lunch":
                            lunches += 1
                            self.assertTrue(request.lunch.enabled)
                            self.assertEqual((begin, finish), (720, 780))
                        if request.lunch.enabled and item.kind in ("walk", "visit") and finish > begin:
                            self.assertFalse(begin < 780 and finish > 720)
                        clock = finish
                    self.assertLessEqual(lunches, 1)
                    self.assertEqual(location, lodging["id"])
                    self.assertEqual(day.items[-1].kind, "walk")
                    self.assertEqual(day.return_time, hhmm(clock))
                ids = [place.id for place in request.must_visit_places]
                self.assertEqual(visited, ids[:len(visited)])
                self.assertEqual([item.place_id for item in result.unscheduled], ids[len(visited):])
                for item in result.unscheduled[1:]:
                    self.assertEqual(item.reason, "current_order_not_continued")
                self.assertEqual({edge.id for edge in result.edges if edge.used}, used)
