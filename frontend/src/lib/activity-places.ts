import type { Place } from "@/types/place";
import type { Activity, DayPlan } from "@/types/trip";

export type ActivityPlaceBindings = Record<string, Place>;

// Names can repeat, and an activity id may be reused on a different day.
export function activityPlaceKey(day: Pick<DayPlan, "day" | "date">, activity: Pick<Activity, "id">): string {
  return JSON.stringify([day.day, day.date, activity.id]);
}
