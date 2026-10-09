import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useWeatherForecast } from "./use-weather-forecast";
import { queryWeatherForecast, WeatherError } from "./weather-api";
import { getWeatherDates, type WeatherForecastRequest, type WeatherForecastResponse } from "@/types/weather";

vi.mock("./weather-api", () => ({ queryWeatherForecast: vi.fn(), WeatherError: class extends Error { constructor(public kind: string, message: string) { super(message); } } }));
const api = vi.mocked(queryWeatherForecast);
const request = (): WeatherForecastRequest => ({ start_date: "2026-10-10", end_date: "2026-10-12" });
function response(input = request(), covered = true): WeatherForecastResponse {
  const period = { weather: "晴", temperature_celsius: 0, wind_direction: "东", wind_power: "1-3", precipitation: "none" as const, precipitation_basis: "晴" };
  return { request: { ...input }, source: "amap", city: "上海市", adcode: "310000", timezone: "Asia/Shanghai", queried_at: "2026-10-10T00:00:00Z", reported_at: "2026-10-10T07:00:00+08:00", report_time_status: "valid", freshness_at_query: "fresh", coverage: covered ? "complete" : "none", guidance_rule: "amap_text_precipitation_v1",
    days: getWeatherDates(input).map((date) => ({ date, status: covered ? "available" : "unavailable", day: covered ? { ...period } : null, night: covered ? { ...period } : null })) };
}
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (error: Error) => void; const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; }); return { promise, resolve, reject }; }
beforeEach(() => api.mockReset());

