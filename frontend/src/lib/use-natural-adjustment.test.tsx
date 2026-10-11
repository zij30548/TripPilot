import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useWeatherCheckRevision, useWeatherScheduleCheck } from "./use-weather-schedule-check";
import { useSchedulePreview } from "./use-schedule-preview";
import { useWeatherForecast } from "./use-weather-forecast";
import { useCandidatePool } from "./use-candidate-pool";
import { useScheduleAdjustment } from "./use-schedule-adjustment";
import { useScheduleExplanation } from "./use-schedule-explanation";
import { adjustmentResponse } from "./schedule-adjustment.test-fixtures";
import { candidateResponse, deferred, forecast, now, optionals, required, stamp, tripRequest } from "./weather-schedule-check.test-fixtures";
import type { ScheduleRequest, ScheduleResponse } from "@/types/schedule";
import type { IntentResponse } from "./adjustment-intent-api";
import type { ScheduleCapture } from "./schedule-adjustment";

const api = vi.hoisted(() => ({ schedule: vi.fn(), weather: vi.fn(), candidates: vi.fn(), intent: vi.fn(), explanation: vi.fn() }));
vi.mock("./schedule-api", async o => ({ ...await o<typeof import("./schedule-api")>(), querySchedulePreview: api.schedule }));
vi.mock("./weather-api", async o => ({ ...await o<typeof import("./weather-api")>(), queryWeatherForecast: api.weather }));
vi.mock("./candidates-api", async o => ({ ...await o<typeof import("./candidates-api")>(), queryCandidates: api.candidates }));
vi.mock("./adjustment-intent-api", async o => ({ ...await o<typeof import("./adjustment-intent-api")>(), queryAdjustmentIntent: api.intent }));
vi.mock("./explanation-api", async o => ({ ...await o<typeof import("./explanation-api")>(), queryExplanation: api.explanation }));
function resultFor(base: ScheduleCapture, id: string): IntentResponse {
  return { contract_version: "adjustment-intent-v1", request_id: id, draft_version: base.version, schedule_generated_at: base.original.generated_at, interpreted_at: stamp,
    model: "qwen3.7-flash-2026-07-15", usage: { input_tokens: null, output_tokens: null }, selection: { decision: "execute", action: "exclude_optional", target_id: optionals[0].id, reason_code: "single_optional" } };
}
function useHarness(noRequired = false) {
  const request = noRequired ? { ...tripRequest(), must_visit: [], must_visit_places: [] } : tripRequest();
  const revision = useWeatherCheckRevision(), schedule = useSchedulePreview(request, revision.invalidate);
  const candidates = useCandidatePool(request, schedule.updateCandidates), weather = useWeatherForecast({ start_date: request.start_date, end_date: request.end_date }, revision.invalidate);
  const check = useWeatherScheduleCheck({ schedule, candidates, weather }, revision), preview = useScheduleAdjustment({ schedule, candidates, weather, check }, revision);
  return { schedule, candidates, weather, check, preview, ai: useScheduleExplanation(schedule) };
}
async function ready(noRequired = false) {
  const view = renderHook(() => useHarness(noRequired));
  await act(async () => { await view.result.current.candidates.query(); });
  await act(async () => { await view.result.current.schedule.query(); await view.result.current.ai.explain(); });
  act(() => view.result.current.preview.setInstruction(`排除${optionals[0].id}`));
  return view;
}
beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] }); vi.setSystemTime(now); vi.resetAllMocks();
  api.schedule.mockImplementation(async (r: ScheduleRequest) => adjustmentResponse(r));
  api.intent.mockImplementation(async (b: ScheduleCapture, _text: string, id: string) => resultFor(b, id));
  api.candidates.mockImplementation(async () => candidateResponse()); api.weather.mockImplementation(async () => forecast());
  api.explanation.mockImplementation(async (r: ScheduleResponse, id: string) => ({ contract_version: "schedule-explanation-v1", request_id: id, schedule_generated_at: r.generated_at,
    explained_at: stamp, model: "qwen3.7-flash-2026-07-15", highlights: [{ message_code: "arrangement", fact_ids: ["f0"], text: "受控解读" }], limitations: ["限制1", "限制2", "限制3"], usage: { input_tokens: null, output_tokens: null } }));
});
afterEach(() => vi.useRealTimers());

