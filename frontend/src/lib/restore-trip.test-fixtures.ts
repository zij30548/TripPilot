import { createSnapshot, type SavedDetail } from "./saved-trips";
import { emptyCostInputs } from "./schedule-costs";
import { scheduleRequest, tripRequest, required } from "./weather-schedule-check.test-fixtures";
import { adjustmentResponse } from "./schedule-adjustment.test-fixtures";
import type { ScheduleRequest } from "@/types/schedule";
import { cityRequest, located } from "./destination.test-fixtures";

export function savedFixture(overrides: Partial<ScheduleRequest> = {}, id = "a".repeat(32)): SavedDetail {
  const req = { ...scheduleRequest(), transport_mode: "walking" as const, ...overrides };
  const s = adjustmentResponse(req);
  const requirements = { ...tripRequest(), start_date: req.start_date, end_date: req.end_date,
    accommodation_place: req.accommodation_place, accommodation_location: req.accommodation_place.name,
    must_visit_places: req.must_visit_places, must_visit: req.must_visit_places.map(p => p.name), daily_start_time: req.daily_start_time, daily_end_time: req.daily_end_time };
  const costs = { ...emptyCostInputs(), tickets: { [required.id]: "0" }, accommodation: "500", other: "bad" };
  return { id, name: "受控历史行程", schema_version: "saved-trip-v1", saved_at: "2026-10-10T03:00:00Z", start_date: req.start_date,
    end_date: req.end_date, transport_mode: req.transport_mode, status: s.status, snapshot: createSnapshot(requirements, s, costs) };
}

export function savedCityFixture(overrides: Partial<ScheduleRequest> = {}, id = "a".repeat(32)): SavedDetail {
  const value = savedFixture(overrides, id);
  value.schema_version = value.snapshot.schema_version = "saved-trip-v2";
  value.snapshot.requirements = cityRequest(value.snapshot.requirements);
  const r = value.snapshot.schedule.request;
  value.snapshot.schedule.request = { ...r, destination_id: "shanghai", accommodation_place: located(r.accommodation_place), must_visit_places: r.must_visit_places.map(p => located(p)), optional_places: (r.optional_places ?? []).map(p => located(p)) };
  return value;
}