describe("explicit weather lifecycle independent of trip modules", () => {
  it("mount and equal-input rerenders never query; explicit query guards duplicate clicks", async () => {
    const pending = deferred<WeatherForecastResponse>(); api.mockReturnValueOnce(pending.promise);
    const { result, rerender } = renderHook(({ input }) => useWeatherForecast(input), { initialProps: { input: request() } });
    expect(result.current.state.status).toBe("idle"); rerender({ input: request() }); expect(api).not.toHaveBeenCalled();
    let work!: Promise<boolean>; act(() => { work = result.current.query(); void result.current.query(); }); expect(api).toHaveBeenCalledTimes(1); expect(result.current.state.status).toBe("loading");
    await act(async () => { pending.resolve(response()); expect(await work).toBe(true); }); expect(result.current.state.response?.days).toHaveLength(3);
    rerender({ input: request() }); expect(result.current.state.status).toBe("success"); expect(api).toHaveBeenCalledTimes(1);
  });
  it("first failure is distinct from no coverage, keeps no fabricated result and allows explicit retry", async () => {
    api.mockRejectedValueOnce(new WeatherError("timeout", "查询超时。")); const { result } = renderHook(() => useWeatherForecast(request()));
    await act(async () => { expect(await result.current.query()).toBe(false); }); expect(result.current.state).toMatchObject({ status: "failed", response: null, showingPrevious: false, message: "查询超时。" });
    api.mockResolvedValueOnce(response(request(), false)); await act(async () => { expect(await result.current.query()).toBe(true); });
    expect(result.current.state.status).toBe("success"); expect(result.current.state.response?.coverage).toBe("none"); expect(api).toHaveBeenCalledTimes(2);
  });
  it("refresh preserves previous entire snapshot and timestamps on loading/failure without laundering freshness", async () => {
    const old = response(); old.reported_at = "2026-10-07T07:00:00+08:00"; old.freshness_at_query = "stale"; api.mockResolvedValueOnce(old);
    const { result } = renderHook(() => useWeatherForecast(request())); await act(async () => { await result.current.query(); });
    const refresh = deferred<WeatherForecastResponse>(); api.mockReturnValueOnce(refresh.promise); let work!: Promise<boolean>; act(() => { work = result.current.query(); });
    expect(result.current.state).toMatchObject({ status: "loading", response: old, showingPrevious: true });
    await act(async () => { refresh.reject(new Error("sensitive upstream message")); expect(await work).toBe(false); });
    expect(result.current.state).toMatchObject({ status: "failed", response: old, showingPrevious: true }); expect(result.current.state.message).not.toContain("sensitive"); expect(result.current.state.message).toContain("上次");
    expect(result.current.state.response?.queried_at).toBe(old.queried_at); expect(result.current.state.response?.reported_at).toBe(old.reported_at);
  });
  it("successful partial and empty batches replace instead of filling gaps from older batches", async () => {
    api.mockResolvedValueOnce(response()); const { result } = renderHook(() => useWeatherForecast(request())); await act(async () => { await result.current.query(); });
    const partial = response(); partial.coverage = "partial"; partial.days[0] = { ...partial.days[0], status: "unavailable", day: null, night: null }; api.mockResolvedValueOnce(partial);
    await act(async () => { await result.current.query(); }); expect(result.current.state.response).toBe(partial); expect(result.current.state.response?.days[0].day).toBeNull();
    const empty = response(request(), false); api.mockResolvedValueOnce(empty); await act(async () => { await result.current.query(); });
    expect(result.current.state.response).toBe(empty); expect(result.current.state.showingPrevious).toBe(false); expect(result.current.state.message).toBeNull();
  });
  it("changed dates synchronously abort/reset and an old query closure uses the latest dates", async () => {
    const old = deferred<WeatherForecastResponse>(); api.mockReturnValueOnce(old.promise); const { result, rerender } = renderHook(({ input }) => useWeatherForecast(input), { initialProps: { input: request() } });
    const oldQuery = result.current.query; let work!: Promise<boolean>; act(() => { work = oldQuery(); }); const signal = api.mock.calls[0][1];
    const next = { start_date: "2027-01-01", end_date: "2027-01-03" }; rerender({ input: next }); expect(signal?.aborted).toBe(true); expect(result.current.state.status).toBe("idle");
    api.mockResolvedValueOnce(response(next)); await act(async () => { await oldQuery(); }); expect(api.mock.calls[1][0]).toEqual(next);
    await act(async () => { old.resolve(response()); expect(await work).toBe(false); }); expect(result.current.state.response?.request).toEqual(next);
  });
  it.each(["success", "error"])("A→B→A isolates late %s and finally cannot unlock a newer pending query", async (outcome) => {
    const old = deferred<WeatherForecastResponse>(); const next = deferred<WeatherForecastResponse>(); api.mockReturnValueOnce(old.promise).mockReturnValueOnce(next.promise);
    const { result, rerender } = renderHook(({ input }) => useWeatherForecast(input), { initialProps: { input: request() } }); let oldWork!: Promise<boolean>; let nextWork!: Promise<boolean>;
    act(() => { oldWork = result.current.query(); }); const oldSignal = api.mock.calls[0][1];
    rerender({ input: { start_date: "2027-01-01", end_date: "2027-01-01" } }); rerender({ input: request() }); expect(oldSignal?.aborted).toBe(true);
    act(() => { nextWork = result.current.query(); });
    await act(async () => { if (outcome === "success") old.resolve(response()); else old.reject(new Error("stale error")); expect(await oldWork).toBe(false); });
    expect(result.current.state.status).toBe("loading"); expect(result.current.state.message).toBeNull(); act(() => { void result.current.query(); }); expect(api).toHaveBeenCalledTimes(2);
    await act(async () => { next.resolve(response()); expect(await nextWork).toBe(true); }); expect(result.current.state.status).toBe("success");
  });
  it.each(["success", "error"])("edit invalidation synchronously clears retained snapshot and rejects old %s/finally", async (outcome) => {
    api.mockResolvedValueOnce(response()); const { result } = renderHook(() => useWeatherForecast(request())); await act(async () => { await result.current.query(); });
    const old = deferred<WeatherForecastResponse>(); const next = deferred<WeatherForecastResponse>(); api.mockReturnValueOnce(old.promise).mockReturnValueOnce(next.promise);
    let oldWork!: Promise<boolean>; let nextWork!: Promise<boolean>; act(() => { oldWork = result.current.query(); result.current.invalidate(); nextWork = result.current.query(); });
    expect(api.mock.calls[1][1]?.aborted).toBe(true); expect(result.current.state.response).toBeNull(); expect(result.current.state.showingPrevious).toBe(false);
    await act(async () => { if (outcome === "success") old.resolve(response()); else old.reject(new Error("old error")); await oldWork; });
    act(() => { void result.current.query(); }); expect(api).toHaveBeenCalledTimes(3); expect(result.current.state.status).toBe("loading");
    await act(async () => { next.resolve(response()); await nextWork; });
  });
  it.each(["success", "error"])("unmount cancels pending %s and fresh result starts empty", async (outcome) => {
    const pending = deferred<WeatherForecastResponse>(); api.mockReturnValueOnce(pending.promise); const { result, unmount } = renderHook(() => useWeatherForecast(request())); let work!: Promise<boolean>;
    act(() => { work = result.current.query(); }); unmount(); expect(api.mock.calls[0][1]?.aborted).toBe(true);
    await act(async () => { if (outcome === "success") pending.resolve(response()); else pending.reject(new Error("late unmount")); expect(await work).toBe(false); });
    const next = renderHook(() => useWeatherForecast(request())); expect(next.result.current.state.status).toBe("idle"); expect(next.result.current.state.response).toBeNull(); expect(api).toHaveBeenCalledTimes(1);
  });
  it("invalid changed input clears old data and refuses to query", async () => {
    api.mockResolvedValueOnce(response()); const { result, rerender } = renderHook(({ input }) => useWeatherForecast(input), { initialProps: { input: request() } }); await act(async () => { await result.current.query(); });
    rerender({ input: { start_date: "2026-10-10", end_date: "2026-10-14" } }); expect(result.current.canQuery).toBe(false); expect(result.current.state.response).toBeNull();
    await act(async () => { expect(await result.current.query()).toBe(false); }); expect(api).toHaveBeenCalledTimes(1);
  });
});
