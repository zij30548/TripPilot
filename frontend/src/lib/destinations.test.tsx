import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DESTINATIONS, regionBelongsTo } from "./destinations";
import { cityRequest, cityWeather } from "./destination.test-fixtures";
import { forecast, deferred, tripRequest } from "./weather-schedule-check.test-fixtures";
import { isSavedDetail, createSnapshot, type SavedDetail } from "./saved-trips";
import { restoreSavedTrip, newWorkspaceInputs, applyEditedRequirements } from "./restore-trip";
import { isScheduleRequest, isScheduleResponse } from "@/types/schedule";
import { isWeatherForecastResponse } from "@/types/weather";
import { buildRequest, eligibleOptional } from "./schedule-input";
import { explanationSnapshot } from "./explanation-api";
import { savedCityFixture } from "./restore-trip.test-fixtures";
import { useWeatherForecast } from "./use-weather-forecast";
const weather = vi.hoisted(() => vi.fn());
vi.mock("./weather-api", async original => ({ ...await original<typeof import("./weather-api")>(), queryWeatherForecast: weather }));
beforeEach(() => vi.resetAllMocks());
describe("closed destination contract and archive compatibility", () => {
  it("actual Python disk API v2 outputs pass strict TS, restore, cost projection and AI compact contracts for all cities/modes", () => {
    const backend = resolve(process.cwd(), "../backend");
    const rows: SavedDetail[] = JSON.parse(execFileSync(`${backend}/.venv/bin/python`, ["-B", "tests/export_destination_contract.py"], { cwd: backend, env: { ...process.env, PYTHONPATH: backend, PYTHONDONTWRITEBYTECODE: "1" }, encoding: "utf8" }));
    expect(rows).toHaveLength(12);
    for (const row of rows) {
      expect(isSavedDetail(row)).toBe(true);
      const { inputs } = restoreSavedTrip(row, crypto.randomUUID());
      expect(buildRequest(inputs.request, inputs.settings, eligibleOptional(inputs.request, inputs.candidates))).toEqual(row.snapshot.schedule.request);
      expect(createSnapshot(inputs.request, row.snapshot.schedule, inputs.costs)).toEqual(row.snapshot);
      expect(explanationSnapshot(row.snapshot.schedule).request.destination_id).toBe(inputs.request.destination_id);
      const wrong = structuredClone(row.snapshot.schedule); wrong.request.destination_id = "unknown" as never;
      expect(isScheduleResponse(wrong, row.snapshot.schedule.request)).toBe(false);
    }
  });
  it("rejects absent city for new work, unknown/mismatching/districtless provenance and never aliases Shanghai", () => {
    expect(() => newWorkspaceInputs(tripRequest())).toThrow();
    for (const d of DESTINATIONS) {
      const req = cityRequest(tripRequest(), d.id), input = newWorkspaceInputs(req);
      expect(regionBelongsTo(req.accommodation_place!.region, d.id)).toBe(true);
      const schedule = buildRequest(req, input.settings, [])!;
      expect(isScheduleRequest(schedule)).toBe(true);
      delete schedule.accommodation_place.region;
      expect(isScheduleRequest(schedule)).toBe(false);
      const w = cityWeather(forecast(), d.id);
      expect(isWeatherForecastResponse(w, w.request)).toBe(true);
      expect(isWeatherForecastResponse({ ...w, adcode: "000000" }, w.request)).toBe(false);
    }
  });
  it("city confirmation clears costs/candidates/exclusions, retains unrelated fields; cancel does not mutate input", () => {
    const { inputs } = restoreSavedTrip(savedCityFixture(), "s");
    const before = structuredClone(inputs);
    const edited = cityRequest(inputs.request, "hangzhou");
    const applied = applyEditedRequirements(inputs, edited);
    expect(inputs).toEqual(before);
    expect(applied.inputs.candidates.excludedIds.size).toBe(0);
    expect(eligibleOptional(edited, applied.inputs.candidates)).toEqual([]);
    expect(applied.inputs.costs).toEqual({ tickets: {}, meals: {}, accommodation: "", other: "" });
    expect(applied.inputs.request.travelers).toBe(before.request.travelers);
    expect(applied.notices.join()).toContain("目的地已变化");
    expect(applied.notices.join()).not.toContain("原估算保留");
  });
  it("the actual weather transport preserves city in sent snapshot and rejects legacy Shanghai response", async () => {
    const { queryWeatherForecast } = await vi.importActual<typeof import("./weather-api")>("./weather-api");
    const hangzhou = cityWeather(forecast(), "hangzhou");
    vi.mocked(fetch).mockResolvedValueOnce(new Response(JSON.stringify(hangzhou)));
    expect(await queryWeatherForecast(hangzhou.request)).toEqual(hangzhou);
    expect(JSON.parse(vi.mocked(fetch).mock.calls.at(-1)![1]!.body as string)).toEqual(hangzhou.request);
    vi.mocked(fetch).mockResolvedValueOnce(new Response(JSON.stringify(forecast())));
    await expect(queryWeatherForecast(hangzhou.request)).rejects.toThrow("格式无效");
  });
  it.each(["success", "error"])("weather city ABA isolates old %s/finally with identical dates and same latest city", async outcome => {
    const a = cityWeather(forecast()), b = cityWeather(forecast(), "beijing"), pending = deferred<typeof a>();
    weather.mockReturnValueOnce(pending.promise).mockResolvedValueOnce(a);
    const view = renderHook(({ r }) => useWeatherForecast(r), { initialProps: { r: a.request } });
    let old!: Promise<boolean>; act(() => { old = view.result.current.query(); });
    const signal = weather.mock.calls[0][1];
    view.rerender({ r: b.request }); view.rerender({ r: a.request });
    expect(signal.aborted).toBe(true); expect(weather).toHaveBeenCalledTimes(1);
    await act(async () => { if (outcome === "success") pending.resolve(a); else pending.reject(new Error("old")); await old; });
    expect(view.result.current.state.response).toBeNull(); expect(view.result.current.state.status).toBe("idle");
    await act(async () => { await view.result.current.query(); }); expect(view.result.current.state.response).toEqual(a);
  });
});
