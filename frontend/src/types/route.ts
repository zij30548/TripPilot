import { hasValidCoordinates } from "@/types/place";

// AMap Web Service and JS API both use [longitude, latitude].
export type RouteCoordinate = [longitude: number, latitude: number];

export type WalkingRouteEndpoint = {
  place_id: string;
  longitude: number;
  latitude: number;
};

export type WalkingRouteRequest = {
  origin: WalkingRouteEndpoint;
  destination: WalkingRouteEndpoint;
};

export type WalkingRoute = {
  distance_meters: number;
  duration_seconds: number;
  // Separate upstream steps stay separate; never invent a line across a gap.
  segments: RouteCoordinate[][];
};

export type WalkingRouteResponse = {
  source: "amap";
  queried_at: string;
} & (
  | { status: "ok"; route: WalkingRoute }
  | { status: "no_route" | "same_place"; route: null }
);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isCoordinate(value: unknown): value is RouteCoordinate {
  return Array.isArray(value) && value.length === 2 &&
    typeof value[0] === "number" && typeof value[1] === "number" &&
    hasValidCoordinates({ longitude: value[0], latitude: value[1] });
}

export function isWalkingRouteResponse(value: unknown): value is WalkingRouteResponse {
  if (!isRecord(value) || value.source !== "amap" || typeof value.queried_at !== "string" ||
    !/^\d{4}-\d{2}-\d{2}T.*(?:Z|\+00:00)$/.test(value.queried_at) ||
    !Number.isFinite(Date.parse(value.queried_at))) return false;
  if (value.status === "no_route" || value.status === "same_place") return value.route === null;
  if (value.status !== "ok" || !isRecord(value.route)) return false;
  const route = value.route;
  return typeof route.distance_meters === "number" && Number.isFinite(route.distance_meters) && route.distance_meters > 0 && route.distance_meters <= 100_000 &&
    typeof route.duration_seconds === "number" && Number.isFinite(route.duration_seconds) && route.duration_seconds > 0 &&
    Array.isArray(route.segments) && route.segments.length > 0 &&
    route.segments.every((segment) => Array.isArray(segment) && segment.length >= 2 && segment.every(isCoordinate)) &&
    route.segments.some((segment: RouteCoordinate[]) => segment.some((point) => point[0] !== segment[0][0] || point[1] !== segment[0][1]));
}
