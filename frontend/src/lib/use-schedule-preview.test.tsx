import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useSchedulePreview } from "./use-schedule-preview";
import { querySchedulePreview, ScheduleError } from "./schedule-api";
import { scheduleDates, type ScheduleRequest, type ScheduleResponse } from "@/types/schedule";
import type { TripRequest } from "@/types/trip";
import type { Place } from "@/types/place";

vi.mock("./schedule-api", () => ({ querySchedulePreview: vi.fn(), ScheduleError: class extends Error { constructor(public kind: string, message: string) { super(message); } } }));
const api = vi.mocked(querySchedulePreview);
const place = (id: string): Place => ({ id, name: id, address: "测试地址", longitude: 121.4, latitude: 31.2, category: "原类别", source: "amap" });
function request(): TripRequest { return {
  start_date: "2026-10-10", end_date: "2026-10-11", budget: 3000, travelers: 2, accommodation_location: "hotel", accommodation_place: place("hotel"),
  must_visit: ["one", "two"], must_visit_places: [place("one"), place("two")], daily_start_time: "09:00:00", daily_end_time: "18:00:00", interests: ["摄影"], pace: "balanced", avoid_places: ["拥挤地点"],
}; }
function response(input: ScheduleRequest): ScheduleResponse { return {
  status: "unscheduled", request: structuredClone(input), generated_at: "2026-10-06T04:00:00Z", rules: ["固定输入顺序"], unknowns: ["未核实营业"], edges: [],
  days: scheduleDates(input.start_date, input.end_date)!.map((date) => ({ date, items: [], return_time: null })),
  unscheduled: input.must_visit_places.map((p, index) => ({ place_id: p.id, reason: index ? "current_order_not_continued" : "time_window", message: "本次未排入" })),
}; }
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (error: Error) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; }); return { promise, resolve, reject }; }
beforeEach(() => { api.mockReset(); });

describe("explicit independent walking schedule lifecycle", () => {
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
    expect(body).toEqual({ start_date: input.start_date, end_date: input.end_date, daily_start_time: "09:00", daily_end_time: "18:00", accommodation_place: input.accommodation_place,
      must_visit_places: input.must_visit_places, duration_settings: [{ place_id: "one", minutes: 60, source: "default" }, { place_id: "two", minutes: 60, source: "default" }],
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
    act(() => { result.current.setStayMinutes("one", "90"); result.current.setLunchEnabled(false); }); let work!: Promise<boolean>;
    act(() => { work = result.current.query(); }); const signal = api.mock.calls[0][1]; act(() => result.current.invalidate()); expect(signal?.aborted).toBe(true); unmount();
    if (outcome === "success") old.resolve(response(api.mock.calls[0][0])); else old.reject(new Error("old error")); expect(await work).toBe(false);
    const fresh = renderHook(() => useSchedulePreview(request())); expect(fresh.result.current.state.status).toBe("idle");
    expect(fresh.result.current.stays.one).toEqual({ value: "60", source: "default" }); expect(fresh.result.current.lunch.enabled).toBe(true);
  });
});
