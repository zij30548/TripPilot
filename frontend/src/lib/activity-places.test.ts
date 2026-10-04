import { describe, expect, it } from "vitest";

import { activityPlaceKey } from "@/lib/activity-places";

describe("activityPlaceKey", () => {
  it("isolates repeated activity ids by both day number and date", () => {
    const activity = { id: "same-id" };
    const first = activityPlaceKey({ day: 1, date: "2026-10-10" }, activity);
    const nextDay = activityPlaceKey({ day: 2, date: "2026-10-11" }, activity);
    const differentDate = activityPlaceKey({ day: 1, date: "2026-10-11" }, activity);
    const differentDayNumber = activityPlaceKey({ day: 2, date: "2026-10-10" }, activity);
    expect(new Set([first, nextDay, differentDate, differentDayNumber]).size).toBe(4);
  });

  it("is stable for equivalent activity identity without relying on the activity name", () => {
    const day = { day: 1, date: "2026-10-10" };
    const first = { id: "same-id", name: "旧活动名称" };
    const renamed = { id: "same-id", name: "新活动名称" };
    expect(activityPlaceKey(day, first)).toBe(activityPlaceKey({ ...day }, renamed));
    expect(activityPlaceKey(day, first)).not.toBe(activityPlaceKey(day, { id: "other-id" }));
  });

  it("preserves delimiter-containing identities without composite-key collisions", () => {
    const first = activityPlaceKey({ day: 1, date: "2026-10-10" }, { id: 'id,with:delimiters["quoted"]' });
    const second = activityPlaceKey({ day: 1, date: "2026-10-10" }, { id: 'id,with:delimiters["quoted"]suffix' });
    expect(first).not.toBe(second);
    expect(JSON.parse(first)).toEqual([1, "2026-10-10", 'id,with:delimiters["quoted"]']);
  });
});
