import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useWeatherCheckRevision, useWeatherScheduleCheck } from "./use-weather-schedule-check";
import { useSchedulePreview } from "./use-schedule-preview";
import { useWeatherForecast } from "./use-weather-forecast";
import { useCandidatePool } from "./use-candidate-pool";
import { candidateResponse, deferred, forecast, now, optionals, required, schedule, tripRequest } from "./weather-schedule-check.test-fixtures";
import type { ScheduleRequest, ScheduleResponse } from "@/types/schedule";
import type { WeatherForecastResponse } from "@/types/weather";

const api = vi.hoisted(() => ({ schedule: vi.fn(), weather: vi.fn(), candidates: vi.fn() }));
vi.mock("./schedule-api", async (original) => ({ ...await original<typeof import("./schedule-api")>(), querySchedulePreview: api.schedule }));
vi.mock("./weather-api", async (original) => ({ ...await original<typeof import("./weather-api")>(), queryWeatherForecast: api.weather }));
vi.mock("./candidates-api", async (original) => ({ ...await original<typeof import("./candidates-api")>(), queryCandidates: api.candidates }));
function useHarness() {
  const revision = useWeatherCheckRevision();
  const draft = useSchedulePreview(tripRequest(), revision.invalidate);
  const candidates = useCandidatePool(tripRequest(), draft.updateCandidates);
  const weather = useWeatherForecast({ start_date: "2026-10-10", end_date: "2026-10-10" }, revision.invalidate);
  const check = useWeatherScheduleCheck({ schedule: draft, candidates, weather }, revision);
  return { draft, candidates, weather, check };
}
async function ready() {
  const view = renderHook(useHarness);
  await act(async () => { await view.result.current.candidates.query(); });
  await act(async () => { await view.result.current.draft.query(); await view.result.current.weather.query(); });
  await api.schedule.mock.results[0]?.value;
  expect(view.result.current.draft.state, "draft prepared").toMatchObject({ status: "success" });
  expect(view.result.current.weather.state, "weather prepared").toMatchObject({ status: "success" });
  act(() => { expect(view.result.current.check.check()).toBe(true); });
  return view;
}
beforeEach(() => {
  vi.useFakeTimers(); vi.setSystemTime(now); vi.clearAllMocks();
  api.schedule.mockImplementation(async (request: ScheduleRequest) => schedule(request));
  api.weather.mockImplementation(async () => forecast()); api.candidates.mockImplementation(async () => candidateResponse());
});
afterEach(() => vi.useRealTimers());

