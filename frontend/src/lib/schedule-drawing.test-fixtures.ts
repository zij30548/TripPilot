import type { ScheduleResponse } from "@/types/schedule";
import type { ScheduleDrawing } from "./schedule-drawing";

export function drawingFixture(response: ScheduleResponse): ScheduleDrawing {
  return { version: "walking-drawing-v1", entries: response.edges.filter(e => e.used && e.status === "ok" && e.transport_mode === "walking").map(e => ({
    edge_id: e.id, transport_mode: "walking", origin: e.origin, destination: e.destination, queried_at: e.queried_at, status: "available",
    route: { duration_seconds: e.duration_seconds!, distance_meters: e.distance_meters!, segments: [[[e.origin.longitude, e.origin.latitude], [e.destination.longitude, e.destination.latitude]]] },
  })) };
}
