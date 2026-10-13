import type { ScheduleRequest, ScheduleResponse } from "@/types/schedule";
import { isScheduleResponse } from "@/types/schedule";
import { isWalkingRouteResponse, type WalkingRoute, type WalkingRouteEndpoint, type WalkingRouteResponse } from "@/types/route";
import { isTransitRouteResponse, type TransitRouteResponse } from "@/types/transit";

export type WalkingDrawing = { edge_id: string; transport_mode: "walking"; origin: WalkingRouteEndpoint; destination: WalkingRouteEndpoint;
  queried_at: string; status: "available" | "missing" | "limit_exceeded"; route: WalkingRoute | null };
export type ScheduleDrawing = { version: "walking-drawing-v1"; entries: WalkingDrawing[] };
export type DrawingState = { status: "valid"; attachment: ScheduleDrawing } | { status: "missing" | "invalid"; attachment: null };
export type ScheduleDelivery = { schedule: ScheduleResponse; drawing: DrawingState };
const record = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const exact = (v: Record<string, unknown>, keys: string[]) => Object.keys(v).length === keys.length && keys.every(k => k in v);
const endpoint = (v: unknown, expected: WalkingRouteEndpoint) => record(v) && exact(v, ["place_id", "longitude", "latitude"]) &&
  v.place_id === expected.place_id && v.longitude === expected.longitude && v.latitude === expected.latitude;

// Drawing failure is local: the independently exact-validated core remains usable.
export function validateDrawing(value: unknown, schedule: ScheduleResponse): DrawingState {
  const invalid: DrawingState = { status: "invalid", attachment: null };
  if (!record(value) || !exact(value, ["version", "entries"]) || value.version !== "walking-drawing-v1" || !Array.isArray(value.entries) || value.entries.length > 29) return invalid;
  const used = new Set(schedule.days.flatMap(d => d.items.filter(i => i.kind === "walk").map(i => i.edge_id)));
  const expected = schedule.edges.filter(e => used.has(e.id) && e.transport_mode === "walking" && e.status === "ok");
  if (value.entries.length !== expected.length) return invalid;
  const seen = new Set<string>(); let points = 0;
  for (const raw of value.entries) {
    if (!record(raw) || !exact(raw, ["edge_id", "transport_mode", "origin", "destination", "queried_at", "status", "route"])) return invalid;
    const edge = expected.find(e => e.id === raw.edge_id);
    if (!edge || seen.has(edge.id) || raw.transport_mode !== "walking" || !endpoint(raw.origin, edge.origin) || !endpoint(raw.destination, edge.destination) ||
      typeof raw.queried_at !== "string" || !Number.isFinite(Date.parse(raw.queried_at)) || Date.parse(raw.queried_at) !== Date.parse(edge.queried_at)) return invalid;
    seen.add(edge.id);
    if (raw.status === "missing" || raw.status === "limit_exceeded") { if (raw.route !== null) return invalid; continue; }
    const wrapped = { status: "ok", source: "amap", queried_at: raw.queried_at, route: raw.route };
    if (raw.status !== "available" || !record(raw.route) || !exact(raw.route, ["distance_meters", "duration_seconds", "segments"]) || !isWalkingRouteResponse(wrapped) || wrapped.status !== "ok") return invalid;
    const route = wrapped.route, count = route.segments.reduce((n, s) => n + s.length, 0);
    if (route.distance_meters !== edge.distance_meters || route.duration_seconds !== edge.duration_seconds || route.segments.length > 256 || count > 6_000) return invalid;
    points += count;
  }
  if (points > 24_000 || new TextEncoder().encode(JSON.stringify(value)).length > 1_048_576) return invalid;
  return { status: "valid", attachment: value as ScheduleDrawing };
}

// Explicit negotiated envelope; a legacy server/core caller still has no attachment.
export function readScheduleDelivery(value: unknown, request: ScheduleRequest): ScheduleDelivery | null {
  if (record(value) && exact(value, ["schedule", "drawing"])) {
    if (!isScheduleResponse(value.schedule, request)) return null;
    return { schedule: value.schedule, drawing: validateDrawing(value.drawing, value.schedule) };
  }
  return isScheduleResponse(value, request) ? { schedule: value, drawing: { status: "missing", attachment: null } } : null;
}
export function unpackSchedule(value: ScheduleResponse | ScheduleDelivery): ScheduleDelivery {
  return "schedule" in value ? value : { schedule: value, drawing: { status: "missing", attachment: null } };
}

export type SelectedScheduleRoute = { itemIndex: number; origin: string; destination: string; edgeId: string; queriedAt: string;
  walking: WalkingRouteResponse | null; transit: TransitRouteResponse | null; message: string };
export function scheduleRouteFor(response: ScheduleResponse, date: string, itemIndex: number, drawing?: DrawingState): SelectedScheduleRoute | null {
  const item = response.days.find(d => d.date === date)?.items[itemIndex];
  if (!item || !["walk", "transit"].includes(item.kind)) return null;
  const edge = response.edges.find(e => e.id === item.edge_id && e.used && e.origin.place_id === item.from_place_id && e.destination.place_id === item.to_place_id);
  if (!edge || (edge.transport_mode === "walking" ? "walk" : "transit") !== item.kind) return null;
  const places = [response.request.accommodation_place, ...response.request.must_visit_places, ...(response.request.optional_places ?? [])];
  const result: SelectedScheduleRoute = { itemIndex, origin: places.find(p => p.id === item.from_place_id)?.name ?? "起点",
    destination: places.find(p => p.id === item.to_place_id)?.name ?? "终点", edgeId: edge.id, queriedAt: edge.queried_at, walking: null, transit: null, message: "本次查询未提供可绘制路线；不会自动补查。" };
  if (edge.status === "same_place") return { ...result, message: "同一地点，无需移动，不绘制折线。" };
  if (edge.status !== "ok") return result;
  if (edge.transport_mode === "transit") {
    const route = { status: "ok", source: "amap", queried_at: edge.queried_at, selection_rule: edge.selection_rule, route: edge.transit_route };
    if (!isTransitRouteResponse(route) || route.status !== "ok") return { ...result, message: "公交几何未通过校验，未绘制；文字行程仍可使用。" };
    const drawable = route.route.legs.some(l => l.geometry.length > 0);
    return { ...result, transit: drawable ? route : null, message: !drawable ? "本方案没有可绘制几何，线路与站点仍可查看；不会自动补查。" : route.route.geometry_complete ? "展示本次查询所采用方案的乘车与接驳路段。" : "路线地图不完整：仅显示已提供的路段，不用直线补齐缺口。" };
  }
  if (drawing?.status === "invalid") return { ...result, message: "步行绘图附件未通过对应关系校验，已拒绝绘制；时间轴、费用和保存仍可使用。" };
  const entry = drawing?.attachment?.entries.find(e => e.edge_id === edge.id);
  if (entry?.status === "limit_exceeded") return { ...result, message: "步行几何超出本次绘图容量，未绘制；不截断路线，时间轴、费用和保存仍可使用。" };
  if (entry?.status !== "available" || !entry.route) return result;
  return { ...result, walking: { status: "ok", source: "amap", queried_at: edge.queried_at, route: entry.route }, message: "蓝色实线：本次查询采用的步行路段；分段保留，不跨缺口连线。" };
}