describe("result-local weather check invalidation and action boundary", () => {
  it("annotations and checks are local, preserve draft, roles use IDs even with identical names", async () => {
    const { result } = await ready(); const before = result.current.draft.state.response;
    act(() => result.current.check.annotate(required.id, "outdoor"));
    expect(result.current.check.valid).toBe(false);
    act(() => result.current.check.check());
    expect(result.current.check.valid).toBe(true);
    expect(result.current.check.report!.days[0].visits.map((v) => v.exposure)).toEqual(["outdoor", "unknown"]);
    act(() => { expect(result.current.check.canAdjustNow(required.id)).toBe(false); expect(result.current.check.canAdjustNow(optionals[3].id)).toBe(false); });
    expect(result.current.draft.state.response).toBe(before);
    expect(api.schedule).toHaveBeenCalledTimes(1); expect(api.weather).toHaveBeenCalledTimes(1); expect(api.candidates).toHaveBeenCalledTimes(1);
  });
  it("ordinary candidate exclusion still clears draft, promotes fourth candidate and requires manual query; IDs retain labels", async () => {
    const { result } = await ready();
    act(() => result.current.check.annotate(required.id, "indoor"));
    act(() => result.current.check.annotate(optionals[0].id, "outdoor"));
    act(() => result.current.check.check());
    const old = result.current.check.canAdjustNow;
    act(() => { expect(old(optionals[0].id)).toBe(true); result.current.candidates.exclude(optionals[0].id); expect(old(optionals[0].id)).toBe(false); });
    expect(result.current.candidates.excludedIds.has(optionals[0].id)).toBe(true);
    expect(result.current.draft.state.response).toBeNull(); expect(result.current.check.valid).toBe(false);
    expect(result.current.draft.optionalPlaces.map((p) => p.id)).toEqual(optionals.slice(1).map((p) => p.id));
    expect(api.schedule).toHaveBeenCalledTimes(1);
    await act(async () => { await result.current.draft.query(); });
    act(() => result.current.check.check());
    expect(result.current.check.report!.days[0].visits.map((v) => [v.place.id, v.exposure])).toEqual([[required.id, "indoor"], [optionals[1].id, "unknown"]]);
    act(() => { expect(old(optionals[0].id)).toBe(false); result.current.candidates.restore(optionals[0].id); });
    expect(result.current.draft.state.response).toBeNull();
    await act(async () => { await result.current.draft.query(); });
    expect(result.current.check.annotations.get(optionals[0].id)).toBe("outdoor");
    expect(result.current.check.valid).toBe(false);
  });
  it.each(["annotation", "mode", "stay", "lunch", "candidate", "clear"])("rejects old check/exclude handlers within the same %s change event, including ABA", async (kind) => {
    const { result } = await ready(); const old = result.current.check;
    act(() => {
      if (kind === "annotation") { old.annotate(required.id, "indoor"); old.annotate(required.id, "unknown"); }
      if (kind === "mode") { result.current.draft.setTransportMode("transit"); result.current.draft.setTransportMode("walking"); }
      if (kind === "stay") { result.current.draft.setStayMinutes(required.id, "90"); result.current.draft.setStayMinutes(required.id, "60"); }
      if (kind === "lunch") { result.current.draft.setLunchEnabled(false); result.current.draft.setLunchEnabled(true); }
      if (kind === "candidate") { result.current.candidates.exclude(optionals[1].id); result.current.candidates.restore(optionals[1].id); }
      if (kind === "clear") old.clear();
      expect(old.canAdjustNow(optionals[0].id)).toBe(false); expect(old.check()).toBe(false);
    });
    expect(result.current.check.valid).toBe(false); expect(result.current.candidates.excludedIds.has(optionals[0].id)).toBe(false);
    expect(api.schedule).toHaveBeenCalledTimes(1); expect(api.weather).toHaveBeenCalledTimes(1);
  });
  it("weather start invalidates immediately but not the draft; failed refresh needs a NEW check with previous provenance", async () => {
    const { result } = await ready(); const draft = result.current.draft.state.response, old = result.current.check;
    const pending = deferred<WeatherForecastResponse>(); api.weather.mockReturnValueOnce(pending.promise);
    let task!: Promise<boolean>;
    act(() => { task = result.current.weather.query(); expect(old.canAdjustNow(optionals[0].id)).toBe(false); });
    expect(result.current.check.valid).toBe(false); expect(result.current.draft.state.response).toBe(draft);
    await act(async () => { pending.reject(new Error("controlled error")); await task; });
    expect(result.current.check.valid).toBe(false); expect(result.current.draft.state.response).toBe(draft);
    act(() => result.current.check.check());
    expect(result.current.check.valid).toBe(true);
    expect(result.current.check.report).toMatchObject({ showingPrevious: true, queriedAt: forecast().queried_at, reportedAt: forecast().reported_at });
    expect(result.current.check.report!.refreshError).toContain("查询天气失败");
    act(() => { expect(old.canAdjustNow(optionals[0].id)).toBe(false); });
  });
  it.each([
    { module: "weather", outcome: "success" }, { module: "weather", outcome: "error" },
    { module: "candidates", outcome: "success" }, { module: "candidates", outcome: "error" },
    { module: "draft", outcome: "success" }, { module: "draft", outcome: "error" },
  ] as const)("$module: old $outcome and finally cannot revive a report or unlock new work", async ({ module, outcome }) => {
    const { result } = await ready(); const old = result.current.check;
    const pending = deferred<WeatherForecastResponse | ScheduleResponse | ReturnType<typeof candidateResponse>>();
    api[module === "draft" ? "schedule" : module].mockReturnValueOnce(pending.promise);
    let task!: Promise<boolean>;
    act(() => { task = result.current[module].query(); expect(old.canAdjustNow(optionals[0].id)).toBe(false); });
    act(() => result.current[module].invalidate());
    const replacement = deferred<WeatherForecastResponse | ScheduleResponse | ReturnType<typeof candidateResponse>>();
    api[module === "draft" ? "schedule" : module].mockReturnValueOnce(replacement.promise);
    let newTask!: Promise<boolean>;
    act(() => { newTask = result.current[module].query(); });
    const latest = result.current[module].state;
    const response = module === "weather" ? forecast() : module === "candidates" ? candidateResponse() : schedule(api.schedule.mock.calls.at(-1)![0]);
    await act(async () => { if (outcome === "error") pending.reject(new Error("late old error")); else pending.resolve(response); await task; });
    expect(result.current[module].state).toBe(latest); expect(result.current.check.valid).toBe(false);
    expect(latest.status).toBe("loading");
    await act(async () => { expect(await result.current[module].query()).toBe(false); });
    expect(api[module === "draft" ? "schedule" : module]).toHaveBeenCalledTimes(3);
    await act(async () => { replacement.resolve(response); await newTask; });
    expect(result.current.check.valid).toBe(false);
    expect(result.current.candidates.excludedIds.has(optionals[0].id)).toBe(false);
  });
  it("a replacement report invalidates the previous report's captured action even without any input changes", async () => {
    const { result } = await ready(); const old = result.current.check;
    act(() => result.current.check.check());
    act(() => { expect(old.canAdjustNow(optionals[0].id)).toBe(false); expect(result.current.check.canAdjustNow(optionals[0].id)).toBe(true); });
  });
  it.each(["midnight", "stale"])("rechecks %s at click time, not only when the display timer happens to update", async (boundary) => {
    if (boundary === "stale") api.weather.mockImplementation(async () => ({ ...forecast(), reported_at: "2026-10-09T10:00:00+08:00" }));
    const { result } = await ready(); const old = result.current.check;
    vi.setSystemTime(boundary === "midnight" ? "2026-10-10T16:00:00Z" : "2026-10-10T02:00:00.001Z");
    act(() => { expect(old.canAdjustNow(optionals[0].id)).toBe(false); });
    expect(result.current.check.valid).toBe(false);
    vi.setSystemTime(now); act(() => window.dispatchEvent(new Event("focus")));
    expect(result.current.check.valid).toBe(false); // Clock ABA never revives.
    expect(api.weather).toHaveBeenCalledTimes(1);
  });
  it("updates on midnight timer, visibility and focus without querying", async () => {
    vi.setSystemTime("2026-10-10T15:59:59.999Z"); const { result } = await ready();
    expect(result.current.check.valid).toBe(true);
    act(() => vi.advanceTimersByTime(1)); expect(result.current.check.valid).toBe(false);
    act(() => result.current.check.check());
    expect(result.current.check.report!.days[0].visits.every((v) => !v.canAdjust)).toBe(true);
    vi.setSystemTime("2026-10-11T02:00:00Z");
    act(() => document.dispatchEvent(new Event("visibilitychange")));
    expect(result.current.check.valid).toBe(false);
    expect(api.weather).toHaveBeenCalledTimes(1);
  });
  it("all optional exclusion without required leaves a validation error, no fabricated substitute; edit/unmount clears annotations", async () => {
    const view = await ready();
    act(() => view.result.current.check.annotate(required.id, "indoor"));
    act(() => view.result.current.check.clear()); expect(view.result.current.check.annotations.size).toBe(0);
    const old = view.result.current.check; view.unmount(); expect(old.check()).toBe(false); expect(old.canAdjustNow(optionals[0].id)).toBe(false);
    const next = renderHook(() => {
      const request = { ...tripRequest(), must_visit: [], must_visit_places: [] };
      const draft = useSchedulePreview(request); const pool = useCandidatePool(request, draft.updateCandidates); return { draft, pool };
    });
    await act(async () => { await next.result.current.pool.query(); });
    act(() => optionals.forEach((place) => next.result.current.pool.exclude(place.id)));
    expect(next.result.current.draft.canQuery).toBe(false);
    await act(async () => { await next.result.current.draft.query(); });
    expect(api.schedule).toHaveBeenCalledTimes(1); expect(next.result.current.draft.state.response).toBeNull();
  });
});
