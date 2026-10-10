import type { Place } from "@/types/place";
import type { CandidateResponse } from "@/types/candidates";
import type { TripPlan, TripRequest } from "@/types/trip";
import { isScheduleResponse, type ScheduleEdge, type ScheduleItem, type ScheduleRequest, type ScheduleResponse } from "@/types/schedule";
import { classifyWeatherPrecipitation, getWeatherDates, type WeatherForecastRequest, type WeatherForecastResponse, type WeatherPeriod } from "@/types/weather";

// Fictional offline fixtures, never production travel data or live evidence.
export const home: Place = { id: "check-home", name: "测试住宿", address: "住宿地址", category: "测试", latitude: 31, longitude: 121, source: "amap" };
export const required: Place = { ...home, id: "check-required", name: "同名地点", address: "必去地址", longitude: 121.01 };
export const optionals = [1, 2, 3, 4].map((n) => ({ ...home, id: `check-optional-${n}`, name: n === 1 ? "同名地点" : `测试可选${n}`, address: `可选地址${n}`, longitude: 121 + n / 10 }));
export const stamp = "2026-10-10T01:00:00Z";
export const now = Date.parse(stamp);
export function tripRequest(): TripRequest {
  return { start_date: "2026-10-10", end_date: "2026-10-10", daily_start_time: "09:00", daily_end_time: "18:00", accommodation_location: home.name, accommodation_place: home, must_visit: [required.name], must_visit_places: [required], budget: 3000, travelers: 2, pace: "balanced", interests: ["摄影"], avoid_places: [] };
}
export function plan(request = tripRequest()): TripPlan {
  return { request, destination: "上海", estimated_cost: 20, currency: "CNY", is_mock: true, notice: "测试 Mock", budget_breakdown: { transport: 0, food: 0, tickets: 20, other: 0 },
    days: [1, 2].map((day) => ({ day, date: `2026-10-${9 + day}`, title: `旧第${day}天`, transports: [], activities: ["甲", "乙"].map((label, index) => ({ id: `mock-${index}`, name: `测试活动${label}`, category: "sightseeing" as const, estimated_cost: 10, description: "Mock 描述", start_time: "09:00", end_time: "10:00" })), weather: { date: `2026-10-${9 + day}`, condition: "Mock 晴", min_temperature: 18, max_temperature: 25, rain_risk: 10 } })) };
}
export function period(weather: string | null): WeatherPeriod {
  return { weather, temperature_celsius: 0, wind_direction: "东", wind_power: "≤3", precipitation: classifyWeatherPrecipitation(weather), precipitation_basis: weather };
}
export function forecast(request: WeatherForecastRequest = { start_date: "2026-10-10", end_date: "2026-10-10" }): WeatherForecastResponse {
  return { request, source: "amap", city: "上海市", adcode: "310000", timezone: "Asia/Shanghai", queried_at: stamp, reported_at: "2026-10-10T08:00:00+08:00", report_time_status: "valid", freshness_at_query: "fresh", guidance_rule: "amap_text_precipitation_v1", coverage: "complete", days: getWeatherDates(request).map((date) => ({ date, status: "available", day: period("晴"), night: period("小雨") })) };
}
export function scheduleRequest(): ScheduleRequest {
  return { start_date: "2026-10-10", end_date: "2026-10-10", daily_start_time: "09:00", daily_end_time: "18:00", accommodation_place: home, must_visit_places: [required], optional_places: [optionals[0]], duration_settings: [required, optionals[0]].map((p) => ({ place_id: p.id, minutes: 60, source: "default" })), lunch: { enabled: true, start_time: "12:00", end_time: "13:00" } };
}
export function schedule(request = scheduleRequest()): ScheduleResponse {
  const edges: ScheduleEdge[] = [], items: ScheduleItem[] = [];
  let minutes = 540, from = request.accommodation_place;
  const clock = (n: number) => `${String(Math.floor(n / 60)).padStart(2, "0")}:${String(n % 60).padStart(2, "0")}`;
  const append = (duration: number, input: Partial<ScheduleItem> & Pick<ScheduleItem, "kind">) => {
    items.push({ start_time: clock(minutes), end_time: clock(minutes + duration), duration_minutes: duration, place_id: null, from_place_id: null, to_place_id: null, edge_id: null, duration_source: null, ...input }); minutes += duration;
  };
  const move = (to: Place) => {
    const id = `edge-${edges.length}`;
    edges.push({ id, origin: { place_id: from.id, longitude: from.longitude, latitude: from.latitude }, destination: { place_id: to.id, longitude: to.longitude, latitude: to.latitude }, transport_mode: "walking", status: "ok", duration_seconds: 60, duration_minutes: 1, distance_meters: 80, source: "amap", queried_at: stamp, message: null, used: true, transit_route: null, selection_rule: null });
    append(1, { kind: "walk", from_place_id: from.id, to_place_id: to.id, edge_id: id }); from = to;
  };
  const chosen = request.optional_places?.[0];
  for (const place of [...request.must_visit_places, ...(chosen ? [chosen] : [])]) {
    move(place); const setting = request.duration_settings.find((s) => s.place_id === place.id)!;
    append(setting.minutes, { kind: "visit", place_id: place.id, duration_source: setting.source });
  }
  move(request.accommodation_place);
  const result: ScheduleResponse = { status: "complete", generated_at: stamp, request: { ...request, transport_mode: "walking" }, edges, days: [{ date: request.start_date, items, return_time: clock(minutes) }], unscheduled: [], rules: ["离线测试"], unknowns: ["预算未验证"], optional_results: (request.optional_places ?? []).map((place) => ({ place_id: place.id, scheduled_date: place.id === chosen?.id ? request.start_date : null, not_attempted_reason: null, attempts: [{ date: request.start_date, outcome: place.id === chosen?.id ? "scheduled" : "day_slot_used", message: "测试尾部名额" }] })) };
  if ((request.optional_places?.length ?? 0) > 1) result.status = "partial";
  if (!isScheduleResponse(result, request)) throw new Error("Invalid offline schedule fixture");
  return result;
}
export function candidateResponse(): CandidateResponse {
  return { status: "success" as const, queried_at: stamp, keywords: ["公园"], queries: [{ interest: "摄影", keyword: "公园", status: "success" as const, result_count: 4, message: null }], candidates: [{ place: required, role: "must_visit" as const, retrieval_sources: [] }, ...optionals.map((place) => ({ place, role: "optional" as const, retrieval_sources: [{ interest: "摄影" as const, keyword: "公园" }] }))] };
}
export function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
