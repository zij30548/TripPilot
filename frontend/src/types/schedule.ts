import { hasValidCoordinates, isConfirmedPlace, type Place } from "./place";
import type { WalkingRouteEndpoint } from "./route";
import { isTransitRouteResponse, type TransitRoute } from "./transit";

export type ScheduleTransportMode = "walking" | "transit";
export type DurationSetting = { place_id: string; minutes: number; source: "default" | "user" };
export type ScheduleRequest = {
  start_date: string; end_date: string; daily_start_time: string; daily_end_time: string;
  accommodation_place: Place; must_visit_places: Place[]; duration_settings: DurationSetting[];
  optional_places?: Place[];
  transport_mode?: ScheduleTransportMode;
  lunch: { enabled: boolean; start_time: string; end_time: string };
};
export type ScheduleItem = {
  kind: "walk" | "transit" | "visit" | "wait" | "lunch";
  start_time: string; end_time: string; duration_minutes: number;
  place_id: string | null; from_place_id: string | null; to_place_id: string | null;
  edge_id: string | null; duration_source: "default" | "user" | null;
};
export type ScheduleDay = { date: string; items: ScheduleItem[]; return_time: string | null };
export type ScheduleEdge = {
  id: string; origin: WalkingRouteEndpoint; destination: WalkingRouteEndpoint;
  transport_mode: ScheduleTransportMode;
  status: "ok" | "same_place" | "no_route" | "unsupported" | "timeout" | "data_error" | "failed" | "budget_exhausted";
  duration_seconds: number | null; duration_minutes: number | null; distance_meters: number | null;
  source: "amap" | "same_place"; queried_at: string; message: string | null; used: boolean;
  transit_route: TransitRoute | null; selection_rule: "first_supported_complete" | null;
};
export type UnscheduledPlace = {
  place_id: string; reason: "time_window" | "route_timeout" | "no_route" | "route_unsupported" | "route_budget_exhausted" | "route_data_error" | "route_failed" | "current_order_not_continued";
  message: string;
};
export type ScheduleResponse = {
  status: "complete" | "partial" | "unscheduled"; generated_at: string; request: ScheduleRequest & { transport_mode: ScheduleTransportMode };
  days: ScheduleDay[]; unscheduled: UnscheduledPlace[]; edges: ScheduleEdge[]; rules: string[]; unknowns: string[];
  optional_results: OptionalScheduleResult[];
};
export type OptionalScheduleResult = {
  place_id: string; scheduled_date: string | null; not_attempted_reason: "must_incomplete" | null;
  attempts: { date: string; outcome: "scheduled" | "time_window" | "no_route" | "unsupported" | "timeout" | "data_error" | "failed" | "budget_exhausted" | "day_slot_used"; message: string }[];
};
function record(value: unknown): value is Record<string, unknown> { return typeof value === "object" && value !== null && !Array.isArray(value); }
function exact(value: Record<string, unknown>, expected: string[]): boolean { return Object.keys(value).length === expected.length && expected.every((key) => key in value); }
function text(value: unknown): value is string { return typeof value === "string" && value.trim().length > 0; }
function integer(value: unknown, min: number, max = Number.MAX_SAFE_INTEGER): value is number { return typeof value === "number" && Number.isInteger(value) && value >= min && value <= max; }
function nonnegative(value: unknown): value is number { return typeof value === "number" && Number.isFinite(value) && value >= 0; }
function utc(value: unknown): value is string {
  return typeof value === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)$/.test(value) &&
    Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 19) === value.slice(0, 19);
}
export function scheduleMinutes(value: unknown): number | null {
  if (typeof value !== "string" || !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(value)) return null;
  return Number(value.slice(0, 2)) * 60 + Number(value.slice(3));
}
// Legacy TripRequest echoes may include :00 seconds. Never silently hide nonzero seconds.
export function scheduleClock(value: string): string { return /^\d{2}:\d{2}:00$/.test(value) ? value.slice(0, 5) : value; }
function dateMilliseconds(value: unknown): number | null {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const parsed = Date.parse(`${value}T00:00:00Z`);
  return Number.isFinite(parsed) && new Date(parsed).toISOString().slice(0, 10) === value ? parsed : null;
}
export function scheduleDates(start: string, end: string): string[] | null {
  const from = dateMilliseconds(start); const to = dateMilliseconds(end);
  if (from === null || to === null || to < from || to - from > 2 * 86_400_000) return null;
  return Array.from({ length: (to - from) / 86_400_000 + 1 }, (_, index) => new Date(from + index * 86_400_000).toISOString().slice(0, 10));
}
export function isScheduleRequest(value: unknown): value is ScheduleRequest {
  if (!record(value) || !exact(value, ["start_date", "end_date", "daily_start_time", "daily_end_time", "accommodation_place", "must_visit_places", "duration_settings", "lunch", ...("optional_places" in value ? ["optional_places"] : []), ...("transport_mode" in value ? ["transport_mode"] : [])]) ||
      ("transport_mode" in value && value.transport_mode !== "walking" && value.transport_mode !== "transit") ||
      typeof value.start_date !== "string" || typeof value.end_date !== "string" || !scheduleDates(value.start_date, value.end_date) ||
      !isConfirmedPlace(value.accommodation_place) || !Array.isArray(value.must_visit_places) || value.must_visit_places.length > 6 ||
      !value.must_visit_places.every(isConfirmedPlace) || !Array.isArray(value.duration_settings) ||
      !record(value.lunch) || !exact(value.lunch, ["enabled", "start_time", "end_time"]) || typeof value.lunch.enabled !== "boolean") return false;
  const optional: unknown = "optional_places" in value ? value.optional_places : [];
  if (!Array.isArray(optional) || optional.length > 3 || !optional.every(isConfirmedPlace) ||
      value.must_visit_places.length + optional.length < 1 || value.duration_settings.length !== value.must_visit_places.length + optional.length) return false;
  const dayStart = scheduleMinutes(value.daily_start_time); const dayEnd = scheduleMinutes(value.daily_end_time);
  const lunchStart = scheduleMinutes(value.lunch.start_time); const lunchEnd = scheduleMinutes(value.lunch.end_time);
  if (dayStart === null || dayEnd === null || dayStart >= dayEnd || lunchStart === null || lunchEnd === null ||
      (value.lunch.enabled && (lunchStart >= lunchEnd || lunchStart < dayStart || lunchEnd > dayEnd))) return false;
  const all = [...value.must_visit_places, ...optional];
  const ids = new Set(all.map((place) => place.id.trim()));
  const accommodation = value.accommodation_place;
  if (ids.size !== all.length || optional.some((place) => place.id === accommodation.id) ||
      ![value.accommodation_place, ...all].every((place) => place.id.length <= 128 && /^[A-Za-z0-9_-]+$/.test(place.id))) return false;
  const settingIds = new Set<string>();
  return value.duration_settings.every((setting) => {
    if (!record(setting) || !exact(setting, ["place_id", "minutes", "source"]) || !text(setting.place_id) || !ids.has(setting.place_id) || settingIds.has(setting.place_id) ||
        !integer(setting.minutes, 15, 480) || (setting.source !== "default" && setting.source !== "user") || (setting.source === "default" && setting.minutes !== 60)) return false;
    settingIds.add(setting.place_id); return true;
  });
}
function samePlace(left: Place, right: Place): boolean {
  return left.id === right.id && left.name === right.name && left.address === right.address && left.longitude === right.longitude &&
    left.latitude === right.latitude && left.category === right.category && left.source === right.source;
}
function sameRequest(left: ScheduleRequest, right: ScheduleRequest): boolean {
  return (left.transport_mode ?? "walking") === (right.transport_mode ?? "walking") && left.start_date === right.start_date && left.end_date === right.end_date && left.daily_start_time === right.daily_start_time && left.daily_end_time === right.daily_end_time &&
    samePlace(left.accommodation_place, right.accommodation_place) && left.must_visit_places.length === right.must_visit_places.length &&
    left.must_visit_places.every((place, index) => samePlace(place, right.must_visit_places[index])) &&
    (left.optional_places ?? []).length === (right.optional_places ?? []).length && (left.optional_places ?? []).every((place, index) => samePlace(place, (right.optional_places ?? [])[index])) &&
    left.duration_settings.every((setting) => right.duration_settings.some((original) => original.place_id === setting.place_id && original.minutes === setting.minutes && original.source === setting.source)) &&
    left.lunch.enabled === right.lunch.enabled && left.lunch.start_time === right.lunch.start_time && left.lunch.end_time === right.lunch.end_time;
}
function endpoint(value: unknown, places: Place[]): value is WalkingRouteEndpoint {
  return record(value) && exact(value, ["place_id", "longitude", "latitude"]) && typeof value.longitude === "number" && typeof value.latitude === "number" &&
    hasValidCoordinates({ longitude: value.longitude, latitude: value.latitude }) && Number(value.longitude.toFixed(6)) === value.longitude && Number(value.latitude.toFixed(6)) === value.latitude &&
    places.some((place) => place.id === value.place_id && Math.abs(place.longitude - (value.longitude as number)) <= 0.000000500001 && Math.abs(place.latitude - (value.latitude as number)) <= 0.000000500001);
}
function sameEndpoint(origin: WalkingRouteEndpoint, destination: WalkingRouteEndpoint): boolean {
  return origin.place_id === destination.place_id || (origin.longitude.toFixed(6) === destination.longitude.toFixed(6) && origin.latitude.toFixed(6) === destination.latitude.toFixed(6));
}

