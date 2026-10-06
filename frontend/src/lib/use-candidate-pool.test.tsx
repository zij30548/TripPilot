import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useCandidatePool } from "./use-candidate-pool";
import { CandidateError, queryCandidates } from "./candidates-api";
import { candidateSearches, type CandidateResponse } from "@/types/candidates";
import type { Place } from "@/types/place";
import type { TripRequest } from "@/types/trip";

vi.mock("./candidates-api", () => ({ queryCandidates: vi.fn(), CandidateError: class extends Error { constructor(public kind: string, message: string) { super(message); } } }));
const api = vi.mocked(queryCandidates);
const place = (id: string): Place => ({ id, name: id, address: "测试地址", latitude: 31.2, longitude: 121.4, category: "原始类别", source: "amap" });
const request = (): TripRequest => ({ start_date: "2026-10-10", end_date: "2026-10-11", budget: 3000, travelers: 2,
  accommodation_location: "hotel", accommodation_place: place("hotel"), must_visit: ["must"], must_visit_places: [place("must")],
  interests: ["摄影", "美食"], pace: "balanced", avoid_places: ["拥挤的地方"], daily_start_time: "09:00", daily_end_time: "21:00" });
function response(ids = ["one", "two"], status: CandidateResponse["status"] = "success"): CandidateResponse {
  const sources = candidateSearches(["摄影", "美食"]);
  return { status, queried_at: "2026-10-06T01:02:03Z", keywords: sources.map((source) => source.keyword),
    queries: sources.map((source, index) => ({ ...source, status: status === "failed" || (status === "partial" && index === 1) ? "failed" : "success",
      result_count: status === "failed" || (status === "partial" && index === 1) ? 0 : ids.length,
      message: status === "failed" || (status === "partial" && index === 1) ? "搜索未完成" : null })),
    candidates: [{ place: place("must"), role: "must_visit", retrieval_sources: [] },
      ...ids.map((id) => ({ place: place(id), role: "optional" as const, retrieval_sources: [sources[0]] }))] };
}
function deferred<T>() {
  let resolve!: (value: T) => void; let reject!: (error: Error) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
beforeEach(() => api.mockReset());

describe("whole-trip candidate pool lifecycle", () => {
  it("does not auto query; retains submitted must-visits from the start", () => {
    const input = request(); const { result, rerender } = renderHook(() => useCandidatePool(input));
    expect(result.current.state.status).toBe("idle"); expect(result.current.candidates.map((item) => item.place.id)).toEqual(["must"]);
    rerender(); expect(api).not.toHaveBeenCalled(); expect(result.current.getActivePlaces()).toEqual(input.must_visit_places);
  });
  it("rejects legacy missing accommodation or unsupported legacy interests without a request", async () => {
    for (const input of [{ ...request(), accommodation_place: null }, { ...request(), interests: ["未知"] }]) {
      const { result, unmount } = renderHook(() => useCandidatePool(input));
      expect(result.current.canQuery).toBe(false);
      await act(async () => { expect(await result.current.query()).toBe(false); });
      expect(result.current.state.message).toContain("确认住宿参考点"); unmount();
    }
    expect(api).not.toHaveBeenCalled();
  });
  it("only explicit query calls; synchronous duplicate clicks share one pending request", async () => {
    const pending = deferred<CandidateResponse>(); api.mockReturnValueOnce(pending.promise);
    const { result } = renderHook(() => useCandidatePool(request()));
    let first!: Promise<boolean>;
    act(() => { first = result.current.query(); void result.current.query(); });
    expect(api).toHaveBeenCalledTimes(1); expect(result.current.state.status).toBe("loading");
    await act(async () => { pending.resolve(response()); await first; });
    expect(result.current.state.status).toBe("success");
  });
  it("exclusion/restoration is local and cannot exclude required places", async () => {
    const input = request(); const original = structuredClone(input); api.mockResolvedValue(response());
    const { result } = renderHook(() => useCandidatePool(input)); await act(async () => { await result.current.query(); });
    act(() => { result.current.exclude("must"); result.current.exclude("one"); });
    expect([...result.current.excludedIds]).toEqual(["one"]); expect(result.current.activePlaces.map((item) => item.id)).toEqual(["must", "two"]);
    act(() => result.current.restore("one")); expect(result.current.activePlaces).toHaveLength(3);
    expect(api).toHaveBeenCalledTimes(1); expect(input).toEqual(original);
  });
  it("initial all-failed response preserves must requirements and is not a success", async () => {
    const data = response([], "failed"); api.mockResolvedValue(data);
    const { result } = renderHook(() => useCandidatePool(request()));
    await act(async () => { expect(await result.current.query()).toBe(false); });
    expect(result.current.state).toMatchObject({ status: "failed", response: null, lastAttempt: data, showingPrevious: false });
    expect(result.current.candidates.map((entry) => entry.place.id)).toEqual(["must"]);
  });
  it("initial network failure has safe prompt and must remains", async () => {
    api.mockRejectedValueOnce(new Error("sensitive URL")); const { result } = renderHook(() => useCandidatePool(request()));
    await act(async () => { await result.current.query(); });
    expect(result.current.state.message).not.toContain("sensitive"); expect(result.current.candidates).toHaveLength(1);
  });
  it.each(["failed", "throw"])("refresh %s retains last valid list and exclusions", async (kind) => {
    const previous = response(); const pending = deferred<CandidateResponse>();
    api.mockResolvedValueOnce(previous).mockReturnValueOnce(pending.promise);
    const { result } = renderHook(() => useCandidatePool(request())); await act(async () => { await result.current.query(); });
    act(() => result.current.exclude("one")); let updating!: Promise<boolean>;
    act(() => { updating = result.current.query(); });
    expect(result.current.state).toMatchObject({ status: "loading", showingPrevious: true, response: previous });
    await act(async () => { if (kind === "failed") pending.resolve(response([], "failed")); else pending.reject(new CandidateError("timeout", "获取超时")); await updating; });
    expect(result.current.state).toMatchObject({ status: "failed", response: previous, showingPrevious: true });
    expect(result.current.state.message).toContain("上次"); expect([...result.current.excludedIds]).toEqual(["one"]);
    expect(result.current.candidates.map((entry) => entry.place.id)).toEqual(["must", "one", "two"]);
  });
  it("partial replaces rather than merges old optionals; omitted IDs keep their exclusion decisions", async () => {
    api.mockResolvedValueOnce(response()).mockResolvedValueOnce(response(["three"], "partial")).mockResolvedValueOnce(response(["one"]));
    const { result } = renderHook(() => useCandidatePool(request())); await act(async () => { await result.current.query(); });
    act(() => result.current.exclude("one")); await act(async () => { await result.current.query(); });
    expect(result.current.state.status).toBe("partial"); expect(result.current.candidates.map((entry) => entry.place.id)).toEqual(["must", "three"]);
    expect(result.current.excludedIds.has("one")).toBe(true);
    await act(async () => { await result.current.query(); });
    expect(result.current.candidates.map((entry) => entry.place.id)).toEqual(["must", "one"]);
    expect(result.current.activePlaces.map((item) => item.id)).toEqual(["must"]);
  });
  it("successful empty optional result replaces old results without calling it a failure", async () => {
    api.mockResolvedValueOnce(response()).mockResolvedValueOnce(response([]));
    const { result } = renderHook(() => useCandidatePool(request()));
    await act(async () => { await result.current.query(); }); await act(async () => { await result.current.query(); });
    expect(result.current.state.status).toBe("success"); expect(result.current.candidates.map((entry) => entry.place.id)).toEqual(["must"]);
  });
  it.each(["success", "error"])("invalidate protects new query from late old %s, including repeated same request", async (kind) => {
    const old = deferred<CandidateResponse>(); const fresh = deferred<CandidateResponse>();
    api.mockReturnValueOnce(old.promise).mockReturnValueOnce(fresh.promise);
    const { result } = renderHook(() => useCandidatePool(request())); let oldRequest!: Promise<boolean>; let newRequest!: Promise<boolean>;
    act(() => { oldRequest = result.current.query(); }); const oldSignal = api.mock.calls[0][1];
    act(() => result.current.invalidate()); expect(oldSignal?.aborted).toBe(true);
    act(() => { newRequest = result.current.query(); });
    await act(async () => { if (kind === "success") old.resolve(response(["old"])); else old.reject(new Error("old failure")); await oldRequest; });
    expect(result.current.state.status).toBe("loading"); expect(result.current.candidates).toHaveLength(1);
    await act(async () => { fresh.resolve(response(["fresh"])); await newRequest; });
    expect(result.current.candidates.map((entry) => entry.place.id)).toEqual(["must", "fresh"]);
  });
  it("editing synchronously clears valid results and all remembered exclusions", async () => {
    api.mockResolvedValue(response()); const { result } = renderHook(() => useCandidatePool(request()));
    await act(async () => { await result.current.query(); }); act(() => result.current.exclude("one")); act(() => result.current.invalidate());
    expect(result.current.state).toMatchObject({ status: "idle", response: null, lastAttempt: null });
    expect(result.current.excludedIds.size).toBe(0); expect(result.current.candidates).toHaveLength(1);
  });
  it.each(["success", "error"])("unmount aborts and ignores late %s; remount has no pool or exclusions", async (kind) => {
    const pending = deferred<CandidateResponse>(); api.mockReturnValueOnce(pending.promise);
    const { result, unmount } = renderHook(() => useCandidatePool(request())); let work!: Promise<boolean>;
    act(() => { work = result.current.query(); }); const signal = api.mock.calls[0][1]; unmount(); expect(signal?.aborted).toBe(true);
    if (kind === "success") pending.resolve(response()); else pending.reject(new Error("old error"));
    expect(await work).toBe(false);
    const current = renderHook(() => useCandidatePool(request())); expect(current.result.current.state.status).toBe("idle");
    expect(current.result.current.excludedIds.size).toBe(0); expect(current.result.current.candidates).toHaveLength(1);
  });
});
