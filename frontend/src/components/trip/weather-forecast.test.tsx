import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import WeatherForecast from "./weather-forecast";
import type { useWeatherForecast } from "@/lib/use-weather-forecast";
import { getWeatherDates, type WeatherForecastRequest, type WeatherForecastResponse, type WeatherPeriod } from "@/types/weather";

// Explicit offline weather fixtures, not real forecast observations.
const request: WeatherForecastRequest = { start_date: "2026-10-10", end_date: "2026-10-12" };
const rain: WeatherPeriod = { weather: "小雨", temperature_celsius: 0, wind_direction: "东", wind_power: "≤3", precipitation: "rain", precipitation_basis: "小雨" };
const snow: WeatherPeriod = { weather: "小雪", temperature_celsius: -4, wind_direction: null, wind_power: null, precipitation: "snow", precipitation_basis: "小雪" };
const sunny: WeatherPeriod = { ...rain, weather: "晴", precipitation: "none", precipitation_basis: "晴" };
function response(overrides: Partial<WeatherForecastResponse> = {}): WeatherForecastResponse {
  return { request, source: "amap", city: "上海市", adcode: "310000", timezone: "Asia/Shanghai", queried_at: "2026-10-10T01:00:00Z", reported_at: "2026-10-10T08:00:00+08:00", report_time_status: "valid", freshness_at_query: "fresh", coverage: "complete", guidance_rule: "amap_text_precipitation_v1", days: getWeatherDates(request).map((date) => ({ date, status: "available", day: rain, night: snow })), ...overrides };
}
function controller(value: WeatherForecastResponse | null = null, overrides: Partial<ReturnType<typeof useWeatherForecast>["state"]> = {}): ReturnType<typeof useWeatherForecast> {
  return { canQuery: true, query: vi.fn().mockResolvedValue(true), invalidate: vi.fn(), state: { status: value ? "success" : "idle", response: value, message: null, showingPrevious: false, ...overrides } };
}
const dayCard = (date = "2026-10-10") => within(screen.getByRole("article", { name: `${date} 天气预报` }));

beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date("2026-10-10T09:00:00+08:00")); });
afterEach(() => vi.useRealTimers());

