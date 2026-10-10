import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useWeatherCheckRevision, useWeatherScheduleCheck } from "./use-weather-schedule-check";
import { useSchedulePreview } from "./use-schedule-preview";
import { useWeatherForecast } from "./use-weather-forecast";
import { useCandidatePool } from "./use-candidate-pool";
import { useScheduleAdjustment } from "./use-schedule-adjustment";
import { adjustmentResponse } from "./schedule-adjustment.test-fixtures";
import { candidateResponse, deferred, forecast, now, optionals, required, tripRequest } from "./weather-schedule-check.test-fixtures";
import { ScheduleError } from "./schedule-api";
import type { ScheduleRequest, ScheduleResponse } from "@/types/schedule";

const api = vi.hoisted(() => ({ schedule: vi.fn(), weather: vi.fn(), candidates: vi.fn() }));
vi.mock("./schedule-api", async (original) => ({ ...await original<typeof import("./schedule-api")>(), querySchedulePreview: api.schedule }));
vi.mock("./weather-api", async (original) => ({ ...await original<typeof import("./weather-api")>(), queryWeatherForecast: api.weather }));
vi.mock("./candidates-api", async (original) => ({ ...await original<typeof import("./candidates-api")>(), queryCandidates: api.candidates }));
function useHarness(noRequired = false) {
  const request = noRequired ? { ...tripRequest(), must_visit: [], must_visit_places: [] } : tripRequest();
  const revision = useWeatherCheckRevision();
  const draft = useSchedulePreview(request, revision.invalidate);
  const candidates = useCandidatePool(request, draft.updateCandidates);
  const weather = useWeatherForecast({ start_date: request.start_date, end_date: request.end_date }, revision.invalidate);
  const check = useWeatherScheduleCheck({ schedule: draft, candidates, weather }, revision);
  const preview = useScheduleAdjustment({ schedule: draft, candidates, weather, check }, revision);
  return { draft, candidates, weather, check, preview };
}
async function ready(mode: "walking" | "transit" = "walking", noRequired = false) {
  const view = renderHook(() => useHarness(noRequired));
  await act(async () => { await view.result.current.candidates.query(); });
  if (mode === "transit") act(() => view.result.current.draft.setTransportMode(mode));
  await act(async () => { await view.result.current.draft.query(); await view.result.current.weather.query(); });
  act(() => { expect(view.result.current.check.check()).toBe(true); });
  return view;
}
beforeEach(() => {
  vi.useFakeTimers(); vi.setSystemTime(now); vi.clearAllMocks();
  api.schedule.mockImplementation(async (request: ScheduleRequest) => adjustmentResponse(request));
  api.weather.mockImplementation(async () => forecast()); api.candidates.mockImplementation(async () => candidateResponse());
});
afterEach(() => vi.useRealTimers());

