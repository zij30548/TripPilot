import json
import unittest
from copy import deepcopy

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.schemas.place import ConfirmedPlace, Place
from app.schemas.trip import TripPlan, TripRequest
from main import app


def sample_place(place_id: str = "TEST_A", name: str = "测试住宿区域") -> dict[str, object]:
    # Fictional fixtures for contract tests; no live POI requests or claims.
    return {
        "id": place_id,
        "name": name,
        "address": "测试地址",
        "longitude": 121.5,
        "latitude": 31.2,
        "category": "测试分类",
        "source": "amap",
    }


def legacy_request() -> dict[str, object]:
    return {
        "start_date": "2026-10-10",
        "end_date": "2026-10-11",
        "budget": 3000,
        "travelers": 2,
        "accommodation_location": "旧版住宿描述",
        "pace": "balanced",
        "interests": ["Citywalk"],
        "must_visit": ["旧版必去描述"],
        "avoid_places": ["旧版避免描述"],
        "daily_start_time": "09:00",
        "daily_end_time": "21:00",
    }


def confirmed_request() -> dict[str, object]:
    accommodation = sample_place()
    places = [sample_place("TEST_B", "测试必去一"), sample_place("TEST_C", "测试必去二")]
    return legacy_request() | {
        "accommodation_location": accommodation["name"],
        "accommodation_place": accommodation,
        "must_visit": [place["name"] for place in places],
        "must_visit_places": places,
    }


class TripConfirmedPlaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def assert_invalid(self, payload: dict[str, object]) -> None:
        # Explicit serialization exercises malicious non-standard NaN/Infinity
        # bodies too, without relying on the client's permissive encoder.
        response = self.client.post(
            "/trips/plan", content=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json(), {
            "detail": "旅行需求参数无效，请检查日期、住宿参考点和必去地点等信息。",
        })

    def test_legacy_request_keeps_text_and_gets_safe_empty_defaults(self) -> None:
        response = self.client.post("/trips/plan", json=legacy_request())
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertIsNone(result["request"]["accommodation_place"])
        self.assertEqual(result["request"]["must_visit_places"], [])
        self.assertEqual(result["request"]["must_visit"], ["旧版必去描述"])
        self.assertEqual(result["request"]["accommodation_location"], "旧版住宿描述")
        # Response models must accept their own legacy default serialization.
        TripPlan.model_validate(result)

    def test_confirmed_places_echo_identity_coordinates_and_optional_fields(self) -> None:
        payload = confirmed_request()
        response = self.client.post("/trips/plan", json=payload)
        self.assertEqual(response.status_code, 200)
        request = response.json()["request"]
        self.assertEqual(request["accommodation_place"], payload["accommodation_place"])
        self.assertEqual(request["must_visit_places"], payload["must_visit_places"])
        TripPlan.model_validate(response.json())

    def test_no_must_visit_places_is_valid(self) -> None:
        payload = confirmed_request() | {"must_visit_places": [], "must_visit": []}
        response = self.client.post("/trips/plan", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["request"]["must_visit_places"], [])

    def test_nullable_accommodation_retains_legacy_compatibility(self) -> None:
        payload = legacy_request() | {"accommodation_place": None}
        response = self.client.post("/trips/plan", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["request"]["accommodation_place"])

    def test_both_roles_can_share_the_same_place(self) -> None:
        place = sample_place()
        payload = confirmed_request() | {"must_visit_places": [place], "must_visit": [place["name"]]}
        self.assertEqual(self.client.post("/trips/plan", json=payload).status_code, 200)

    def test_same_name_different_ids_remain_distinct(self) -> None:
        places = [sample_place("TEST_B", "同名地点"), sample_place("TEST_C", "同名地点") | {
            "address": "另一个地址", "longitude": 121.6,
        }]
        payload = confirmed_request() | {"must_visit_places": places, "must_visit": ["同名地点", "同名地点"]}
        response = self.client.post("/trips/plan", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["request"]["must_visit_places"], places)

    def test_duplicate_ids_are_rejected_even_with_different_names(self) -> None:
        for name in ["同名", "不同名称"]:
            with self.subTest(name=name):
                places = [sample_place("TEST_DUP", "同名"), sample_place("TEST_DUP", name)]
                self.assert_invalid(confirmed_request() | {
                    "must_visit_places": places, "must_visit": [place["name"] for place in places],
                })

    def test_trimmed_duplicate_ids_are_rejected(self) -> None:
        places = [sample_place("TEST_DUP", "一"), sample_place(" TEST_DUP ", "二")]
        self.assert_invalid(confirmed_request() | {"must_visit_places": places, "must_visit": ["一", "二"]})

    def test_text_fields_must_match_the_confirmed_roles_and_order(self) -> None:
        for changes in [
            {"accommodation_location": "不一致"},
            {"must_visit": ["不一致"]},
            {"must_visit": ["测试必去二", "测试必去一"]},
            {"must_visit": []},
            {"must_visit_places": []},
        ]:
            with self.subTest(changes=changes):
                self.assert_invalid(confirmed_request() | changes)

    def test_legacy_can_supply_only_one_structured_role(self) -> None:
        place = sample_place()
        requests = [
            legacy_request() | {"accommodation_place": place, "accommodation_location": place["name"]},
            legacy_request() | {"must_visit_places": [place], "must_visit": [place["name"]]},
        ]
        for payload in requests:
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post("/trips/plan", json=payload).status_code, 200)

    def test_explicit_new_fields_are_not_silently_ignored(self) -> None:
        for changes in [
            {"accommodation_place": []}, {"accommodation_place": "地点文本"},
            {"must_visit_places": None}, {"must_visit_places": {}},
            {"must_visit_places": "地点文本"}, {"must_visit_places": [None]},
        ]:
            with self.subTest(changes=changes):
                self.assert_invalid(confirmed_request() | changes)

    def test_all_place_fields_are_required_even_when_nullable(self) -> None:
        for role in ["accommodation_place", "must_visit_places"]:
            for field in sample_place():
                with self.subTest(role=role, field=field):
                    place = sample_place()
                    del place[field]
                    payload = confirmed_request() | {role: place if role == "accommodation_place" else [place]}
                    self.assert_invalid(payload)

    def test_place_rejects_bad_identity_source_extra_fields_and_optional_text(self) -> None:
        for changes in [
            {"id": ""}, {"id": "  "}, {"id": 123}, {"id": True},
            {"name": ""}, {"name": "  "}, {"name": 123}, {"name": True},
            {"source": "manual"}, {"source": None}, {"source": "amap "},
            {"address": []}, {"address": 123}, {"category": {}}, {"category": True},
            {"price": 100}, {"key": "SENSITIVE_TEST_ONLY"},
        ]:
            for role in ["accommodation_place", "must_visit_places"]:
                with self.subTest(changes=changes, role=role):
                    place = sample_place() | changes
                    self.assert_invalid(confirmed_request() | {
                        role: place if role == "accommodation_place" else [place],
                    })

    def test_coordinates_require_finite_json_numbers_in_geographic_range(self) -> None:
        cases = [
            {"longitude": value} for value in ["121.5", True, None, [], float("nan"), float("inf"), -181, 181]
        ] + [
            {"latitude": value} for value in ["31.2", False, None, {}, float("-inf"), -91, 91]
        ]
        for changes in cases:
            for role in ["accommodation_place", "must_visit_places"]:
                with self.subTest(changes=changes, role=role):
                    place = sample_place() | changes
                    self.assert_invalid(confirmed_request() | {
                        role: place if role == "accommodation_place" else [place],
                    })

    def test_coordinate_bounds_zero_and_nullable_strings_are_accepted(self) -> None:
        for longitude, latitude in [(180, 90), (-180, -90), (0, 0)]:
            with self.subTest(longitude=longitude, latitude=latitude):
                place = sample_place() | {
                    "longitude": longitude, "latitude": latitude, "address": None, "category": None,
                }
                model = ConfirmedPlace.model_validate(place)
                self.assertEqual(model.longitude, longitude)
                self.assertEqual(model.latitude, latitude)
                self.assertIsNone(model.address)
                self.assertIsNone(model.category)

    def test_identity_and_names_normalize_before_consistency_checks(self) -> None:
        place = sample_place("  TEST_A  ", " 测试住宿区域 ")
        payload = confirmed_request() | {"accommodation_place": place}
        response = self.client.post("/trips/plan", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["request"]["accommodation_place"]["id"], "TEST_A")
        self.assertEqual(response.json()["request"]["accommodation_place"]["name"], "测试住宿区域")

    def test_response_validation_still_rejects_invalid_confirmed_place_data(self) -> None:
        result = self.client.post("/trips/plan", json=confirmed_request()).json()
        for changes in [
            {"must_visit": ["不一致"]},
            {"accommodation_location": "不一致"},
            {"must_visit_places": [sample_place(), sample_place()], "must_visit": ["测试住宿区域"] * 2},
            {"accommodation_place": sample_place() | {"source": "unverified"}},
            {"accommodation_place": sample_place() | {"latitude": "31.2"}},
        ]:
            with self.subTest(changes=changes):
                candidate = deepcopy(result)
                candidate["request"].update(changes)
                with self.assertRaises(ValidationError):
                    TripPlan.model_validate(candidate)

    def test_new_requirements_do_not_change_mock_itinerary_or_budget(self) -> None:
        original = self.client.post("/trips/plan", json=legacy_request()).json()
        updated = self.client.post("/trips/plan", json=confirmed_request()).json()
        for field in ["days", "budget_breakdown", "estimated_cost", "notice", "is_mock"]:
            with self.subTest(field=field):
                self.assertEqual(updated[field], original[field])

    def test_search_output_model_keeps_its_existing_default_source_behavior(self) -> None:
        place = sample_place()
        del place["source"]
        self.assertEqual(Place.model_validate(place).source, "amap")
        with self.assertRaises(ValidationError):
            ConfirmedPlace.model_validate(place)

    def test_model_default_lists_are_not_shared(self) -> None:
        first = TripRequest.model_validate(legacy_request())
        second = TripRequest.model_validate(legacy_request())
        first.must_visit_places.append(ConfirmedPlace.model_validate(sample_place()))
        self.assertEqual(second.must_visit_places, [])

    def test_safe_validation_does_not_reflect_sensitive_input(self) -> None:
        sensitive = "SENSITIVE_TEST_ONLY"
        payload = confirmed_request() | {"accommodation_place": sample_place() | {"source": sensitive}}
        response = self.client.post("/trips/plan", json=payload)
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(sensitive, response.text)
        self.assertNotIn("input", response.text)


if __name__ == "__main__":
    unittest.main()
