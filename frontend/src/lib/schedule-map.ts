import { hasValidCoordinates, type Place } from "@/types/place";
import type { ScheduleResponse } from "@/types/schedule";

export type ScheduleMapVisit = { itemIndex: number; number: number; placeId: string; place: Place | null };
export type ScheduleMapData = ReturnType<typeof scheduleMapData>;

// A presentation projection, never a route or a replacement for contract validation.
// Keep invalid/missing places in the timeline; do not invent map coordinates.
export function scheduleMapData(response: ScheduleResponse, date: string) {
  const home = response.request.accommodation_place;
  const snapshots = new Map([...response.request.must_visit_places, ...response.request.optional_places ?? []].map(p => [p.id, p]));
  snapshots.set(home.id, home);
  const visits: ScheduleMapVisit[] = [];
  response.days.find(d => d.date === date)?.items.forEach((item, itemIndex) => {
    if (item.kind === "visit" && item.place_id) visits.push({ itemIndex, number: visits.length + 1, placeId: item.place_id, place: snapshots.get(item.place_id) ?? null });
  });
  const unique = new Map<string, Place>();
  // An empty date must not appear populated just because there is accommodation.
  if (visits.length) {
    unique.set(home.id, home);
    visits.forEach(v => { if (v.place) unique.set(v.place.id, v.place); });
  }
  const places = [...unique.values()];
  const labels = Object.fromEntries(places.map(p => [p.id,
    [p.id === home.id ? "住" : null, ...visits.filter(v => v.placeId === p.id).map(v => v.number)].filter(v => v !== null).join("·"),
  ]));
  return { date, destinationId: response.request.destination_id, home, visits, places, labels,
    invalidCount: places.filter(p => !hasValidCoordinates(p)).length + visits.filter(v => !v.place).length };
}
