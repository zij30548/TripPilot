import { describe, expect, it } from "vitest";
import { adoptionIssue, compareSchedules, prepareAdjustmentInput } from "./schedule-adjustment";
import { captureFixture, adjustmentResponse } from "./schedule-adjustment.test-fixtures";
import { optionals, required } from "./weather-schedule-check.test-fixtures";

const prepare = (base = captureFixture()) => prepareAdjustmentInput(base, optionals[0].id, { ...base.candidates, excludedIds: new Set([optionals[0].id]) });
describe("adjustment snapshots and comparison", () => {
  it("reuses first three selection, excludes only proposed target, defaults new stays separately without changing source", () => {
    const base = captureFixture(), before = structuredClone(base);
    base.settings.stays[required.id] = { value: "45", source: "user" };
    const expected = structuredClone(base), result = prepare(base).value!;
    expect(result.request.optional_places!.map((p) => p.id)).toEqual(optionals.slice(1).map((p) => p.id));
    expect(result.request.duration_settings).toEqual([{ place_id: required.id, minutes: 45, source: "user" }, ...optionals.slice(1).map((p) => ({ place_id: p.id, minutes: 60, source: "default" }))]);
    expect(base).toEqual(expected); expect(before.candidates.excludedIds.size).toBe(0);
    result.settings.stays[required.id].value = "99"; result.candidates.excludedIds = new Set();
    expect(base.settings.stays[required.id].value).toBe("45");
  });
  it("uses a promoted place's historical user setting, no name matching", () => {
    const base = captureFixture(); base.settings.stays[optionals[3].id] = { value: "90", source: "user" };
    expect(prepare(base).value!.request.duration_settings.at(-1)).toEqual({ place_id: optionals[3].id, minutes: 90, source: "user" });
    expect(prepare(base).value!.request.must_visit_places).toEqual([required]); // Same name as excluded POI, distinct ID.
  });
  it.each(["required", "not_visited", "batch", "extra_exclusion", "loading"])("rejects invalid context %s", (kind) => {
    const base = captureFixture(), target = kind === "required" ? required.id : kind === "not_visited" ? optionals[3].id : optionals[0].id;
    const candidates = structuredClone(base.candidates); candidates.excludedIds = new Set([target]);
    if (kind === "batch") candidates.response!.queried_at = "2026-10-10T04:00:00Z";
    if (kind === "extra_exclusion") candidates.excludedIds = new Set([target, optionals[2].id]);
    if (kind === "loading") candidates.loading = true;
    expect(prepareAdjustmentInput(base, target, candidates).value).toBeNull();
  });
  it("refuses an empty proposed input without inventing a visit", () => {
    const base = captureFixture(); base.request.must_visit_places = []; base.request.must_visit = [];
    base.candidates.response!.candidates = base.candidates.response!.candidates.filter((c) => c.place.id === optionals[0].id);
    expect(prepare(base).message).toContain("没有必去或可选");
  });
  it.each(["walking", "transit"] as const)("keeps %s throughout and allows partial with every required visit", (mode) => {
    const base = captureFixture(); base.settings.transportMode = mode;
    base.original = adjustmentResponse({ ...base.original.request, transport_mode: mode });
    const proposed = prepare(base).value!.request, response = adjustmentResponse(proposed);
    expect(response.status).toBe("partial"); expect(proposed.transport_mode).toBe(mode);
    expect(adoptionIssue(base, optionals[0].id, proposed, response)).toBeNull();
    expect(response.days[0].items.filter((i) => i.kind !== "visit").every((i) => i.kind === (mode === "walking" ? "walk" : "transit"))).toBe(true);
  });
  it("allows no new optional when required visits are complete, but never allows incomplete required or no visits", () => {
    const base = captureFixture(), proposed = prepare(base).value!.request;
    expect(adoptionIssue(base, optionals[0].id, proposed, adjustmentResponse(proposed, "required_only"))).toBeNull();
    expect(adoptionIssue(base, optionals[0].id, proposed, adjustmentResponse(proposed, "empty"))).toContain("未完整安排必去");
    const withoutRequired = { ...proposed, must_visit_places: [], duration_settings: proposed.duration_settings.filter((s) => s.place_id !== required.id) };
    const noRequiredBase = structuredClone(base); noRequiredBase.original = adjustmentResponse({ ...base.original.request, must_visit_places: [], duration_settings: base.original.request.duration_settings.filter((s) => s.place_id !== required.id) });
    expect(adoptionIssue(noRequiredBase, optionals[0].id, withoutRequired, adjustmentResponse(withoutRequired, "empty"))).toContain("没有实际安排");
  });
  it.each(["target", "mode", "dates", "required", "return", "duration"])("cannot adopt mutated %s", (field) => {
    const base = captureFixture(), proposed = prepare(base).value!.request, response = adjustmentResponse(proposed);
    if (field === "target") proposed.optional_places![0] = optionals[0];
    if (field === "mode") response.request.transport_mode = "transit";
    if (field === "dates") response.days[0].date = "2026-10-11";
    if (field === "required") response.request.must_visit_places = [];
    if (field === "return") response.days[0].return_time = null;
    if (field === "duration") response.edges[0].duration_minutes = 900;
    expect(adoptionIssue(base, optionals[0].id, proposed, response)).not.toBeNull();
  });
  it("compares ID + actual date/time, includes required changes, and keeps missing return null", () => {
    const base = captureFixture(), next = adjustmentResponse(prepare(base).value!.request);
    next.days[0].items.find((i) => i.kind === "visit")!.start_time = "10:00";
    next.days[0].items.find((i) => i.kind === "visit")!.end_time = "11:00";
    next.days[0].date = "2026-10-11";
    const diff = compareSchedules(base.original, next);
    expect(diff.removed.map((v) => v.place.id)).toEqual([optionals[0].id]);
    expect(diff.added.map((v) => v.place.id)).toEqual([optionals[1].id]);
    expect(diff.retained[0]).toMatchObject({ changed: true, before: { role: "must_visit", date: "2026-10-10", start: "09:01" }, after: { date: "2026-10-11", start: "10:00", end: "11:00" } });
    expect(diff.returns).toEqual([{ date: "2026-10-10", before: "11:03", after: null }, { date: "2026-10-11", before: null, after: "11:03" }]);
  });
});
