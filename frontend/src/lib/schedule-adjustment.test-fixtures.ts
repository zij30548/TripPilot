import { isScheduleResponse, type ScheduleRequest, type ScheduleResponse } from "@/types/schedule";
import type { ScheduleCapture } from "./schedule-adjustment";
import { candidateResponse, schedule, scheduleRequest, tripRequest } from "./weather-schedule-check.test-fixtures";

export function captureFixture(): ScheduleCapture {
  const request = scheduleRequest();
  return { version: 7, request: tripRequest(), original: schedule(request), settings: { transportMode: "walking", stays: Object.fromEntries(request.duration_settings.map((s) => [s.place_id, { value: String(s.minutes), source: s.source }])), lunch: { enabled: true, start: "12:00", end: "13:00" } },
    candidates: { response: candidateResponse(), lastAttempt: candidateResponse(), excludedIds: new Set(), loading: false, showingPrevious: false, message: null } };
}
export function adjustmentResponse(request: ScheduleRequest, kind: "normal" | "required_only" | "empty" = "normal"): ScheduleResponse {
  const empty = kind === "empty";
  const subset: ScheduleRequest = { ...request, end_date: request.start_date, transport_mode: "walking", optional_places: kind === "required_only" ? [] : request.optional_places };
  subset.duration_settings = request.duration_settings.filter((s) => [...subset.must_visit_places, ...(subset.optional_places ?? [])].some((p) => p.id === s.place_id));
  const result: ScheduleResponse = empty ? { status: "unscheduled", request: { ...request, transport_mode: request.transport_mode ?? "walking" }, generated_at: "2026-10-10T02:00:00Z", days: [{ date: request.start_date, items: [], return_time: null }], edges: [], unscheduled: request.must_visit_places.map((p, i) => ({ place_id: p.id, reason: i ? "current_order_not_continued" : "time_window", message: "受控测试：未安排" })), optional_results: [], rules: ["受控测试"], unknowns: ["预算未知"] } : schedule(subset);
  result.request = structuredClone({ ...request, transport_mode: request.transport_mode ?? "walking" });
  result.generated_at = "2026-10-10T02:00:00Z";
  const visited = new Set(result.days.flatMap((d) => d.items.filter((i) => i.kind === "visit").map((i) => i.place_id)));
  result.status = visited.size === request.must_visit_places.length + (request.optional_places?.length ?? 0) ? "complete" : visited.size ? "partial" : "unscheduled";
  result.optional_results = (request.optional_places ?? []).map((p) => ({ place_id: p.id, scheduled_date: visited.has(p.id) ? request.start_date : null, not_attempted_reason: result.unscheduled.length ? "must_incomplete" : null, attempts: result.unscheduled.length ? [] : [{ date: request.start_date, outcome: visited.has(p.id) ? "scheduled" : kind === "normal" ? "day_slot_used" : "time_window", message: "受控试排结果" }] }));
  if (request.transport_mode === "transit") {
    result.days.forEach((d) => d.items.forEach((i) => { if (i.kind === "walk") i.kind = "transit"; }));
    result.edges.forEach((e) => { e.transport_mode = "transit"; e.distance_meters = null; e.selection_rule = "first_supported_complete";
      e.transit_route = { duration_seconds: e.duration_seconds!, walking_distance_meters: 0, fare_cny: null, geometry_complete: false, legs: [{ mode: "bus", line_name: "受控公交", departure_stop: "测试起点", arrival_stop: "测试终点", distance_meters: null, duration_seconds: null, instruction: null, geometry: [], geometry_complete: false }] };
    });
  }
  if (!isScheduleResponse(result, request)) throw new Error("Invalid controlled adjustment response");
  return result;
}
