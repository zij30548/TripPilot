import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useSchedulePreview } from "./use-schedule-preview";
import { useScheduleExplanation } from "./use-schedule-explanation";
import { useWeatherCheckRevision, useWeatherScheduleCheck } from "./use-weather-schedule-check";
import { useWeatherForecast } from "./use-weather-forecast";
import { useCandidatePool } from "./use-candidate-pool";
import { useScheduleAdjustment } from "./use-schedule-adjustment";
import { adjustmentResponse } from "./schedule-adjustment.test-fixtures";
import { candidateResponse, deferred, forecast, now, optionals, required, tripRequest } from "./weather-schedule-check.test-fixtures";
import type { ExplanationResponse } from "./explanation-api";
import type { ScheduleRequest, ScheduleResponse } from "@/types/schedule";

const api = vi.hoisted(() => ({ schedule: vi.fn(), explain: vi.fn(), weather: vi.fn(), candidates: vi.fn() }));
vi.mock("./schedule-api", async (original) => ({ ...await original<typeof import("./schedule-api")>(), querySchedulePreview: api.schedule }));
vi.mock("./explanation-api", async (original) => ({ ...await original<typeof import("./explanation-api")>(), queryExplanation: api.explain }));
vi.mock("./weather-api", async (original) => ({ ...await original<typeof import("./weather-api")>(), queryWeatherForecast: api.weather }));
vi.mock("./candidates-api", async (original) => ({ ...await original<typeof import("./candidates-api")>(), queryCandidates: api.candidates }));
function useHarness() {
  const request = tripRequest(), revision = useWeatherCheckRevision();
  const schedule = useSchedulePreview(request, revision.invalidate);
  const explanation = useScheduleExplanation(schedule);
  const candidates = useCandidatePool(request, schedule.updateCandidates);
  const weather = useWeatherForecast({ start_date: request.start_date, end_date: request.end_date }, revision.invalidate);
  const check = useWeatherScheduleCheck({ schedule, candidates, weather }, revision);
  const adjustment = useScheduleAdjustment({ schedule, candidates, weather, check }, revision);
  return { schedule, explanation, candidates, weather, check, adjustment };
}
function result(response: ScheduleResponse, id = "test"): ExplanationResponse { return {
  contract_version: "schedule-explanation-v1", request_id: id, model: "qwen3.7-flash-2026-07-15", schedule_generated_at: response.generated_at, explained_at: response.generated_at,
  highlights: [{ message_code: "arrangement", fact_ids: ["f0"], text: "受控解释" }], limitations: ["未核实", "参考", "快照"], usage: { input_tokens: null, output_tokens: null },
}; }
async function ready() {
  const view = renderHook(() => useHarness());
  await act(async () => { await view.result.current.candidates.query(); await view.result.current.schedule.query(); });
  return view;
}
beforeEach(() => {
  vi.useFakeTimers(); vi.setSystemTime(now); vi.resetAllMocks();
  api.schedule.mockImplementation(async (request: ScheduleRequest) => adjustmentResponse(request));
  api.explain.mockImplementation(async (s: ScheduleResponse, id: string) => result(s, id));
  api.weather.mockImplementation(async () => forecast()); api.candidates.mockImplementation(async () => candidateResponse());
});
afterEach(() => vi.useRealTimers());

