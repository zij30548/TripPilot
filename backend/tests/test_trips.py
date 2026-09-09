import unittest

from fastapi.testclient import TestClient

from app.schemas.trip import TripPlan
from main import app


def valid_request() -> dict[str, object]:
    return {
        "start_date": "2026-10-01",
        "end_date": "2026-10-02",
        "budget": 3000,
        "travelers": 2,
        "accommodation_location": "静安寺附近",
        "pace": "balanced",
        "interests": ["摄影", "Citywalk"],
        "must_visit": ["外滩"],
        "avoid_places": [],
        "daily_start_time": "09:00",
        "daily_end_time": "21:00",
    }


class TripApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_health(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_plan_response(self) -> None:
        response = self.client.post("/trips/plan", json=valid_request())
        self.assertEqual(response.status_code, 200)
        plan = TripPlan.model_validate(response.json())
        self.assertEqual(plan.destination, "上海")
        self.assertTrue(plan.is_mock)
        self.assertEqual(plan.estimated_cost, 1000)
        self.assertEqual([day.day for day in plan.days], [1, 2])
        for day in plan.days:
            self.assertGreater(len(day.activities), 0)
            for activity in day.activities:
                self.assertLess(activity.start_time, activity.end_time)

    def test_fixed_plan_for_all_supported_durations_and_paces(self) -> None:
        expected = self.client.post("/trips/plan", json=valid_request()).json()
        for end_date in ["2026-10-01", "2026-10-02", "2026-10-03"]:
            for pace in ["relaxed", "balanced", "packed"]:
                with self.subTest(end_date=end_date, pace=pace):
                    payload = valid_request() | {"end_date": end_date, "pace": pace}
                    response = self.client.post("/trips/plan", json=payload)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json(), expected)

    def test_invalid_requests(self) -> None:
        cases = [
            {"end_date": "2026-09-30"}, {"end_date": "2026-10-04"},
            {"start_date": "not-a-date"}, {"budget": 0}, {"budget": -1},
            {"budget": True}, {"travelers": 0}, {"travelers": 1.5},
            {"travelers": True}, {"accommodation_location": "  "},
            {"pace": "unknown"}, {"daily_end_time": "09:00"},
            {"daily_end_time": "08:00"}, {"daily_start_time": "25:00"},
            {"daily_start_time": "09:00+08:00"}, {"interests": "摄影"},
            {"must_visit": ["  "]}, {"unexpected": "value"},
        ]
        for change in cases:
            with self.subTest(change=change):
                response = self.client.post("/trips/plan", json=valid_request() | change)
                self.assertEqual(response.status_code, 422)

    def test_all_request_fields_are_required(self) -> None:
        for field in valid_request():
            with self.subTest(field=field):
                payload = valid_request()
                del payload[field]
                response = self.client.post("/trips/plan", json=payload)
                self.assertEqual(response.status_code, 422)

    def test_cors_preflight_and_response(self) -> None:
        for origin in ["http://localhost:3000", "http://127.0.0.1:3000"]:
            with self.subTest(origin=origin):
                response = self.client.options("/trips/plan", headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "content-type",
                })
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["access-control-allow-origin"], origin)
                response = self.client.post("/trips/plan", json=valid_request(),
                                            headers={"Origin": origin})
                self.assertEqual(response.headers["access-control-allow-origin"], origin)

    def test_cors_rejects_other_origins(self) -> None:
        response = self.client.options("/trips/plan", headers={
            "Origin": "https://example.com",
            "Access-Control-Request-Method": "POST",
        })
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("access-control-allow-origin", response.headers)


if __name__ == "__main__":
    unittest.main()
