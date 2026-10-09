import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useSchedulePreview } from "./use-schedule-preview";
import { querySchedulePreview, ScheduleError } from "./schedule-api";
import { useCandidatePool, type CandidatePoolSnapshot } from "./use-candidate-pool";
import { queryCandidates } from "./candidates-api";
import type { CandidateResponse } from "@/types/candidates";
import { scheduleDates, type ScheduleRequest, type ScheduleResponse } from "@/types/schedule";
import type { TripRequest } from "@/types/trip";
import type { Place } from "@/types/place";

vi.mock("./schedule-api", () => ({ querySchedulePreview: vi.fn(), ScheduleError: class extends Error { constructor(public kind: string, message: string) { super(message); } } }));
vi.mock("./candidates-api", () => ({ queryCandidates: vi.fn(), CandidateError: class extends Error { constructor(public kind: string, message: string) { super(message); } } }));
const api = vi.mocked(querySchedulePreview);
const candidatesApi = vi.mocked(queryCandidates);
const place = (id: string): Place => ({ id, name: id, address: "测试地址", longitude: 121.4, latitude: 31.2, category: "原类别", source: "amap" });
function request(): TripRequest { return {
  start_date: "2026-10-10", end_date: "2026-10-11", budget: 3000, travelers: 2, accommodation_location: "hotel", accommodation_place: place("hotel"),
  must_visit: ["one", "two"], must_visit_places: [place("one"), place("two")], daily_start_time: "09:00:00", daily_end_time: "18:00:00", interests: ["摄影"], pace: "balanced", avoid_places: ["拥挤地点"],
}; }
function response(input: ScheduleRequest): ScheduleResponse { return {
  status: "unscheduled", request: { ...structuredClone(input), transport_mode: input.transport_mode ?? "walking" }, generated_at: "2026-10-06T04:00:00Z", rules: ["固定输入顺序"], unknowns: ["未核实营业"], edges: [],
  days: scheduleDates(input.start_date, input.end_date)!.map((date) => ({ date, items: [], return_time: null })),
  unscheduled: input.must_visit_places.map((p, index) => ({ place_id: p.id, reason: index ? "current_order_not_continued" : "time_window", message: "本次未排入" })),
  optional_results: (input.optional_places ?? []).map((p) => ({ place_id: p.id, scheduled_date: null, not_attempted_reason: input.must_visit_places.length ? "must_incomplete" : null,
    attempts: input.must_visit_places.length ? [] : scheduleDates(input.start_date, input.end_date)!.map((date) => ({ date, outcome: "time_window", message: "该日剩余时间不足。" })) })),
}; }
function candidateSnapshot(ids = ["o1", "o2", "o3", "o4"], excluded: string[] = []): CandidatePoolSnapshot {
  const data: CandidateResponse = { status: "success", queried_at: "2026-10-06T04:00:00Z", keywords: ["公园"],
    queries: [{ interest: "摄影", keyword: "公园", status: "success", result_count: ids.length, message: null }],
    candidates: ids.map((id) => ({ place: place(id), role: "optional", retrieval_sources: [{ interest: "摄影", keyword: "公园" }] })) };
  return { response: data, lastAttempt: data, excludedIds: new Set(excluded), loading: false, showingPrevious: false, message: null };
}
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (error: Error) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; }); return { promise, resolve, reject }; }
beforeEach(() => { api.mockReset(); candidatesApi.mockReset(); });