// Reuse the transit adapter's public contract while rejecting extra fields at the
// schedule boundary. Missing leg estimates/geometry remain unknown, not zero.
function isScheduleTransitRoute(value: unknown, queriedAt: string): value is TransitRoute {
  return record(value) && exact(value, ["duration_seconds", "walking_distance_meters", "fare_cny", "geometry_complete", "legs"]) &&
    Array.isArray(value.legs) && value.legs.every((leg) => record(leg) && exact(leg, ["mode", "distance_meters", "duration_seconds", "instruction", "line_name", "departure_stop", "arrival_stop", "geometry", "geometry_complete"])) &&
    (value.walking_distance_meters === 0 || value.legs.some((leg) => leg.mode === "walking")) &&
    isTransitRouteResponse({ status: "ok", source: "amap", queried_at: queriedAt, selection_rule: "first_supported_complete", route: value });
}

export function isScheduleResponse(value: unknown, request: ScheduleRequest): value is ScheduleResponse {
  if (!isScheduleRequest(request) || !record(value) || !exact(value, ["status", "generated_at", "request", "days", "unscheduled", "edges", "rules", "unknowns", "optional_results"]) ||
      (value.status !== "complete" && value.status !== "partial" && value.status !== "unscheduled") || !utc(value.generated_at) ||
      !isScheduleRequest(value.request) || !("transport_mode" in value.request) || !sameRequest(value.request, request) || !Array.isArray(value.days) || !Array.isArray(value.unscheduled) ||
      !Array.isArray(value.edges) || value.edges.length > ((request.optional_places ?? []).length ? 29 : 17) || !Array.isArray(value.optional_results) ||
      !Array.isArray(value.rules) || !value.rules.length || !value.rules.every(text) ||
      !Array.isArray(value.unknowns) || !value.unknowns.length || !value.unknowns.every(text)) return false;
  const optional = request.optional_places ?? [];
  const mode = request.transport_mode ?? "walking";
  const movementKind = mode === "walking" ? "walk" : "transit";
  const optionalIds = new Set(optional.map((place) => place.id));
  const places = [request.accommodation_place, ...request.must_visit_places, ...optional];
  const edges = new Map<string, ScheduleEdge>(); const pairs = new Set<string>();
  for (const item of value.edges) {
    if (!record(item) || !exact(item, ["id", "origin", "destination", "transport_mode", "status", "duration_seconds", "duration_minutes", "distance_meters", "source", "queried_at", "message", "used", "transit_route", "selection_rule"]) ||
        item.transport_mode !== mode ||
        !text(item.id) || edges.has(item.id) || !endpoint(item.origin, places) || !endpoint(item.destination, places) || typeof item.used !== "boolean" ||
        !utc(item.queried_at) || !(item.message === null || text(item.message))) return false;
    const identity = (point: WalkingRouteEndpoint) => [point.place_id, point.longitude.toFixed(6), point.latitude.toFixed(6)];
    const pair = JSON.stringify([mode, identity(item.origin), identity(item.destination)]); if (pairs.has(pair)) return false;
    pairs.add(pair);
    const zero = sameEndpoint(item.origin, item.destination);
    if (item.status === "ok") {
      if (zero || item.source !== "amap" || !nonnegative(item.duration_seconds) || item.duration_seconds <= 0 || !integer(item.duration_minutes, 1) ||
          item.duration_minutes !== Math.ceil(item.duration_seconds / 60) || item.message !== null) return false;
      if (mode === "walking") {
        if (!nonnegative(item.distance_meters) || item.distance_meters <= 0 || item.distance_meters > 100_000 || item.transit_route !== null || item.selection_rule !== null) return false;
      } else if (item.distance_meters !== null || item.selection_rule !== "first_supported_complete" || !isScheduleTransitRoute(item.transit_route, item.queried_at) || item.transit_route.duration_seconds !== item.duration_seconds) return false;
    } else if (item.status === "same_place") {
      if (!zero || item.source !== "same_place" || item.duration_seconds !== 0 || item.duration_minutes !== 0 || item.distance_meters !== (mode === "walking" ? 0 : null) || item.message !== null || item.transit_route !== null || item.selection_rule !== null) return false;
    } else if (item.status === "no_route" || (item.status === "unsupported" && mode === "transit") || item.status === "timeout" || item.status === "data_error" || item.status === "failed" || item.status === "budget_exhausted") {
      if (zero || item.source !== "amap" || item.used || !text(item.message) || item.duration_seconds !== null || item.duration_minutes !== null || item.distance_meters !== null || item.transit_route !== null || item.selection_rule !== null) return false;
    } else return false;
    edges.set(item.id, item as ScheduleEdge);
  }
  const dates = scheduleDates(request.start_date, request.end_date)!;
  const dayStart = scheduleMinutes(request.daily_start_time)!; const dayEnd = scheduleMinutes(request.daily_end_time)!;
  const lunchStart = scheduleMinutes(request.lunch.start_time)!; const lunchEnd = scheduleMinutes(request.lunch.end_time)!;
  if (value.days.length !== dates.length) return false;
  const visited: string[] = []; const used = new Set<string>(); const optionalVisits = new Map<string, string>();
  const dailyOptional = new Map<string, string>(); const tails: Place[] = [];
  for (let dayIndex = 0; dayIndex < dates.length; dayIndex++) {
    const day: unknown = value.days[dayIndex];
    if (!record(day) || !exact(day, ["date", "items", "return_time"]) || day.date !== dates[dayIndex] || !Array.isArray(day.items)) return false;
    if (!day.items.length) { if (day.return_time !== null) return false; tails.push(request.accommodation_place); continue; }
    let clock = dayStart; let location = request.accommodation_place.id; let visitCount = 0; let lunchCount = 0;
    let firstMovement = true; let lastKind: unknown = null; let tail = request.accommodation_place; let optionalVisited = false;
    for (const raw of day.items) {
      if (!record(raw) || !exact(raw, ["kind", "start_time", "end_time", "place_id", "from_place_id", "to_place_id", "edge_id", "duration_minutes", "duration_source"])) return false;
      const start = scheduleMinutes(raw.start_time); const end = scheduleMinutes(raw.end_time);
      if (start === null || end === null || start !== clock || end < start || end > dayEnd || !integer(raw.duration_minutes, 0) || raw.duration_minutes !== end - start) return false;
      if (raw.kind === movementKind) {
        if (raw.place_id !== null || raw.duration_source !== null || !text(raw.edge_id)) return false;
        const edge = edges.get(raw.edge_id);
        if (!edge || (edge.status !== "ok" && edge.status !== "same_place") || !edge.used || raw.duration_minutes !== edge.duration_minutes ||
            raw.from_place_id !== location || raw.from_place_id !== edge.origin.place_id || raw.to_place_id !== edge.destination.place_id) return false;
        if (firstMovement && raw.from_place_id !== request.accommodation_place.id) return false;
        firstMovement = false; location = edge.destination.place_id; used.add(edge.id);
      } else if (raw.kind === "visit") {
        if (firstMovement || !text(raw.place_id) || raw.place_id !== location || raw.from_place_id !== null || raw.to_place_id !== null || raw.edge_id !== null) return false;
        const setting = request.duration_settings.find((setting) => setting.place_id === raw.place_id);
        if (!setting || raw.duration_minutes !== setting.minutes || raw.duration_source !== setting.source || optionalVisited) return false;
        if (optionalIds.has(raw.place_id)) {
          if (optionalVisits.has(raw.place_id)) return false;
          optionalVisits.set(raw.place_id, day.date); dailyOptional.set(day.date, raw.place_id); optionalVisited = true;
        } else {
          if (request.must_visit_places[visited.length]?.id !== raw.place_id) return false;
          tail = request.must_visit_places[visited.length]; visited.push(raw.place_id);
        }
        visitCount++;
      } else if (raw.kind === "wait" || raw.kind === "lunch") {
        if (raw.place_id !== null || raw.from_place_id !== null || raw.to_place_id !== null || raw.edge_id !== null || raw.duration_source !== null || !request.lunch.enabled || raw.duration_minutes <= 0) return false;
        if (raw.kind === "lunch") { if (start !== lunchStart || end !== lunchEnd || ++lunchCount > 1) return false; }
        else if (end !== lunchStart || lunchCount !== 0) return false;
      } else return false;
      if (request.lunch.enabled && (raw.kind === movementKind || raw.kind === "visit") && start < lunchEnd && end > lunchStart) return false;
      clock = end; lastKind = raw.kind;
    }
    if (!visitCount || firstMovement || lastKind !== movementKind || location !== request.accommodation_place.id || day.return_time !== day.items.at(-1).end_time ||
        (request.lunch.enabled && dayStart < lunchEnd && clock > lunchStart && lunchCount !== 1)) return false;
    tails.push(tail);
  }
  if ([...edges.values()].some((edge) => edge.used !== used.has(edge.id))) return false;
  const remainder = request.must_visit_places.slice(visited.length);
  const scheduledCount = visited.length + optionalVisits.size;
  if (remainder.length !== value.unscheduled.length || (remainder.length && optionalVisits.size) ||
      value.status !== (scheduledCount === request.must_visit_places.length + optional.length ? "complete" : scheduledCount ? "partial" : "unscheduled")) return false;
  if (!value.unscheduled.every((item, index) => record(item) && exact(item, ["place_id", "reason", "message"]) && item.place_id === remainder[index].id && text(item.message) &&
    (index ? item.reason === "current_order_not_continued" : ["time_window", "route_timeout", "no_route", "route_data_error", "route_failed", "route_budget_exhausted", ...(mode === "transit" ? ["route_unsupported"] : [])].includes(item.reason as string)))) return false;
  const findEdge = (from: Place, to: Place) => [...edges.values()].find((edge) => endpoint(edge.origin, [from]) && endpoint(edge.destination, [to]));
  if (remainder.length && value.unscheduled[0].reason !== "time_window") {
    const blocked = remainder[0];
    const expectedStatus: Record<string, string> = { route_timeout: "timeout", no_route: "no_route", route_unsupported: "unsupported", route_budget_exhausted: "budget_exhausted", route_data_error: "data_error", route_failed: "failed" };
    const possibleOrigins = [request.accommodation_place, ...(visited.length ? [request.must_visit_places[visited.length - 1]] : [])];
    const relevant = [...possibleOrigins.map((origin) => findEdge(origin, blocked)), findEdge(blocked, request.accommodation_place)];
    const expected = expectedStatus[value.unscheduled[0].reason as string];
    if (!relevant.some((edge) => edge?.status === expected || (!edge && expected === "data_error"))) return false;
  }
  // Only the bounded base edges and fixed daily tail→optional→stay alternatives are valid.
  const allowed: [Place, Place][] = request.must_visit_places.flatMap((place) => [[request.accommodation_place, place], [place, request.accommodation_place]] as [Place, Place][]);
  request.must_visit_places.slice(1).forEach((place, index) => allowed.push([request.must_visit_places[index], place]));
  if (!remainder.length) for (const place of optional) {
    allowed.push([place, request.accommodation_place]);
    for (const tail of tails) allowed.push([tail, place]);
  }
  if ([...edges.values()].some((edge) => !allowed.some(([from, to]) => endpoint(edge.origin, [from]) && endpoint(edge.destination, [to])))) return false;
  if (value.optional_results.length !== optional.length) return false;
  return value.optional_results.every((result, index) => {
    if (!record(result) || !exact(result, ["place_id", "scheduled_date", "not_attempted_reason", "attempts"]) || result.place_id !== optional[index].id || !Array.isArray(result.attempts) ||
        result.scheduled_date !== (optionalVisits.get(optional[index].id) ?? null)) return false;
    if (remainder.length) return result.not_attempted_reason === "must_incomplete" && result.attempts.length === 0;
    if (result.not_attempted_reason !== null) return false;
    const count = result.scheduled_date === null ? dates.length : dates.indexOf(result.scheduled_date as string) + 1;
    if (result.attempts.length !== count) return false;
    return result.attempts.every((attempt, dayIndex) => {
      if (!record(attempt) || !exact(attempt, ["date", "outcome", "message"]) || attempt.date !== dates[dayIndex] || !text(attempt.message)) return false;
      const winner = optional.findIndex((place) => place.id === dailyOptional.get(dates[dayIndex]));
      if (winner >= 0 && winner < index) return attempt.outcome === "day_slot_used";
      if (attempt.outcome === "scheduled") return result.scheduled_date === attempt.date && dayIndex === count - 1;
      if (result.scheduled_date === attempt.date || !["time_window", "no_route", "timeout", "data_error", "failed", "budget_exhausted", ...(mode === "transit" ? ["unsupported"] : [])].includes(attempt.outcome as string)) return false;
      if (attempt.outcome === "time_window") return true;
      const relevant = [findEdge(tails[dayIndex], optional[index]), findEdge(optional[index], request.accommodation_place)];
      // The algorithm reports the first unavailable directed edge; an absent
      // record is data_error, not an invented timeout or unsupported scheme.
      const failure = relevant.find((edge) => !edge || (edge.status !== "ok" && edge.status !== "same_place"));
      return failure ? failure.status === attempt.outcome : relevant.includes(undefined) && attempt.outcome === "data_error";
    });
  });
}
