import { hasValidCoordinates } from "@/types/place";
import type { RouteCoordinate } from "@/types/route";

export type TransitLeg = {
  mode: "walking" | "bus" | "subway";
  distance_meters: number | null;
  duration_seconds: number | null;
  instruction: string | null;
  line_name: string | null;
  departure_stop: string | null;
  arrival_stop: string | null;
  geometry: RouteCoordinate[][];
  geometry_complete: boolean;
};

export type TransitRoute = {
  duration_seconds: number;
  walking_distance_meters: number;
  fare_cny: number | null;
  geometry_complete: boolean;
  legs: TransitLeg[];
};

export type TransitRouteResponse = {
  source: "amap";
  queried_at: string;
  selection_rule: "first_supported_complete";
} & (
  | { status: "ok"; route: TransitRoute }
  | { status: "no_route" | "unsupported" | "same_place"; route: null }
);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function nonnegative(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

function nullableMetric(value: unknown): value is number | null {
  return value === null || nonnegative(value);
}

function text(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function coordinate(value: unknown): value is RouteCoordinate {
  return Array.isArray(value) && value.length === 2 &&
    typeof value[0] === "number" && typeof value[1] === "number" &&
    hasValidCoordinates({ longitude: value[0], latitude: value[1] });
}

function isLeg(value: unknown): value is TransitLeg {
  if (!isRecord(value) || !["walking", "bus", "subway"].includes(value.mode as string) ||
    !nullableMetric(value.distance_meters) || !nullableMetric(value.duration_seconds) ||
    !(value.instruction === null || text(value.instruction)) ||
    ![value.line_name, value.departure_stop, value.arrival_stop].every((field) => field === null || text(field)) ||
    typeof value.geometry_complete !== "boolean" || !Array.isArray(value.geometry)) return false;
  if (value.mode !== "walking" && ![value.line_name, value.departure_stop, value.arrival_stop].every(text)) return false;
  if (value.mode === "walking" && ![value.line_name, value.departure_stop, value.arrival_stop].every((field) => field === null)) return false;
  if (!value.geometry.every((segment) => Array.isArray(segment) && segment.length >= 2 && segment.every(coordinate) &&
    segment.some((point: RouteCoordinate) => point[0] !== segment[0][0] || point[1] !== segment[0][1]))) return false;
  return !value.geometry_complete || value.geometry.length > 0;
}

export function isTransitRouteResponse(value: unknown): value is TransitRouteResponse {
  if (!isRecord(value) || value.source !== "amap" || value.selection_rule !== "first_supported_complete" ||
    typeof value.queried_at !== "string" || !/^\d{4}-\d{2}-\d{2}T.*(?:Z|\+00:00)$/.test(value.queried_at) ||
    !Number.isFinite(Date.parse(value.queried_at))) return false;
  if (["no_route", "unsupported", "same_place"].includes(value.status as string)) return value.route === null;
  if (value.status !== "ok" || !isRecord(value.route)) return false;
  const route = value.route;
  return nonnegative(route.duration_seconds) && route.duration_seconds > 0 &&
    nonnegative(route.walking_distance_meters) && nullableMetric(route.fare_cny) &&
    typeof route.geometry_complete === "boolean" && Array.isArray(route.legs) && route.legs.length > 0 &&
    route.legs.every(isLeg) && route.legs.some((leg: TransitLeg) => leg.mode !== "walking") &&
    route.geometry_complete === route.legs.every((leg: TransitLeg) => leg.geometry_complete);
}