describe("isolated adjustment request and synchronous adoption", () => {
  it.each(["walking", "transit"] as const)("%s: preview is private; adopt exactly what was shown, then ordinary query uses identical input", async (mode) => {
    const { result } = await ready(mode);
    act(() => result.current.check.annotate(required.id, "indoor")); act(() => result.current.check.check());
    const original = result.current.draft.state.response, settings = result.current.draft.stays;
    const oldWeather = result.current.weather.state.response;
    await act(async () => { expect(await result.current.preview.start(optionals[0].id)).toBe(true); });
    expect(result.current.preview.state.response!.status).toBe("partial");
    expect(result.current.preview.canAdopt).toBe(true);
    expect(result.current.draft.state.response).toBe(original);
    expect(result.current.candidates.excludedIds.size).toBe(0);
    expect(result.current.draft.stays).toBe(settings);
    expect(settings[optionals[3].id]).toBeUndefined();
    expect(result.current.check.valid).toBe(true);
    const shown = result.current.preview.state.response!, adopt = result.current.preview.adopt;
    const input = api.schedule.mock.calls[1][0];
    expect(input.transport_mode).toBe(mode);
    act(() => { expect(adopt()).toBe(true); expect(adopt()).toBe(false); });
    expect(api.schedule).toHaveBeenCalledTimes(2);
    expect(result.current.draft.state.response).toEqual(shown);
    expect(result.current.candidates.excludedIds).toEqual(new Set([optionals[0].id]));
    expect(result.current.draft.optionalPlaces.map((p) => p.id)).toEqual(optionals.slice(1).map((p) => p.id));
    expect(result.current.draft.stays[optionals[3].id]).toEqual({ value: "60", source: "default" });
    expect(result.current.check.valid).toBe(false);
    expect(result.current.check.annotations.get(required.id)).toBe("indoor");
    expect(result.current.check.annotations.has(optionals[1].id)).toBe(false);
    expect(result.current.weather.state.response).toBe(oldWeather);
    await act(async () => { await result.current.draft.query(); });
    expect(api.schedule.mock.calls[2][0]).toEqual(input);
    expect(api.weather).toHaveBeenCalledTimes(1); expect(api.candidates).toHaveBeenCalledTimes(1);
    act(() => result.current.candidates.restore(optionals[0].id));
    expect(result.current.draft.state.response).toBeNull();
    expect(result.current.draft.optionalPlaces[0].id).toBe(optionals[0].id);
  });
  it.each(["loading", "ready"])("cancel %s preserves official draft, exclusions, settings and annotations", async (phase) => {
    const { result } = await ready(); const original = result.current.draft.state.response, settings = result.current.draft.stays;
    const pending = deferred<ScheduleResponse>(); api.schedule.mockReturnValueOnce(pending.promise);
    let task!: Promise<boolean>;
    act(() => { task = result.current.preview.start(optionals[0].id); });
    await act(async () => { expect(await result.current.preview.start(optionals[0].id)).toBe(false); });
    expect(api.schedule).toHaveBeenCalledTimes(2);
    const response = adjustmentResponse(api.schedule.mock.calls[1][0]);
    if (phase === "ready") await act(async () => { pending.resolve(response); await task; });
    act(() => { expect(result.current.preview.cancel()).toBe(true); });
    expect(api.schedule.mock.calls[1][1].aborted).toBe(true);
    if (phase === "loading") await act(async () => { pending.resolve(response); expect(await task).toBe(false); });
    expect(result.current.preview.state.status).toBe("idle");
    expect(result.current.draft.state.response).toBe(original); expect(result.current.draft.stays).toBe(settings);
    expect(result.current.candidates.excludedIds.size).toBe(0); expect(result.current.check.valid).toBe(true);
    expect(api.schedule).toHaveBeenCalledTimes(2);
  });
  it.each(["error", "timeout", "invalid"])("%s cannot replace the original or mutate formal inputs", async (kind) => {
    const { result } = await ready(); const original = result.current.draft.state.response, settings = result.current.draft.stays;
    if (kind === "invalid") api.schedule.mockResolvedValueOnce({ status: "complete" });
    else api.schedule.mockRejectedValueOnce(kind === "timeout" ? new ScheduleError("timeout", "请求超时") : new Error("controlled failure"));
    await act(async () => { expect(await result.current.preview.start(optionals[0].id)).toBe(false); });
    expect(result.current.preview.state.status).toBe("failed");
    expect(result.current.draft.state.response).toBe(original); expect(result.current.draft.stays).toBe(settings);
    expect(result.current.candidates.excludedIds.size).toBe(0); expect(result.current.preview.adopt()).toBe(false);
  });
  it.each(["success", "error"])("old %s/finally and old cancel cannot affect a new pending preview, including same-target ABA", async (outcome) => {
    const { result } = await ready(); const oldPending = deferred<ScheduleResponse>(); api.schedule.mockReturnValueOnce(oldPending.promise);
    let oldTask!: Promise<boolean>, newTask!: Promise<boolean>;
    act(() => { oldTask = result.current.preview.start(optionals[0].id); });
    const old = result.current.preview;
    act(() => old.cancel());
    const next = deferred<ScheduleResponse>(); api.schedule.mockReturnValueOnce(next.promise);
    act(() => { newTask = result.current.preview.start(optionals[0].id); });
    const active = result.current.preview.state;
    act(() => { expect(old.cancel()).toBe(false); expect(old.adopt()).toBe(false); });
    await act(async () => { if (outcome === "success") oldPending.resolve(adjustmentResponse(api.schedule.mock.calls[1][0])); else oldPending.reject(new Error("late error")); await oldTask; });
    expect(result.current.preview.state).toBe(active);
    await act(async () => { expect(await result.current.preview.start(optionals[0].id)).toBe(false); });
    expect(api.schedule).toHaveBeenCalledTimes(3);
    await act(async () => { next.resolve(adjustmentResponse(api.schedule.mock.calls[2][0])); expect(await newTask).toBe(true); });
  });
  it.each(["annotation", "report", "mode", "stay", "lunch", "candidates", "weather", "query", "edit"])("%s invalidates before the next render; cancel never restores the old official inputs", async (kind) => {
    const { result } = await ready();
    await act(async () => { await result.current.preview.start(optionals[0].id); });
    const old = result.current.preview, original = result.current.draft.state.response;
    let task: Promise<boolean> | undefined;
    act(() => {
      if (kind === "annotation") result.current.check.annotate(required.id, "outdoor");
      if (kind === "report") result.current.check.check();
      if (kind === "mode") { result.current.draft.setTransportMode("transit"); result.current.draft.setTransportMode("walking"); }
      if (kind === "stay") { result.current.draft.setStayMinutes(required.id, "90"); result.current.draft.setStayMinutes(required.id, "60"); }
      if (kind === "lunch") { result.current.draft.setLunchEnabled(false); result.current.draft.setLunchEnabled(true); }
      if (kind === "candidates") { result.current.candidates.exclude(optionals[1].id); result.current.candidates.restore(optionals[1].id); }
      if (kind === "weather") task = result.current.weather.query();
      if (kind === "query") task = result.current.draft.query();
      if (kind === "edit") { result.current.check.clear(); result.current.draft.invalidate(); }
      expect(old.adopt()).toBe(false);
    });
    if (task) await act(async () => { await task; });
    expect(result.current.preview.state.status).toBe("expired");
    expect(result.current.candidates.excludedIds.has(optionals[0].id)).toBe(false);
    const current = result.current.draft.state.response;
    act(() => result.current.preview.cancel());
    expect(result.current.draft.state.response).toBe(current);
    if (["annotation", "report", "weather"].includes(kind)) expect(current).toBe(original);
    if (["mode", "stay", "lunch", "candidates", "edit"].includes(kind)) expect(current).toBeNull();
  });
  it.each(["midnight", "stale"])("adopt rechecks %s even before the clock renders; clock reversal cannot revive preview", async (kind) => {
    if (kind === "stale") api.weather.mockResolvedValueOnce({ ...forecast(), reported_at: "2026-10-09T10:00:00+08:00" });
    const { result } = await ready(); await act(async () => { await result.current.preview.start(optionals[0].id); });
    vi.setSystemTime(kind === "midnight" ? "2026-10-10T16:00:00Z" : "2026-10-10T02:00:00.001Z");
    act(() => { expect(result.current.preview.adopt()).toBe(false); });
    expect(result.current.preview.state.status).toBe("expired");
    vi.setSystemTime(now); act(() => window.dispatchEvent(new Event("focus")));
    expect(result.current.preview.canAdopt).toBe(false); expect(api.schedule).toHaveBeenCalledTimes(2);
  });
  it("failed weather refresh retains original weather/draft but does not resurrect preview after recheck", async () => {
    const { result } = await ready(); await act(async () => { await result.current.preview.start(optionals[0].id); });
    const old = result.current.preview, original = result.current.draft.state.response;
    api.weather.mockRejectedValueOnce(new Error("controlled refresh failure"));
    await act(async () => { await result.current.weather.query(); });
    act(() => result.current.check.check());
    expect(result.current.check.report!.showingPrevious).toBe(true);
    expect(result.current.preview.state.status).toBe("expired"); expect(old.adopt()).toBe(false);
    expect(result.current.draft.state.response).toBe(original);
  });
  it("required-only partial is adoptable, but a valid empty/incomplete-required preview stays inspectable and cannot be adopted", async () => {
    const { result } = await ready();
    api.schedule.mockImplementationOnce(async (r) => adjustmentResponse(r, "empty"));
    await act(async () => { await result.current.preview.start(optionals[0].id); });
    expect(result.current.preview.state.status).toBe("ready"); expect(result.current.preview.canAdopt).toBe(false);
    expect(result.current.preview.adopt()).toBe(false); expect(result.current.candidates.excludedIds.size).toBe(0);
    act(() => result.current.preview.cancel());
    api.schedule.mockImplementationOnce(async (r) => adjustmentResponse(r, "required_only"));
    await act(async () => { await result.current.preview.start(optionals[0].id); });
    expect(result.current.preview.state.response!.status).toBe("partial");
    act(() => { expect(result.current.preview.adopt()).toBe(true); });
  });
  it("required target is rejected and removing the sole optional without required never sends an empty request", async () => {
    const view = await ready();
    await act(async () => { expect(await view.result.current.preview.start(required.id)).toBe(false); });
    view.unmount();
    api.candidates.mockResolvedValueOnce({ ...candidateResponse(), candidates: candidateResponse().candidates.filter((p) => p.role === "optional" && p.place.id === optionals[0].id) });
    const { result } = await ready("walking", true); const calls = api.schedule.mock.calls.length, original = result.current.draft.state.response;
    await act(async () => { expect(await result.current.preview.start(optionals[0].id)).toBe(false); });
    expect(result.current.preview.state.message).toContain("没有必去");
    expect(api.schedule).toHaveBeenCalledTimes(calls); expect(result.current.draft.state.response).toBe(original);
  });
  it.each(["success", "error"])("unmount cancels in-flight preview and rejects late %s", async (outcome) => {
    const view = await ready(); const pending = deferred<ScheduleResponse>(); api.schedule.mockReturnValueOnce(pending.promise);
    let task!: Promise<boolean>; act(() => { task = view.result.current.preview.start(optionals[0].id); });
    const old = view.result.current.preview; view.unmount();
    expect(api.schedule.mock.calls[1][1].aborted).toBe(true);
    await act(async () => { if (outcome === "success") pending.resolve(adjustmentResponse(api.schedule.mock.calls[1][0])); else pending.reject(new Error("late unmount")); expect(await task).toBe(false); });
    expect(old.adopt()).toBe(false);
  });
});
