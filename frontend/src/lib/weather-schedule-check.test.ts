import { describe, expect, it } from "vitest";
import { checkWeatherSchedule, scheduledVisits, weatherCheckTimeKey, type VisitExposure } from "./weather-schedule-check";
import { forecast, now, optionals, period, required, schedule } from "./weather-schedule-check.test-fixtures";

describe("pure weather and actual visit association", () => {
  it("uses actual visits, request identity and date, not same names, unused candidates or Mock days; does not mutate inputs", () => {
    const draft = schedule(), weather = forecast();
    draft.request.optional_places!.push(optionals[3]); // Not visited, never gets an invented date.
    draft.days.push({ date: "2026-10-11", items: [], return_time: null });
    const before = JSON.stringify([draft, weather]);
    const report = checkWeatherSchedule(draft, weather, new Map(), now);
    expect(report.days.map((day) => [day.date, day.visits.length])).toEqual([["2026-10-10", 2], ["2026-10-11", 0]]);
    expect(report.days[0].visits.map((v) => [v.place.id, v.role, v.exposure])).toEqual([[required.id, "must_visit", "unknown"], [optionals[0].id, "optional", "unknown"]]);
    expect(scheduledVisits(null)).toEqual([]);
    expect(JSON.stringify([draft, weather])).toBe(before);
  });
  it.each([["小雨", "晴", "precipitation", "no_trigger"], ["晴", "小雪", "no_trigger", "precipitation"], ["雨夹雪", "新描述", "precipitation", "unassessed"], [null, "小雨", "unassessed", "precipitation"], ["新雨天描述", "晴", "unassessed", "no_trigger"]])("keeps independent day/night evidence %s / %s", (day, night, dayStatus, nightStatus) => {
    const weather = forecast(); weather.days[0].day = period(day); weather.days[0].night = period(night);
    const visit = checkWeatherSchedule(schedule(), weather, new Map([[required.id, "outdoor"]]), now).days[0].visits[0];
    expect(visit.periods.map((p) => p.status)).toEqual([dayStatus, nightStatus]);
    expect(visit.periods.map((p) => p.description)).toEqual([day, night]);
    expect(visit.attention).toBe(dayStatus === "precipitation" || nightStatus === "precipitation");
    expect(visit.periods[0].explanation).not.toContain("夜间");
    expect(visit.periods[1].explanation).not.toContain("白天");
  });
  it.each<VisitExposure>(["indoor", "outdoor", "unknown"])("does not infer exposure; accepts only user's %s setting", (exposure) => {
    const visits = checkWeatherSchedule(schedule(), forecast(), new Map([[required.id, exposure]]), now).days[0].visits;
    expect(visits[0].attention).toBe(exposure === "outdoor");
    expect(visits[0].exposure).toBe(exposure);
    expect(visits[1].exposure).toBe("unknown");
  });
  it.each(["stale", "publication", "coverage", "past", "descriptions"])("does not authorize weather-based adjustment for %s", (reason) => {
    const weather = forecast(); let clock = now;
    if (reason === "stale") weather.reported_at = "2026-10-09T08:00:00+08:00";
    if (reason === "publication") { weather.reported_at = null; weather.report_time_status = "missing"; }
    if (reason === "coverage") weather.days = [{ date: "2026-10-10", status: "unavailable", day: null, night: null }];
    if (reason === "past") clock = Date.parse("2026-10-11T00:00:00+08:00");
    if (reason === "descriptions") { weather.days[0].day = period(null); weather.days[0].night = period("未识别天气"); }
    const visit = checkWeatherSchedule(schedule(), weather, new Map([[required.id, "outdoor"]]), clock).days[0].visits[0];
    expect(visit.attention).toBe(false); expect(visit.canAdjust).toBe(false);
    expect(visit.periods.every((p) => p.status === "unassessed")).toBe(true);
  });
  it("time key changes at Shanghai midnight and after, not at, exactly 24 hours", () => {
    const weather = forecast(); const boundary = Date.parse(weather.reported_at!) + 86400000;
    expect(weatherCheckTimeKey(weather, boundary)).toBe("2026-10-11|fresh");
    expect(weatherCheckTimeKey(weather, boundary + 1)).toBe("2026-10-11|stale");
    expect(weatherCheckTimeKey(weather, Date.parse("2026-10-10T15:59:59Z"))).toBe("2026-10-10|fresh");
    expect(weatherCheckTimeKey(weather, Date.parse("2026-10-10T16:00:00Z"))).toBe("2026-10-11|fresh");
  });
  it("keeps failed-refresh provenance without laundering the original query or publication", () => {
    const weather = forecast();
    const report = checkWeatherSchedule(schedule(), weather, new Map(), now, { showingPrevious: true, refreshError: "刷新失败" });
    expect(report).toMatchObject({ showingPrevious: true, refreshError: "刷新失败", queriedAt: weather.queried_at, reportedAt: weather.reported_at });
  });
});
