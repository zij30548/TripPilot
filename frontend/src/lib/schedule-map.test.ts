import { describe, expect, it } from "vitest";
import { scheduleMapData } from "./schedule-map";
import { home, optionals, required, schedule } from "./weather-schedule-check.test-fixtures";

describe("official day place projection (controlled snapshots)", () => {
  it("uses actual visit order and response-owned snapshots, not same-name identity or unused optionals", () => {
    const r = schedule(), before = JSON.stringify(r);
    const data = scheduleMapData(r, r.days[0].date);
    expect(data.visits.map(v => [v.number, v.placeId])).toEqual([[1, required.id], [2, optionals[0].id]]);
    expect(data.places.map(p => p.id)).toEqual([home.id, required.id, optionals[0].id]);
    expect(data.labels).toEqual({ [home.id]: "住", [required.id]: "1", [optionals[0].id]: "2" });
    expect(data.places.slice(1).map(p => p.name)).toEqual(["同名地点", "同名地点"]);
    expect(JSON.stringify(r)).toBe(before);
  });
  it("deduplicates home/visit/return but retains both home and ordered visit meanings", () => {
    const r = schedule(); r.request.must_visit_places[0] = home;
    r.days[0].items.find(i => i.kind === "visit")!.place_id = home.id;
    const data = scheduleMapData(r, r.days[0].date);
    expect(data.places.map(p => p.id)).toEqual([home.id, optionals[0].id]);
    expect(data.labels[home.id]).toBe("住·1"); expect(data.visits[0].place).toBe(home);
  });
  it("ignores unarranged requirements, other dates, wait/lunch and request-only places", () => {
    const r = schedule(); const visit = r.days[0].items.find(i => i.kind === "visit")!;
    r.request.must_visit_places.push({ ...home, id: "unarranged" });
    r.days.push({ date: "2026-10-11", items: [{ ...visit, place_id: optionals[0].id }], return_time: null });
    r.days[0].items = [visit, { ...visit, kind: "lunch", place_id: null }, { ...visit, kind: "wait", place_id: null }];
    expect(scheduleMapData(r, r.days[0].date).places.map(p => p.id)).toEqual([home.id, required.id]);
    expect(scheduleMapData(r, "2026-10-11").visits.map(v => v.number)).toEqual([1]);
  });
  it("has no markers even for home on empty or absent dates", () => {
    const r = schedule(); r.days[0].items = [];
    for (const date of [r.days[0].date, "2027-01-01"]) expect(scheduleMapData(r, date).places).toEqual([]);
  });
  it("does not fabricate coordinates or lose visits when map coordinates/snapshots are unusable", () => {
    const r = structuredClone(schedule()); r.request.must_visit_places[0].longitude = NaN;
    r.days[0].items.filter(i => i.kind === "visit")[1].place_id = "missing";
    const data = scheduleMapData(r, r.days[0].date);
    expect(data.invalidCount).toBe(2); expect(data.visits).toHaveLength(2);
    expect(data.visits[1].place).toBeNull(); expect(data.places[1].longitude).toBeNaN();
  });
  it("transit geometry and transport items never enter the place projection", () => {
    const r = schedule(), original = scheduleMapData(r, r.days[0].date);
    r.request.transport_mode = "transit"; r.days[0].items.forEach(i => { if (i.kind === "walk") i.kind = "transit"; });
    expect(scheduleMapData(r, r.days[0].date)).toEqual(original);
  });
});