describe("official draft identity and isolated explanation", () => {
  it("explicit only, duplicate guarded; cancellation and old cancellation cannot stop a new call", async () => {
    const { result: view } = await ready(); expect(api.explain).not.toHaveBeenCalled();
    const first = deferred<ExplanationResponse>(); api.explain.mockReturnValueOnce(first.promise);
    act(() => { void view.current.explanation.explain(); void view.current.explanation.explain(); });
    expect(api.explain).toHaveBeenCalledTimes(1); const oldCancel = view.current.explanation.cancel;
    act(() => oldCancel()); expect(api.explain.mock.calls[0][2].aborted).toBe(true);
    const next = deferred<ExplanationResponse>(); api.explain.mockReturnValueOnce(next.promise);
    act(() => { void view.current.explanation.explain(); });
    act(() => oldCancel()); expect(api.explain.mock.calls[1][2].aborted).toBe(false);
    await act(async () => first.resolve(result(view.current.schedule.state.response!)));
    expect(view.current.explanation.loading).toBe(true);
    act(() => { void view.current.explanation.explain(); }); expect(api.explain).toHaveBeenCalledTimes(2);
    await act(async () => next.resolve(result(view.current.schedule.state.response!)));
    expect(view.current.explanation.state.status).toBe("success");
  });
  it.each(["success", "error"])("ABA isolates old %s and finally; old capture reads current draft, not old render", async (outcome) => {
    const { result: view } = await ready(); const oldCapture = view.current.schedule.capture, oldExplain = view.current.explanation.explain;
    const stale = deferred<ExplanationResponse>(); api.explain.mockReturnValueOnce(stale.promise);
    act(() => { void oldExplain(); });
    act(() => { view.current.schedule.setTransportMode("transit"); view.current.schedule.setTransportMode("walking"); expect(oldCapture()).toBeNull(); });
    expect(view.current.explanation.state.status).toBe("idle");
    api.schedule.mockImplementationOnce(async (request: ScheduleRequest) => ({ ...adjustmentResponse(request), generated_at: "2026-10-10T03:00:00Z" }));
    await act(async () => { await view.current.schedule.query(); });
    const current = view.current.schedule.capture()!; expect(oldCapture()).toEqual(current);
    const newest = deferred<ExplanationResponse>(); api.explain.mockReturnValueOnce(newest.promise);
    act(() => { void oldExplain(); });
    expect(api.explain.mock.calls[1][0]).toEqual(current.original);
    await act(async () => { if (outcome === "success") stale.resolve(result(current.original)); else stale.reject(new Error("old error")); });
    expect(view.current.explanation.state.status).toBe("loading");
    act(() => { void view.current.explanation.explain(); }); expect(api.explain).toHaveBeenCalledTimes(2);
    await act(async () => newest.resolve(result(current.original)));
    expect(view.current.explanation.state.status).toBe("success");
  });
  it.each(["stays", "lunch", "mode", "candidate", "refresh", "query", "edit"])("%s synchronously invalidates and aborts; late errors cannot restore", async (kind) => {
    const { result: view } = await ready(); const pending = deferred<ExplanationResponse>(); api.explain.mockReturnValueOnce(pending.promise);
    act(() => { void view.current.explanation.explain(); });
    act(() => {
      if (kind === "stays") view.current.schedule.setStayMinutes(required.id, "90");
      if (kind === "lunch") view.current.schedule.setLunchEnabled(false);
      if (kind === "mode") view.current.schedule.setTransportMode("transit");
      if (kind === "candidate") view.current.candidates.exclude(optionals[0].id);
      if (kind === "refresh") void view.current.candidates.query();
      if (kind === "query") void view.current.schedule.query();
      if (kind === "edit") view.current.schedule.invalidate();
      expect(api.explain.mock.calls[0][2].aborted).toBe(true);
    });
    await act(async () => pending.reject(new Error("stale")));
    expect(view.current.explanation.state.status).toBe("idle");
  });
  it("weather, annotations, preview success/cancel/failure/expiry keep explanation; adoption clears synchronously", async () => {
    const { result: view, rerender } = await ready();
    await act(async () => { await view.current.explanation.explain(); await view.current.weather.query(); });
    const official = view.current.schedule.state.response, explanation = view.current.explanation.state.response;
    rerender(); expect(view.current.explanation.state.response).toBe(explanation);
    act(() => view.current.check.annotate(required.id, "indoor")); act(() => { expect(view.current.check.check()).toBe(true); });
    await act(async () => { expect(await view.current.adjustment.start(optionals[0].id)).toBe(true); });
    expect(view.current.explanation.state.response).toBe(explanation);
    act(() => view.current.adjustment.cancel()); expect(view.current.schedule.state.response).toBe(official);
    api.schedule.mockRejectedValueOnce(new Error("controlled preview failure"));
    await act(async () => { await view.current.adjustment.start(optionals[0].id); });
    expect(view.current.explanation.state.response).toBe(explanation);
    act(() => view.current.adjustment.cancel());
    await act(async () => { await view.current.adjustment.start(optionals[0].id); await view.current.weather.query(); });
    expect(view.current.adjustment.state.status).toBe("expired"); expect(view.current.explanation.state.response).toBe(explanation);
    act(() => { view.current.adjustment.cancel(); view.current.check.check(); });
    await act(async () => { await view.current.adjustment.start(optionals[0].id); });
    act(() => { expect(view.current.adjustment.adopt()).toBe(true); });
    expect(view.current.explanation.state.status).toBe("idle"); expect(api.explain).toHaveBeenCalledTimes(1);
  });
  it("manual retry preserves official draft and unmount aborts without late writes", async () => {
    const { result: view, unmount } = await ready(); const official = view.current.schedule.state.response;
    api.explain.mockRejectedValueOnce(new Error("受控鉴权失败"));
    await act(async () => { await view.current.explanation.explain(); }); expect(view.current.explanation.state.status).toBe("failed");
    expect(view.current.schedule.state.response).toBe(official); expect(api.explain).toHaveBeenCalledTimes(1);
    await act(async () => { await view.current.explanation.explain(); }); expect(view.current.explanation.state.status).toBe("success");
    const pending = deferred<ExplanationResponse>(); api.explain.mockReturnValueOnce(pending.promise);
    act(() => { void view.current.explanation.explain(); }); unmount(); expect(api.explain.mock.calls[2][2].aborted).toBe(true);
    await act(async () => pending.resolve(result(official!)));
  });
});