describe("Shanghai forecast panel", () => {
  it.each([
    ["2026-10-10", "2026-10-10", ["2026-10-10"]],
    ["2026-10-31", "2026-11-01", ["2026-10-31", "2026-11-01"]],
    ["2026-12-31", "2027-01-02", ["2026-12-31", "2027-01-01", "2027-01-02"]],
  ])("renders every confirmed date %s–%s without requesting on entry", (start, end, dates) => {
    const weather = controller();
    render(<WeatherForecast request={{ start_date: start, end_date: end }} weather={weather} />);
    expect(screen.getAllByRole("article")).toHaveLength(dates.length);
    for (const date of dates) expect(dayCard(date).getByText("尚未查询该日期预报。")).toBeTruthy();
    expect(weather.query).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "查询天气" }));
    expect(weather.query).toHaveBeenCalledTimes(1);
  });

  it("shows distinct day/night facts including zero, negative temperatures and missing wind", () => {
    render(<WeatherForecast request={request} weather={controller(response())} />);
    const daytime = within(dayCard().getByRole("region", { name: "白天" }));
    const nighttime = within(dayCard().getByRole("region", { name: "夜间" }));
    expect(daytime.getByText("0°C")).toBeTruthy();
    expect(nighttime.getByText("-4°C")).toBeTruthy();
    expect(nighttime.getAllByText("未知")).toHaveLength(2);
    expect(daytime.getByText(/白天预报有雨/)).toBeTruthy();
    expect(daytime.queryByText(/夜间预报/)).toBeNull();
    expect(nighttime.getByText(/夜间预报有雪/)).toBeTruthy();
    expect(screen.getByText("来源：高德天气 · 上海市（310000）")).toBeTruthy();
    expect(screen.getByText(/成功查询时间：2026\/10\/10 09:00:00/)).toBeTruthy();
    expect(screen.getByText(/来源发布时间：2026\/10\/10 08:00:00/)).toBeTruthy();
    expect(screen.queryByText(/降雨概率/)).toBeNull();
  });

  it("night rain does not claim daytime rain; unfamiliar descriptions remain unevaluated", () => {
    const value = response(); value.days[0].day = sunny; value.days[0].night = rain;
    value.days[1].day = { ...rain, weather: "测试未知天气", precipitation: "unknown", precipitation_basis: "测试未知天气" };
    value.days[2].day = { ...rain, weather: null, temperature_celsius: null, precipitation: "unknown", precipitation_basis: null };
    render(<WeatherForecast request={request} weather={controller(value)} />);
    const daytime = within(dayCard().getByRole("region", { name: "白天" }));
    expect(daytime.getByText(/白天未触发雨雪提示规则，不代表保证适合出行/)).toBeTruthy();
    expect(daytime.queryByText(/准备防雨用品/)).toBeNull();
    expect(within(dayCard().getByRole("region", { name: "夜间" })).getByText(/夜间预报有雨/)).toBeTruthy();
    expect(dayCard("2026-10-11").getByText("测试未知天气")).toBeTruthy();
    expect(dayCard("2026-10-11").getByText(/白天天气描述未知或未纳入规则/)).toBeTruthy();
    expect(within(dayCard("2026-10-12").getByRole("region", { name: "白天" })).getAllByText("未知")).toHaveLength(2);
  });

  it("keeps missing dates visible and never calls them outside a claimed range", () => {
    const value = response({ coverage: "partial" }); value.days[1] = { date: "2026-10-11", status: "unavailable", day: null, night: null };
    render(<WeatherForecast request={request} weather={controller(value)} />);
    expect(dayCard("2026-10-11").getByText(/暂未取得该日期预报/)).toBeTruthy();
    expect(dayCard("2026-10-11").queryByText("小雨")).toBeNull();
    expect(dayCard("2026-10-12").getByText("小雨")).toBeTruthy();
    expect(screen.queryByText(/超出预报范围/)).toBeNull();
  });

  it("distinguishes valid zero coverage from failure and never inserts old values", () => {
    const value = response({ coverage: "none", days: getWeatherDates(request).map((date) => ({ date, status: "unavailable", day: null, night: null })) });
    const { rerender } = render(<WeatherForecast request={request} weather={controller(value)} />);
    expect(screen.getByText(/这不等同于查询失败/)).toBeTruthy();
    expect(screen.getAllByText(/暂未取得该日期预报/)).toHaveLength(3);
    expect(screen.queryByRole("alert")).toBeNull();
    rerender(<WeatherForecast request={request} weather={controller(null, { status: "failed", message: "天气查询超时。" })} />);
    expect(screen.getByRole("alert").textContent).toBe("天气查询超时。");
    expect(screen.getByRole("button", { name: "刷新天气" })).toBeTruthy();
  });

  it("marks last snapshot separately from staleness during and after failed refresh", () => {
    const value = response({ queried_at: "2026-10-09T01:00:00Z", reported_at: "2026-10-09T08:00:00+08:00" });
    const loading = controller(value, { status: "loading", showingPrevious: true });
    const view = render(<WeatherForecast request={request} weather={loading} />);
    expect(screen.getByText(/当前展示上次成功查询的整批结果；正在刷新/)).toBeTruthy();
    expect(screen.getByText(/预报已陈旧/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "正在查询天气…" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "正在查询天气…" }));
    expect(loading.query).not.toHaveBeenCalled();
    view.rerender(<WeatherForecast request={request} weather={controller(value, { status: "failed", showingPrevious: true, message: "本次刷新超时。" })} />);
    expect(screen.getByText(/本次刷新失败，查询时间与发布时间仍为原值/)).toBeTruthy();
    expect(screen.getByText(/成功查询时间：2026\/10\/9 09:00:00/)).toBeTruthy();
    expect(screen.queryByText(/白天预报有雨/)).toBeNull();
  });

  it.each(["missing", "invalid"] as const)("unknown source time (%s) never borrows query time for guidance", (status) => {
    render(<WeatherForecast request={request} weather={controller(response({ reported_at: null, report_time_status: status, freshness_at_query: "unknown" }))} />);
    expect(screen.getByText(/预报新鲜度未知/)).toBeTruthy();
    expect(screen.getByText(/来源发布时间：未知/)).toBeTruthy();
    expect(screen.queryByText(/白天预报有雨/)).toBeNull();
    expect(dayCard().getByText("小雨")).toBeTruthy();
  });

  it("updates at the strict 24-hour boundary without network requests", () => {
    const weather = controller(response({ reported_at: "2026-10-09T09:00:00+08:00" }));
    render(<WeatherForecast request={request} weather={weather} />);
    expect(dayCard().getByText(/白天预报有雨/)).toBeTruthy();
    act(() => vi.advanceTimersByTime(1));
    expect(screen.getByText(/预报已陈旧/)).toBeTruthy();
    expect(screen.queryByText(/白天预报有雨/)).toBeNull();
    expect(weather.query).not.toHaveBeenCalled();
  });

  it("uses the current clock immediately for a newly received stale snapshot, without waiting for an old timer", () => {
    const value = response({ reported_at: "2026-10-09T09:00:00+08:00" });
    const weather = controller(value);
    const view = render(<WeatherForecast request={request} weather={weather} />);
    expect(dayCard().getByText(/白天预报有雨/)).toBeTruthy();
    // Clock advances but suspended browser timers have not fired.
    vi.setSystemTime(new Date("2026-10-10T09:00:01+08:00"));
    const refreshed = controller({ ...value, queried_at: "2026-10-10T01:00:01Z", freshness_at_query: "stale" });
    view.rerender(<WeatherForecast request={request} weather={refreshed} />);
    expect(screen.getByText(/预报已陈旧/)).toBeTruthy();
    expect(screen.queryByText(/白天预报有雨/)).toBeNull();
    expect(weather.query).not.toHaveBeenCalled();
    expect(refreshed.query).not.toHaveBeenCalled();
  });

  it("marks yesterday at Shanghai midnight while retaining forecast facts, not historical actual weather", () => {
    vi.setSystemTime(new Date("2026-10-10T23:59:59+08:00"));
    const weather = controller(response({ queried_at: "2026-10-10T15:00:00Z", reported_at: "2026-10-10T22:00:00+08:00" }));
    render(<WeatherForecast request={request} weather={weather} />);
    expect(dayCard().queryByText(/该日期已过去/)).toBeNull();
    act(() => vi.advanceTimersByTime(1000));
    expect(dayCard().getByText(/旧预报不是历史实际天气/)).toBeTruthy();
    expect(dayCard().queryByText(/白天预报有雨/)).toBeNull();
    expect(dayCard("2026-10-11").getByText(/白天预报有雨/)).toBeTruthy();
    expect(weather.query).not.toHaveBeenCalled();
  });

  it("rechecks the local clock on visibility/focus and cleans timers on unmount without fetching", () => {
    const weather = controller(response());
    const view = render(<WeatherForecast request={request} weather={weather} />);
    vi.setSystemTime(new Date("2026-10-11T09:00:00+08:00"));
    fireEvent(document, new Event("visibilitychange"));
    expect(dayCard().getByText(/该日期已过去/)).toBeTruthy();
    expect(screen.getByText(/预报已陈旧/)).toBeTruthy();
    fireEvent(window, new Event("focus"));
    expect(weather.query).not.toHaveBeenCalled();
    view.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});
