"""Actual pure-scheduler output for cross-language/display checks; wholly synthetic.

No live POI lookup, no user request reconstruction, no database or external I/O.
"""
import json

from app.schemas.destination import CityScheduleRequest, CityScheduleResponse
from app.services.schedule import build_schedule
from app.services.schedule import build_required_schedule
from app.services.schedule_routes import required_edges
from tests import test_schedule_optional as optional
from tests import test_schedule_transit as transit


def examples() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    def add(label: str, failures: dict[tuple[str, str], object], roads: list[list[str]],
            *, updates: dict[str, object] | None = None) -> None:
        payload = optional.payload(3, 1, transport_mode="transit", start_date="2026-10-17", end_date="2026-10-22")
        payload["destination_id"] = "shanghai"
        for place in [payload["accommodation_place"], *payload["must_visit_places"], *payload["optional_places"]]:
            place["region"] = {"citycode": "021", "cityname": "上海市", "adcode": "310106"}
        payload.update(updates or {})
        request = CityScheduleRequest.model_validate(payload)
        edges = transit.edges_for(request, failures)
        required = edges[:len(required_edges(request))]
        # Match the real orchestration: no optional queries after an incomplete baseline.
        if build_required_schedule(request, required, transit.NOW, response_type=CityScheduleResponse).unscheduled:
            edges = required
        response = build_schedule(request, edges, transit.NOW, response_type=CityScheduleResponse)
        rows.append({"label": label, "response": response.model_dump(mode="json"), "roads": roads})

    add("normal", {}, [])
    for status in ["data_error", "no_route", "unsupported", "timeout", "budget_exhausted", "failed"]:
        add(status, {("stay", "p1"): status}, [["stay", "p1"]])
    add("return", {("p1", "stay"): "data_error"}, [["p1", "stay"]])
    add("both", {("stay", "p1"): "data_error", ("p1", "stay"): "timeout"}, [["stay", "p1"], ["p1", "stay"]])
    add("prefix", {("p1", "p2"): "data_error", ("stay", "p2"): "timeout"}, [["p1", "p2"]])
    stays = [{"place_id": p, "minutes": 60, "source": "default"} for p in ["p1", "p2", "p3", "o1"]]
    stays[0] = {"place_id": "p1", "minutes": 180, "source": "user"}
    stays[1] = {"place_id": "p2", "minutes": 240, "source": "user"}
    add("next_day_stay", {("stay", "p2"): "data_error"}, [["stay", "p2"]], updates={"duration_settings": stays, "daily_end_time": "19:00"})
    stays[0] = {"place_id": "p1", "minutes": 420, "source": "user"}
    stays[1] = {"place_id": "p2", "minutes": 15, "source": "user"}
    add("next_day_return", {("stay", "p2"): "data_error"}, [["stay", "p2"]], updates={"duration_settings": stays, "daily_end_time": "20:30"})
    add("time_window", {}, [], updates={"daily_end_time": "09:30", "lunch": {"enabled": False, "start_time": "12:00", "end_time": "13:00"}})
    return rows


if __name__ == "__main__":
    print(json.dumps(examples(), ensure_ascii=False))