describe("natural intent → same isolated preview/adoption transaction", () => {
  it.each(["walking", "transit"] as const)("%s keeps official input until adopt; cancel preserves explanation; adopt once with no request", async mode => {
    const { result } = await ready();
    if (mode === "transit") { act(() => result.current.schedule.setTransportMode(mode)); await act(async () => { await result.current.schedule.query(); await result.current.ai.explain(); }); }
    const original = result.current.schedule.state.response, explanation = result.current.ai.state.response;
    await act(async () => { expect(await result.current.preview.startNatural()).toBe(true); });
    expect(result.current.schedule.state.response).toBe(original); expect(result.current.candidates.excludedIds.size).toBe(0);
    expect(result.current.ai.state.response).toBe(explanation); expect(result.current.preview.state.response!.request.transport_mode).toBe(mode);
    act(() => { result.current.preview.cancel(); });
    expect(result.current.ai.state.response).toBe(explanation); expect(result.current.schedule.state.response).toBe(original);
    await act(async () => { await result.current.preview.startNatural(); });
    const shown = result.current.preview.state.response, adopt = result.current.preview.adopt, counts = Object.values(api).map(f => f.mock.calls.length);
    act(() => { expect(adopt()).toBe(true); expect(adopt()).toBe(false); });
    expect(Object.values(api).map(f => f.mock.calls.length)).toEqual(counts);
    expect(result.current.schedule.state.response).toEqual(shown); expect(result.current.ai.state.response).toBeNull();
    expect(result.current.candidates.excludedIds.has(optionals[0].id)).toBe(true);
    expect(result.current.schedule.optionalPlaces.map(p => p.id)).toEqual(optionals.slice(1).map(p => p.id));
    expect(result.current.schedule.stays[optionals[3].id]).toEqual({ value: "60", source: "default" });
    await act(async () => { await result.current.schedule.query(); });
    expect(api.schedule.mock.lastCall![0]).toEqual(shown!.request);
    expect(api.weather).not.toHaveBeenCalled();
  });
  it.each(["clarify", "unsupported"] as const)("%s never queries routes or changes official state", async decision => {
    const { result } = await ready(), original = result.current.schedule.state.response;
    api.intent.mockImplementationOnce(async (base, _text, id) => ({ ...resultFor(base,id), selection: { decision, action: null, target_id: null, reason_code: decision === "clarify" ? "unclear_request" : "multiple_changes" } }));
    await act(async () => { await result.current.preview.startNatural(); });
    expect(result.current.preview.state.status).toBe(decision); expect(api.schedule).toHaveBeenCalledTimes(1); expect(result.current.schedule.state.response).toBe(original);
  });
  it.each([required.id, optionals[2].id, "forged"])("invalid target %s never enters route query", async target => {
    const { result } = await ready(); api.intent.mockImplementationOnce(async (base, _text, id) => { const r = resultFor(base,id); r.selection.target_id = target; return r; });
    await act(async () => { await result.current.preview.startNatural(); });
    expect(result.current.preview.state.status).toBe("failed"); expect(api.schedule).toHaveBeenCalledTimes(1);
  });
  it.each(["resolve", "reject"] as const)("cancel/edit ABA isolates old %s and finally from newer pending request", async finish => {
    const { result } = await ready(), first = deferred<IntentResponse>(), second = deferred<IntentResponse>();
    api.intent.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    let a!: Promise<boolean>, b!: Promise<boolean>;
    act(() => { a = result.current.preview.startNatural(); }); const old = result.current.preview.cancel;
    act(() => { result.current.preview.setInstruction("另一个要求"); result.current.preview.setInstruction(`排除${optionals[0].id}`); });
    act(() => { b = result.current.preview.startNatural(); });
    expect(api.intent.mock.calls[0][3].aborted).toBe(true); act(() => { expect(old()).toBe(false); });
    await act(async () => { if (finish === "resolve") first.resolve(resultFor(api.intent.mock.calls[0][0], api.intent.mock.calls[0][2])); else first.reject(new Error("old failure")); await a; });
    expect(result.current.preview.state.status).toBe("parsing");
    await act(async () => { await result.current.preview.startNatural(); }); expect(api.intent).toHaveBeenCalledTimes(2);
    await act(async () => { second.resolve(resultFor(api.intent.mock.calls[1][0],api.intent.mock.calls[1][2])); await b; });
    expect(result.current.preview.canAdopt).toBe(true); expect(api.schedule).toHaveBeenCalledTimes(2);
  });
  it("newer response finishes first, old success cannot replace it", async () => {
    const { result } = await ready(), pending = deferred<IntentResponse>(); api.intent.mockReturnValueOnce(pending.promise);
    let first!: Promise<boolean>; act(() => { first = result.current.preview.startNatural(); });
    act(() => { result.current.preview.cancel(); });
    await act(async () => { await result.current.preview.startNatural(); }); const shown = result.current.preview.state.response;
    await act(async () => { pending.resolve(resultFor(api.intent.mock.calls[0][0], api.intent.mock.calls[0][2])); await first; });
    expect(result.current.preview.state.response).toBe(shown); expect(api.schedule).toHaveBeenCalledTimes(2);
  });
  it.each(["settings", "candidates", "modeABA", "generate"])("%s synchronously expires preview and rejects old adopt", async change => {
    const { result } = await ready(); await act(async () => { await result.current.preview.startNatural(); }); const old = result.current.preview.adopt;
    await act(async () => {
      if (change === "settings") result.current.schedule.setStayMinutes(required.id,"61");
      if (change === "candidates") { result.current.candidates.exclude(optionals[1].id); result.current.candidates.restore(optionals[1].id); }
      if (change === "modeABA") { result.current.schedule.setTransportMode("transit"); result.current.schedule.setTransportMode("walking"); }
      if (change === "generate") await result.current.schedule.query();
      expect(old()).toBe(false);
    });
    expect(result.current.preview.state.status).toBe("expired"); expect(result.current.candidates.excludedIds.has(optionals[0].id)).toBe(false);
  });
  it("weather refresh/labels do not invalidate explicit natural preview; other weather preview replaces it", async () => {
    const { result } = await ready(); await act(async () => { await result.current.preview.startNatural(); }); const oldAdopt = result.current.preview.adopt;
    await act(async () => { await result.current.weather.query(); });
    act(() => { result.current.check.annotate(required.id,"indoor"); });
    act(() => { expect(result.current.check.check()).toBe(true); });
    expect(result.current.preview.canAdopt).toBe(true); expect(result.current.ai.state.response).not.toBeNull();
    await act(async () => { await result.current.preview.start(optionals[0].id); });
    expect(result.current.preview.state.source).toBe("weather"); act(() => { expect(oldAdopt()).toBe(false); });
    await act(async () => { await result.current.weather.query(); }); expect(result.current.preview.state.status).toBe("expired");
  });
  it("route failure/cancellation retain original draft, and late routes cannot revive preview", async () => {
    const { result } = await ready(), original=result.current.schedule.state.response;
    api.schedule.mockRejectedValueOnce(new Error("controlled timeout")); await act(async () => { await result.current.preview.startNatural(); });
    expect(result.current.preview.state.status).toBe("failed"); expect(result.current.schedule.state.response).toBe(original);
    const route = deferred<ScheduleResponse>(); api.schedule.mockReturnValueOnce(route.promise);
    let pending!: Promise<boolean>; await act(async () => { pending=result.current.preview.startNatural(); });
    act(() => { result.current.preview.cancel(); });
    await act(async () => { route.resolve(adjustmentResponse(api.schedule.mock.lastCall![0])); await pending; });
    expect(result.current.preview.state.status).toBe("idle"); expect(result.current.ai.state.response).not.toBeNull();
  });
  it("unmount cancels parsing; empty-after-exclusion never sends invalid schedule", async () => {
    const view = await ready(), pending = deferred<IntentResponse>(); api.intent.mockReturnValueOnce(pending.promise);
    let running!: Promise<boolean>; act(() => { running=view.result.current.preview.startNatural(); }); view.unmount();
    expect(api.intent.mock.lastCall![3].aborted).toBe(true);
    await act(async () => { pending.reject(new Error("late")); await running; });
    const next=await ready(true); act(() => { for (const p of optionals.slice(1)) next.result.current.candidates.exclude(p.id); });
    await act(async () => { await next.result.current.schedule.query(); }); const count=api.schedule.mock.calls.length;
    await act(async () => { await next.result.current.preview.startNatural(); });
    expect(next.result.current.preview.state.message).toContain("没有必去或可选"); expect(api.schedule).toHaveBeenCalledTimes(count);
  });
});