describe("explicit independent transport schedule lifecycle", () => {
  it("defaults to walking and switching mode preserves stay, lunch, candidate and exclusion inputs without querying", async () => {
    api.mockImplementation(async (body) => response(body)); const { result } = renderHook(() => useSchedulePreview(request()));
    expect(result.current.transportMode).toBe("walking");
    act(() => { result.current.updateCandidates(candidateSnapshot(["o1", "o2"], ["o2"])); result.current.setStayMinutes("o1", "90"); result.current.setLunchEnabled(false); });
    await act(async () => { await result.current.query(); });
    act(() => result.current.setTransportMode("transit"));
    expect(result.current.transportMode).toBe("transit"); expect(result.current.state.response).toBeNull(); expect(api).toHaveBeenCalledTimes(1);
    expect(result.current.stays.o1).toEqual({ value: "90", source: "user" }); expect(result.current.lunch.enabled).toBe(false);
    expect(result.current.optionalPlaces.map((p) => p.id)).toEqual(["o1"]);
    await act(async () => { await result.current.query(); });
    expect(api.mock.calls.map(([body]) => body.transport_mode)).toEqual(["walking", "transit"]);
    expect(api.mock.calls[1][0].duration_settings.find((s) => s.place_id === "o1")?.minutes).toBe(90);
    expect(api.mock.calls[1][0].lunch.enabled).toBe(false); expect(candidatesApi).not.toHaveBeenCalled();
  });
  it("a mode change followed by an older query closure in the same event reads the latest synchronous mode", async () => {
    api.mockImplementation(async (body) => response(body)); const { result } = renderHook(() => useSchedulePreview(request())); const oldQuery = result.current.query;
    await act(async () => { result.current.setTransportMode("transit"); await oldQuery(); });
    expect(api.mock.calls[0][0].transport_mode).toBe("transit");
    act(() => result.current.setTransportMode("walking")); expect(api).toHaveBeenCalledTimes(1);
    await act(async () => { await oldQuery(); }); expect(api.mock.calls[1][0].transport_mode).toBe("walking");
  });
  it.each(["success", "error"])("walking→transit→walking ignores old %s and finally keeps the new query pending guard", async (outcome) => {
    const old = deferred<ScheduleResponse>(); const next = deferred<ScheduleResponse>(); api.mockReturnValueOnce(old.promise).mockReturnValueOnce(next.promise);
    const { result } = renderHook(() => useSchedulePreview(request())); let oldWork!: Promise<boolean>; let freshWork!: Promise<boolean>;
    act(() => { oldWork = result.current.query(); }); const signal = api.mock.calls[0][1];
    act(() => { result.current.setTransportMode("transit"); result.current.setTransportMode("walking"); });
    expect(signal?.aborted).toBe(true); expect(api).toHaveBeenCalledTimes(1); expect(result.current.state.response).toBeNull();
    act(() => { freshWork = result.current.query(); });
    await act(async () => { if (outcome === "success") old.resolve(response(api.mock.calls[0][0])); else old.reject(new Error("stale mode failure")); expect(await oldWork).toBe(false); });
    expect(result.current.state.status).toBe("loading"); act(() => { void result.current.query(); }); expect(api).toHaveBeenCalledTimes(2);
    await act(async () => { next.resolve(response(api.mock.calls[1][0])); await freshWork; });
    expect(result.current.state.response?.request.transport_mode).toBe("walking"); expect(result.current.state.message).toBeNull();
  });
  it.each(["success", "error"])("transit pending %s is isolated after candidate refresh and input change without automatic fallback", async (outcome) => {
    const old = deferred<ScheduleResponse>(); api.mockReturnValueOnce(old.promise); const { result } = renderHook(() => useSchedulePreview(request())); let work!: Promise<boolean>;
    act(() => { result.current.setTransportMode("transit"); work = result.current.query(); });
    act(() => result.current.updateCandidates({ ...candidateSnapshot(), loading: true }));
    expect(api.mock.calls[0][1]?.aborted).toBe(true); expect(result.current.transportMode).toBe("transit");
    await act(async () => { if (outcome === "success") old.resolve(response(api.mock.calls[0][0])); else old.reject(new Error("stale transit")); await work; });
    expect(result.current.state.response).toBeNull(); expect(result.current.state.message).not.toContain("stale transit");
    expect(result.current.canQuery).toBe(false); expect(api).toHaveBeenCalledTimes(1); expect(api.mock.calls[0][0].transport_mode).toBe("transit");
  });
  it("selecting the current mode is a no-op, and a new result mount resets transit to walking", async () => {
    api.mockImplementation(async (body) => response(body)); const { result, unmount } = renderHook(() => useSchedulePreview(request()));
    act(() => result.current.setTransportMode("transit")); await act(async () => { await result.current.query(); }); const prior = result.current.state.response;
    act(() => result.current.setTransportMode("transit")); expect(result.current.state.response).toBe(prior); expect(api).toHaveBeenCalledTimes(1);
    unmount(); const next = renderHook(() => useSchedulePreview(request())); expect(next.result.current.transportMode).toBe("walking"); expect(next.result.current.state.response).toBeNull();
  });
  it("selects only first three eligible optional snapshots and excludes required/accommodation IDs", async () => {
    api.mockImplementation(async (body) => response(body)); const { result } = renderHook(() => useSchedulePreview(request()));
    act(() => result.current.updateCandidates(candidateSnapshot(["hotel", "one", "o1", "o2", "o3", "o4", "o1"], ["o2"])));
    expect(result.current.optionalPlaces.map((p) => p.id)).toEqual(["o1", "o3", "o4"]); expect(result.current.eligibleCount).toBe(3); expect(result.current.notSelectedCount).toBe(0);
    expect(result.current.stays.o1).toEqual({ value: "60", source: "default" });
    await act(async () => { await result.current.query(); });
    expect(api.mock.calls[0][0].optional_places?.map((p) => p.id)).toEqual(["o1", "o3", "o4"]);
    expect(api.mock.calls[0][0].duration_settings.map((entry) => entry.place_id)).toEqual(["one", "two", "o1", "o3", "o4"]);
    expect(Object.keys(api.mock.calls[0][0].optional_places![0])).not.toContain("retrieval_sources");
  });
  it("reports unselected count without automatic candidate or preview requests", () => {
    const { result } = renderHook(() => useSchedulePreview(request())); act(() => result.current.updateCandidates(candidateSnapshot()));
    expect(result.current.eligibleCount).toBe(4); expect(result.current.notSelectedCount).toBe(1); expect(result.current.candidateQueriedAt).toBe("2026-10-06T04:00:00Z");
    expect(api).not.toHaveBeenCalled(); expect(candidatesApi).not.toHaveBeenCalled();
  });
  it("allows optional-only generation but clears it when no active optional remains", async () => {
    api.mockImplementation(async (body) => response(body)); const { result } = renderHook(() => useSchedulePreview({ ...request(), must_visit: [], must_visit_places: [] }));
    expect(result.current.canQuery).toBe(false); act(() => result.current.updateCandidates(candidateSnapshot(["o1"]))); expect(result.current.canQuery).toBe(true);
    await act(async () => { await result.current.query(); }); expect(api.mock.calls[0][0].must_visit_places).toEqual([]);
    act(() => result.current.updateCandidates(candidateSnapshot(["o1"], ["o1"]))); expect(result.current.canQuery).toBe(false); expect(result.current.state.response).toBeNull();
  });
  it("retains user stay choices by ID across exclusion, replacement and reappearance, but submits only current union", async () => {
    api.mockImplementation(async (body) => response(body)); const { result } = renderHook(() => useSchedulePreview(request()));
    act(() => result.current.updateCandidates(candidateSnapshot(["o1"]))); act(() => result.current.setStayMinutes("o1", "90"));
    act(() => result.current.updateCandidates(candidateSnapshot(["o2"]))); act(() => result.current.updateCandidates(candidateSnapshot(["o1"])));
    expect(result.current.stays.o1).toEqual({ value: "90", source: "user" });
    await act(async () => { await result.current.query(); });
    expect(api.mock.calls[0][0].duration_settings).toContainEqual({ place_id: "o1", minutes: 90, source: "user" });
    expect(api.mock.calls[0][0].duration_settings.some((entry) => entry.place_id === "o2")).toBe(false);
  });
  it("candidate first failure still permits required-only preview; old valid/partial source remains explicit after refresh failure", () => {
    const { result } = renderHook(() => useSchedulePreview(request()));
    act(() => result.current.updateCandidates({ ...candidateSnapshot([]), response: null, lastAttempt: null, message: "未获取可选地点。" }));
    expect(result.current.canQuery).toBe(true); expect(result.current.optionalPlaces).toEqual([]);
    const old = candidateSnapshot(["o1"]); old.response!.status = "partial";
    act(() => result.current.updateCandidates({ ...old, showingPrevious: true, message: "更新失败，仍用上次。" }));
    expect(result.current.candidateStatus).toBe("partial"); expect(result.current.showingPreviousCandidates).toBe(true); expect(result.current.candidateMessage).toContain("上次");
    expect(result.current.optionalPlaces[0].id).toBe("o1");
  });
  it("synchronous candidate refresh blocks even an older query callback before React rerenders", async () => {
    const { result } = renderHook(() => useSchedulePreview(request())); const oldQuery = result.current.query;
    await act(async () => { result.current.updateCandidates({ ...candidateSnapshot(), loading: true }); expect(await oldQuery()).toBe(false); });
    expect(result.current.candidateLoading).toBe(true); expect(result.current.canQuery).toBe(false); expect(api).not.toHaveBeenCalled();
  });
  it("uses the newly filtered snapshot when exclude and an old query callback run in the same event", async () => {
    candidatesApi.mockResolvedValue(candidateSnapshot().response!); api.mockImplementation(async (body) => response(body));
    const input = request(); const { result } = renderHook(() => { const preview = useSchedulePreview(input); const pool = useCandidatePool(input, preview.updateCandidates); return { preview, pool }; });
    await act(async () => { await result.current.pool.query(); }); const oldQuery = result.current.preview.query;
    await act(async () => { result.current.pool.exclude("o1"); await oldQuery(); });
    expect(api.mock.calls[0][0].optional_places?.map((p) => p.id)).toEqual(["o2", "o3", "o4"]);
    expect(candidatesApi).toHaveBeenCalledTimes(1);
  });
  it("candidate refresh start synchronously aborts existing preview and successful replacement does not auto regenerate", async () => {
    const old = deferred<ScheduleResponse>(); const refresh = deferred<CandidateResponse>();
    candidatesApi.mockResolvedValueOnce(candidateSnapshot().response!).mockReturnValueOnce(refresh.promise); api.mockReturnValueOnce(old.promise);
    const input = request(); const { result } = renderHook(() => { const preview = useSchedulePreview(input); const pool = useCandidatePool(input, preview.updateCandidates); return { preview, pool }; });
    await act(async () => { await result.current.pool.query(); }); let oldWork!: Promise<boolean>; let updating!: Promise<boolean>;
    act(() => { oldWork = result.current.preview.query(); }); const signal = api.mock.calls[0][1];
    act(() => { updating = result.current.pool.query(); }); expect(signal?.aborted).toBe(true); expect(result.current.preview.candidateLoading).toBe(true);
    await act(async () => { old.resolve(response(api.mock.calls[0][0])); expect(await oldWork).toBe(false); refresh.resolve(candidateSnapshot(["new"]).response!); await updating; });
    expect(result.current.preview.state.response).toBeNull(); expect(result.current.preview.optionalPlaces[0].id).toBe("new"); expect(api).toHaveBeenCalledTimes(1);
  });
  it.each(["success", "error"])("exclude→restore A→B→A rejects old %s/finally without unblocking a newer pending preview", async (outcome) => {
    candidatesApi.mockResolvedValue(candidateSnapshot().response!); const old = deferred<ScheduleResponse>(); const next = deferred<ScheduleResponse>(); api.mockReturnValueOnce(old.promise).mockReturnValueOnce(next.promise);
    const input = request(); const { result } = renderHook(() => { const preview = useSchedulePreview(input); const pool = useCandidatePool(input, preview.updateCandidates); return { preview, pool }; });
    await act(async () => { await result.current.pool.query(); }); let oldWork!: Promise<boolean>; let newWork!: Promise<boolean>;
    act(() => { oldWork = result.current.preview.query(); }); act(() => result.current.pool.exclude("o1")); act(() => result.current.pool.restore("o1"));
    act(() => { newWork = result.current.preview.query(); });
    await act(async () => { if (outcome === "success") old.resolve(response(api.mock.calls[0][0])); else old.reject(new Error("old failure")); await oldWork; });
    expect(result.current.preview.state.status).toBe("loading"); act(() => { void result.current.preview.query(); }); expect(api).toHaveBeenCalledTimes(2);
    await act(async () => { next.resolve(response(api.mock.calls[1][0])); await newWork; }); expect(result.current.preview.state.status).toBe("success");
  });
  it("exclusion outside selected first three still invalidates a displayed preview", async () => {
    candidatesApi.mockResolvedValue(candidateSnapshot().response!); api.mockImplementation(async (body) => response(body));
    const input = request(); const { result } = renderHook(() => { const preview = useSchedulePreview(input); const pool = useCandidatePool(input, preview.updateCandidates); return { preview, pool }; });
    await act(async () => { await result.current.pool.query(); await result.current.preview.query(); });
    act(() => result.current.pool.exclude("o4")); expect(result.current.preview.optionalPlaces.map((p) => p.id)).toEqual(["o1", "o2", "o3"]);
    expect(result.current.preview.state.response).toBeNull(); expect(api).toHaveBeenCalledTimes(1); expect(candidatesApi).toHaveBeenCalledTimes(1);
  });
  it("captures optional snapshots independently of subsequent external object mutation", async () => {
    api.mockImplementation(async (body) => response(body)); const { result } = renderHook(() => useSchedulePreview(request())); const supplied = candidateSnapshot(["o1"]);
    act(() => result.current.updateCandidates(supplied)); supplied.response!.candidates[0].place.name = "later change";
    await act(async () => { await result.current.query(); }); expect(api.mock.calls[0][0].optional_places![0].name).toBe("o1");
  });
  it("defaults to assumed 60 minutes and 12–13 lunch without modifying any confirmed Place or sending requests", () => {
    const input = request(); const original = structuredClone(input); const { result, rerender } = renderHook(() => useSchedulePreview(input));
    expect(result.current.stays).toEqual({ one: { value: "60", source: "default" }, two: { value: "60", source: "default" } });
    expect(result.current.lunch).toEqual({ enabled: true, start: "12:00", end: "13:00" });
    expect(result.current.canQuery).toBe(true); expect(result.current.state.status).toBe("idle"); rerender(); expect(api).not.toHaveBeenCalled(); expect(input).toEqual(original);
  });
  it("explicit query sends only confirmed musts, dates, lodging and settings, normalizing zero seconds", async () => {
    api.mockImplementation(async (input) => response(input)); const input = request(); const { result } = renderHook(() => useSchedulePreview(input));
    await act(async () => { expect(await result.current.query()).toBe(true); });
    const body = api.mock.calls[0][0];
    expect(body).toEqual({ transport_mode: "walking", start_date: input.start_date, end_date: input.end_date, daily_start_time: "09:00", daily_end_time: "18:00", accommodation_place: input.accommodation_place,
      must_visit_places: input.must_visit_places, optional_places: [], duration_settings: [{ place_id: "one", minutes: 60, source: "default" }, { place_id: "two", minutes: 60, source: "default" }],
      lunch: { enabled: true, start_time: "12:00", end_time: "13:00" } });
    expect(result.current.state).toMatchObject({ status: "success", response: { status: "unscheduled" } });
  });
  it("marks even a manually entered 60 as user input and never changes Place", async () => {
    const input = request(); const original = structuredClone(input); api.mockImplementation(async (body) => response(body));
    const { result } = renderHook(() => useSchedulePreview(input)); act(() => result.current.setStayMinutes("one", "60"));
    expect(result.current.stays.one).toEqual({ value: "60", source: "user" });
    await act(async () => { await result.current.query(); }); expect(api.mock.calls[0][0].duration_settings[0].source).toBe("user"); expect(input).toEqual(original);
  });
  it("guards duplicate clicks synchronously with exactly one request", async () => {
    const wait = deferred<ScheduleResponse>(); api.mockReturnValueOnce(wait.promise); const { result } = renderHook(() => useSchedulePreview(request()));
    let work!: Promise<boolean>; act(() => { work = result.current.query(); void result.current.query(); });
    expect(api).toHaveBeenCalledTimes(1); expect(result.current.state.status).toBe("loading");
    await act(async () => { wait.resolve(response(api.mock.calls[0][0])); await work; }); expect(result.current.state.status).toBe("success");
  });
  it.each(["", "14", "481", "60.5", "1e2", "not a number"])("invalid stay %s clears result immediately and prevents query", async (value) => {
    api.mockImplementation(async (body) => response(body)); const { result } = renderHook(() => useSchedulePreview(request()));
    await act(async () => { await result.current.query(); }); act(() => result.current.setStayMinutes("one", value));
    expect(result.current.state.response).toBeNull(); expect(result.current.state.status).toBe("idle"); expect(result.current.canQuery).toBe(false);
    await act(async () => { expect(await result.current.query()).toBe(false); }); expect(api).toHaveBeenCalledTimes(1);
    expect(result.current.validationMessage).toContain("15～480");
  });
  it("disabling invalid lunch can restore valid generation while keeping the draft for re-enabling", async () => {
    api.mockImplementation(async (body) => response(body)); const { result } = renderHook(() => useSchedulePreview(request()));
    act(() => result.current.setLunchStart("")); expect(result.current.canQuery).toBe(false);
    act(() => result.current.setLunchEnabled(false)); expect(result.current.canQuery).toBe(true);
    await act(async () => { await result.current.query(); }); expect(api.mock.calls[0][0].lunch).toEqual({ enabled: false, start_time: "12:00", end_time: "13:00" });
    act(() => result.current.setLunchEnabled(true)); expect(result.current.canQuery).toBe(false); expect(result.current.lunch.start).toBe(""); expect(result.current.state.response).toBeNull();
  });
  it("blocks default lunch outside the submitted daily window until adjusted or disabled", () => {
    const input = { ...request(), daily_start_time: "14:00", daily_end_time: "17:00" }; const { result } = renderHook(() => useSchedulePreview(input));
    expect(result.current.canQuery).toBe(false); expect(result.current.validationMessage).toContain("调整或关闭");
    act(() => { result.current.setLunchStart("15:00"); result.current.setLunchEnd("16:00"); }); expect(result.current.canQuery).toBe(true);
  });
  it.each([
    ["no lodging", { accommodation_place: null }], ["no required", { must_visit_places: [] }],
    ["seven required", { must_visit_places: Array.from({ length: 7 }, (_, index) => place(`${index}`)) }],
    ["four days", { end_date: "2026-10-13" }], ["nonzero seconds", { daily_start_time: "09:00:30" }],
  ])("blocks %s without request", async (_, patch) => {
    const { result } = renderHook(() => useSchedulePreview({ ...request(), ...patch })); expect(result.current.canQuery).toBe(false);
    await act(async () => { expect(await result.current.query()).toBe(false); }); expect(api).not.toHaveBeenCalled();
  });
  it.each(["stay", "lunch_start", "lunch_end", "lunch_enabled"])("every %s edit synchronously aborts pending work and clears stale output", async (control) => {
    const wait = deferred<ScheduleResponse>(); api.mockReturnValueOnce(wait.promise); const { result } = renderHook(() => useSchedulePreview(request())); let work!: Promise<boolean>;
    act(() => { work = result.current.query(); }); const signal = api.mock.calls[0][1];
    act(() => {
      if (control === "stay") result.current.setStayMinutes("one", "90");
      if (control === "lunch_start") result.current.setLunchStart("12:15");
      if (control === "lunch_end") result.current.setLunchEnd("13:15");
      if (control === "lunch_enabled") result.current.setLunchEnabled(false);
    });
    expect(signal?.aborted).toBe(true); expect(result.current.state).toMatchObject({ status: "idle", response: null });
    await act(async () => { wait.resolve(response(api.mock.calls[0][0])); expect(await work).toBe(false); });
    expect(result.current.state.response).toBeNull(); expect(api).toHaveBeenCalledTimes(1);
  });
  it.each(["success", "error"])("stay A→B→A ignores old %s and finally cannot release a newer request guard", async (outcome) => {
    const old = deferred<ScheduleResponse>(); const next = deferred<ScheduleResponse>(); api.mockReturnValueOnce(old.promise).mockReturnValueOnce(next.promise);
    const { result } = renderHook(() => useSchedulePreview(request())); let oldWork!: Promise<boolean>; let freshWork!: Promise<boolean>;
    act(() => { oldWork = result.current.query(); }); act(() => result.current.setStayMinutes("one", "90")); act(() => result.current.setStayMinutes("one", "60"));
    act(() => { freshWork = result.current.query(); });
    await act(async () => { if (outcome === "success") old.resolve(response(api.mock.calls[0][0])); else old.reject(new Error("old failure")); await oldWork; });
    expect(result.current.state.status).toBe("loading"); act(() => { void result.current.query(); }); expect(api).toHaveBeenCalledTimes(2);
    await act(async () => { next.resolve(response(api.mock.calls[1][0])); await freshWork; });
    expect(result.current.state.response?.request.duration_settings[0].source).toBe("user");
  });
  it.each(["success", "error"])("lunch A→B→A isolates old %s even when transport ignores abort", async (outcome) => {
    const old = deferred<ScheduleResponse>(); api.mockReturnValueOnce(old.promise).mockImplementation(async (body) => response(body));
    const { result } = renderHook(() => useSchedulePreview(request())); let oldWork!: Promise<boolean>;
    act(() => { oldWork = result.current.query(); }); act(() => result.current.setLunchEnabled(false)); act(() => result.current.setLunchEnabled(true));
    await act(async () => { await result.current.query(); }); const current = result.current.state.response;
    await act(async () => { if (outcome === "success") old.resolve(response(api.mock.calls[0][0])); else old.reject(new Error("old failure")); await oldWork; });
    expect(result.current.state.response).toBe(current); expect(result.current.state.status).toBe("success");
  });
  it("uses latest draft even if settings edit and query run in the same event", async () => {
    api.mockImplementation(async (body) => response(body)); const { result } = renderHook(() => useSchedulePreview(request()));
    await act(async () => { result.current.setStayMinutes("one", "90"); await result.current.query(); });
    expect(api.mock.calls[0][0].duration_settings[0]).toEqual({ place_id: "one", minutes: 90, source: "user" });
  });
  it("other result re-renders retain preview/settings without additional requests", async () => {
    const input = request(); api.mockImplementation(async (body) => response(body));
    const { result, rerender } = renderHook(() => useSchedulePreview(input)); act(() => result.current.setStayMinutes("one", "90"));
    await act(async () => { await result.current.query(); }); const current = result.current.state.response;
    rerender(); expect(result.current.state.response).toBe(current); expect(result.current.stays.one.value).toBe("90"); expect(api).toHaveBeenCalledTimes(1);
  });
  it("failed requests show safe classified errors and allow explicit retry", async () => {
    api.mockRejectedValueOnce(new ScheduleError("timeout", "生成超时，请主动重试。")); const { result } = renderHook(() => useSchedulePreview(request()));
    await act(async () => { await result.current.query(); }); expect(result.current.state.message).toContain("超时"); expect(api).toHaveBeenCalledTimes(1);
    api.mockImplementation(async (body) => response(body)); await act(async () => { await result.current.query(); }); expect(result.current.state.status).toBe("success");
  });
  it("does not forward arbitrary thrown text", async () => {
    api.mockRejectedValueOnce(new Error("sensitive URL")); const { result } = renderHook(() => useSchedulePreview(request()));
    await act(async () => { await result.current.query(); }); expect(result.current.state.message).not.toContain("sensitive");
  });
  it.each(["success", "error"])("edit invalidation and unmount reject late %s; new mount resets assumptions", async (outcome) => {
    const old = deferred<ScheduleResponse>(); api.mockReturnValueOnce(old.promise); const { result, unmount } = renderHook(() => useSchedulePreview(request()));
    act(() => { result.current.setStayMinutes("one", "90"); result.current.setLunchEnabled(false); result.current.setTransportMode("transit"); }); let work!: Promise<boolean>;
    act(() => { work = result.current.query(); }); const signal = api.mock.calls[0][1]; act(() => result.current.invalidate()); expect(signal?.aborted).toBe(true); unmount();
    if (outcome === "success") old.resolve(response(api.mock.calls[0][0])); else old.reject(new Error("old error")); expect(await work).toBe(false);
    const fresh = renderHook(() => useSchedulePreview(request())); expect(fresh.result.current.state.status).toBe("idle");
    expect(fresh.result.current.stays.one).toEqual({ value: "60", source: "default" }); expect(fresh.result.current.lunch.enabled).toBe(true); expect(fresh.result.current.transportMode).toBe("walking");
  });
});
