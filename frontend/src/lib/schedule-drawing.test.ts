import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { schedule } from "./weather-schedule-check.test-fixtures";
import { savedFixture } from "./restore-trip.test-fixtures";
import { readScheduleDelivery, scheduleRouteFor, validateDrawing } from "./schedule-drawing";
import { drawingFixture } from "./schedule-drawing.test-fixtures";
import type { ScheduleResponse } from "@/types/schedule";
import { createSnapshot, canonical } from "./saved-trips";
import { explanationSnapshot } from "./explanation-api";
import { querySchedulePreview } from "./schedule-api";

describe("bounded, separate live drawing delivery", () => {
  it("validates real Python serialization without changing the strict core", () => {
    const backend = resolve(process.cwd(), "../backend");
    const data = JSON.parse(execFileSync(`${backend}/.venv/bin/python`, ["-B", "-m", "tests.test_schedule_drawing"], { cwd: backend, encoding: "utf8" }));
    const delivery = readScheduleDelivery(data, data.schedule.request)!;
    expect(delivery.drawing.status).toBe("valid"); expect(delivery.schedule).toEqual(data.schedule);
    expect(data.drawing.entries).toHaveLength(3); expect(data.schedule.edges).toHaveLength(5);
  });
  it.each(["origin", "destination", "queried_at", "seconds", "distance", "extra", "coordinate", "duplicate", "missing", "mode", "segments", "points"])("rejects %s but preserves valid scheduling core", key => {
    const core = schedule(), drawing = drawingFixture(core), entry = drawing.entries[0];
    if (key === "origin") entry.origin = entry.destination;
    if (key === "destination") entry.destination = entry.origin;
    if (key === "queried_at") entry.queried_at = "2026-01-01T00:00:00Z";
    if (key === "seconds") entry.route!.duration_seconds++;
    if (key === "distance") entry.route!.distance_meters++;
    if (key === "extra") Object.assign(entry, { secret: "rejected" });
    if (key === "coordinate") entry.route!.segments[0][0][0] = 181;
    if (key === "duplicate") drawing.entries[1] = entry;
    if (key === "missing") drawing.entries.pop();
    if (key === "mode") Object.assign(entry, { transport_mode: "transit" });
    if (key === "segments") entry.route!.segments = Array(257).fill(entry.route!.segments[0]);
    if (key === "points") entry.route!.segments[0] = Array(6001).fill([121, 31]);
    const delivery = readScheduleDelivery({ schedule: core, drawing }, core.request)!;
    expect(delivery.drawing.status).toBe("invalid"); expect(delivery.schedule).toBe(core);
  });
  it("legacy, missing, oversize, zero movement and actual current-day occurrence states", () => {
    const core = schedule(); const d = drawingFixture(core);
    expect(readScheduleDelivery(core, core.request)?.drawing.status).toBe("missing");
    d.entries[0].status = "limit_exceeded"; d.entries[0].route = null;
    const checked = validateDrawing(d, core);
    expect(checked.status).toBe("valid"); expect(scheduleRouteFor(core, core.days[0].date, 0, checked)?.message).toContain("容量");
    expect(scheduleRouteFor(core, "2026-11-01", 0, checked)).toBeNull();
    expect(scheduleRouteFor(core, core.days[0].date, 1, checked)).toBeNull();
    core.edges[0].status = "same_place";
    expect(scheduleRouteFor(core, core.days[0].date, 0)?.message).toContain("无需移动");
  });
  it("negotiates once, and invalid drawing cannot invalidate costs/archive/explanation or relax core checks", async () => {
    const archive = savedFixture().snapshot, core = archive.schedule;
    vi.mocked(fetch).mockResolvedValue({ ok: true, json: async () => ({ schedule: core, drawing: { invalid: true } }) } as Response);
    const delivery = await querySchedulePreview(core.request, undefined, true);
    expect(fetch).toHaveBeenCalledOnce(); expect(vi.mocked(fetch).mock.calls[0][0]).toContain("?drawing=walking-v1");
    expect("schedule" in delivery && delivery.drawing.status).toBe("invalid");
    const snapshot = createSnapshot(archive.requirements, core, archive.costs);
    expect(canonical(snapshot)).toBe(canonical(archive));
    expect(JSON.stringify(explanationSnapshot(core))).not.toContain("segments");
    expect(() => createSnapshot(archive.requirements, { ...core, drawing: {} } as ScheduleResponse, archive.costs)).toThrow();
  });
  it("explicit v1 projection retains transit geometry, canonical contents and original query times", () => {
    const archive = savedFixture({ transport_mode: "transit" }).snapshot;
    const leg = archive.schedule.edges[0].transit_route!.legs[0];
    leg.geometry = [[[121, 31], [121.01, 31]]];
    const next = createSnapshot(archive.requirements, archive.schedule, archive.costs);
    expect(canonical(next)).toBe(canonical(archive));
    expect(next.schedule.edges[0].transit_route!.legs[0].geometry).toEqual(leg.geometry);
    expect(JSON.stringify(explanationSnapshot(next.schedule))).not.toContain("geometry");
  });
  it("the whole drawing batch is bounded independently of valid per-edge geometry", () => {
    const core = schedule(), d = drawingFixture(core);
    // Extra entries/unused edges cannot be smuggled in even with valid coordinates.
    d.entries.push({ ...d.entries[0], edge_id: "unused" });
    expect(validateDrawing(d, core).status).toBe("invalid");
    const unknownCore = { ...core, segments: [] };
    expect(readScheduleDelivery({ schedule: unknownCore, drawing: drawingFixture(core) }, core.request)).toBeNull();
  });
});
