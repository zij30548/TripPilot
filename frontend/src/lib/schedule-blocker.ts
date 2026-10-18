import { scheduleMinutes, type ScheduleResponse } from "@/types/schedule";

/** Explain the first stopped required place, using the verified prefix, not every failed trial edge. */
export function scheduleBlocker(response: ScheduleResponse) {
  const first = response.unscheduled[0];
  if (!first) return null;
  const request = response.request;
  const roads: { from: string; to: string; edge: ScheduleResponse["edges"][number] | undefined }[] = [];
  if (first.reason !== "time_window" && first.reason !== "current_order_not_continued") {
    const required = new Set(request.must_visit_places.map(place => place.id));
    const previous = response.days.flatMap(day => day.items).findLast(item => item.kind === "visit" && required.has(item.place_id ?? ""));
    let origin = previous?.place_id ?? request.accommodation_place.id;
    const edgeFor = (from: string, to: string) => response.edges.find(edge => edge.origin.place_id === from && edge.destination.place_id === to);
    const lunchStart = scheduleMinutes(request.lunch.start_time)!;
    const lunchEnd = scheduleMinutes(request.lunch.end_time)!;
    const endAfter = (start: number, minutes: number) =>
      (request.lunch.enabled && minutes > 0 && start < lunchEnd && start + minutes > lunchStart ? lunchEnd : start) + minutes;
    if (previous) {
      // The scheduler first advances the day if even this stay alone cannot fit.
      // It has not looked at that day's adjacent edge in that case.
      const start = scheduleMinutes(previous.end_time)!;
      const duration = request.duration_settings.find(setting => setting.place_id === first.place_id)!.minutes;
      const outward = edgeFor(origin, first.place_id), backward = edgeFor(first.place_id, request.accommodation_place.id);
      const valid = (edge: typeof outward) => edge?.status === "ok" || edge?.status === "same_place";
      const earliest = endAfter(start, duration);
      const trialEnd = valid(outward) && valid(backward) ? endAfter(endAfter(endAfter(start, outward!.duration_minutes!), duration), backward!.duration_minutes!) : null;
      if (earliest > scheduleMinutes(request.daily_end_time)! || (trialEnd !== null && trialEnd > scheduleMinutes(request.daily_end_time)!)) origin = request.accommodation_place.id;
    }
    const pairs = [[origin, first.place_id], [first.place_id, request.accommodation_place.id]];
    for (const [from, to] of pairs) {
      const edge = edgeFor(from, to);
      if (!edge || (edge.status !== "ok" && edge.status !== "same_place")) roads.push({ from, to, edge });
    }
  }
  return { first, roads, followingCount: response.unscheduled.length - 1 };
}
