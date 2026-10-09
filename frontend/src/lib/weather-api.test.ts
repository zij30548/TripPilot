import { afterEach, describe, expect, it, vi } from "vitest";
import { queryWeatherForecast } from "./weather-api";
import { classifyWeatherPrecipitation, getShanghaiDate, getWeatherDates, getWeatherFreshness, isWeatherForecastRequest, isWeatherForecastResponse, type WeatherForecastRequest, type WeatherForecastResponse, type WeatherPeriod } from "@/types/weather";

const request = (): WeatherForecastRequest => ({ start_date: "2026-12-31", end_date: "2027-01-02" });
const period = (weather: string | null = "晴", temperature: number | null = 0): WeatherPeriod => ({ weather, temperature_celsius: temperature, wind_direction: "东", wind_power: "1-3", precipitation: classifyWeatherPrecipitation(weather), precipitation_basis: weather });
function response(input = request()): WeatherForecastResponse {
  return { request: { ...input }, source: "amap", city: "上海市", adcode: "310000", timezone: "Asia/Shanghai", queried_at: "2026-12-31T00:00:00.123456Z", reported_at: "2026-12-31T07:00:00+08:00", report_time_status: "valid", freshness_at_query: "fresh", coverage: "complete", guidance_rule: "amap_text_precipitation_v1",
    days: getWeatherDates(input).map((date) => ({ date, status: "available", day: period(), night: period("小雨", -2) })) };
}
const stubResponse = (data: unknown) => vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => data }));
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers(); });

describe("independent weather contract and date semantics", () => {
  it.each([
    ["2026-10-10", "2026-10-10", ["2026-10-10"]],
    ["2026-01-31", "2026-02-01", ["2026-01-31", "2026-02-01"]],
    ["2026-12-31", "2027-01-02", ["2026-12-31", "2027-01-01", "2027-01-02"]],
    ["2028-02-28", "2028-03-01", ["2028-02-28", "2028-02-29", "2028-03-01"]],
    ["0001-12-31", "0002-01-01", ["0001-12-31", "0002-01-01"]],
  ])("uses all requested dates across %s–%s", (start_date, end_date, dates) => {
    const input = { start_date, end_date }; expect(isWeatherForecastRequest(input)).toBe(true); expect(getWeatherDates(input)).toEqual(dates); expect(isWeatherForecastResponse(response(input), input)).toBe(true);
  });
  it.each([
    { start_date: "2026-10-10", end_date: "2026-10-13" },
    { start_date: "2026-10-11", end_date: "2026-10-10" },
    { start_date: "2026-02-29", end_date: "2026-03-01" },
    { start_date: "2026-13-01", end_date: "2026-13-02" },
    { start_date: "0000-01-01", end_date: "0000-01-01" },
    { start_date: "2026-1-01", end_date: "2026-01-02" },
    { ...request(), key: "not-accepted" }, { ...request(), city: "北京" }, { ...request(), url: "https://example.invalid" },
    { start_date: ["2026-10-10"], end_date: "2026-10-11" },
  ])("rejects invalid/non-date request %j before network", async (input) => {
    vi.stubGlobal("fetch", vi.fn()); expect(isWeatherForecastRequest(input)).toBe(false);
    await expect(queryWeatherForecast(input as unknown as WeatherForecastRequest)).rejects.toThrow("1～3 天"); expect(fetch).not.toHaveBeenCalled();
  });
  it("accepts zero, negative temperatures, normal missing fields and unknown descriptions without inventing probabilities", () => {
    const data = response(); data.days[0].day = period("未列入词表的新天气", 0); data.days[0].night = { ...period(null, null), wind_direction: null, wind_power: null };
    expect(isWeatherForecastResponse(data, request())).toBe(true); expect(data.days[1].night?.temperature_celsius).toBe(-2); expect(data.days[0].day.precipitation).toBe("unknown");
  });
  it.each([
    ["晴", "none"], ["小雨", "rain"], ["雷阵雨并伴有冰雹", "rain"], ["冻雨", "rain"], ["阵雪", "snow"], ["小雪-中雪", "snow"], ["雨夹雪", "rain_snow"], ["阵雨夹雪", "rain_snow"], ["未来雨况未知", "unknown"], ["未知", "unknown"], [null, "unknown"],
  ])("evaluates exact weather description %s as %s, not substring guessing", (description, expected) => expect(classifyWeatherPrecipitation(description)).toBe(expected));
  it("validates day and night separately and preserves partial/zero coverage", () => {
    const data = response(); expect(data.days[0].day?.precipitation).toBe("none"); expect(data.days[0].night?.precipitation).toBe("rain");
    data.days[1] = { date: data.days[1].date, status: "unavailable", day: null, night: null }; data.coverage = "partial"; expect(isWeatherForecastResponse(data)).toBe(true);
    data.days = data.days.map((day) => ({ date: day.date, status: "unavailable", day: null, night: null })); data.coverage = "none"; data.reported_at = null; data.report_time_status = "missing"; data.freshness_at_query = "unknown"; expect(isWeatherForecastResponse(data)).toBe(true);
  });
  it("uses Shanghai midnight independent of host timezone and changes stale locally only after 24 hours", () => {
    expect(getShanghaiDate(Date.parse("2026-12-31T15:59:59Z"))).toBe("2026-12-31"); expect(getShanghaiDate(Date.parse("2026-12-31T16:00:00Z"))).toBe("2027-01-01");
    const data = response(); const reported = Date.parse(data.reported_at!);
    expect(getWeatherFreshness(data, reported + 86_400_000)).toBe("fresh"); expect(getWeatherFreshness(data, reported + 86_400_001)).toBe("stale");
    expect(getWeatherFreshness({ reported_at: null, report_time_status: "missing" })).toBe("unknown");
    expect(getWeatherFreshness({ reported_at: null, report_time_status: "invalid" })).toBe("unknown");
    expect(getWeatherFreshness(data, reported - 300_001)).toBe("unknown"); expect(getWeatherFreshness(data, reported - 300_000)).toBe("fresh");
  });
  it("accepts stale source time without replacing it with query time, and invalid source time is unknown", () => {
    const data = response(); data.reported_at = "2026-12-29T00:00:00+08:00"; data.freshness_at_query = "stale"; expect(isWeatherForecastResponse(data)).toBe(true);
    data.reported_at = null; data.report_time_status = "invalid"; data.freshness_at_query = "unknown"; expect(isWeatherForecastResponse(data)).toBe(true);
  });
  it("keeps Python microseconds at the 24-hour freshness boundary and counts text Unicode characters consistently", () => {
    const data = response(); data.reported_at = "2026-12-30T08:00:00+08:00"; data.queried_at = "2026-12-31T00:00:00.000001Z"; data.freshness_at_query = "stale";
    data.days[0].day = period("🌦".repeat(100)); expect(isWeatherForecastResponse(data)).toBe(true);
    data.freshness_at_query = "fresh"; expect(isWeatherForecastResponse(data)).toBe(false);
  });
  it.each([
    ["extra top-level field", (data: WeatherForecastResponse) => Object.assign(data, { rain_risk: 0.2 })],
    ["wrong city", (data: WeatherForecastResponse) => Object.assign(data, { city: "北京市" })],
    ["wrong adcode", (data: WeatherForecastResponse) => Object.assign(data, { adcode: "110000" })],
    ["wrong source", (data: WeatherForecastResponse) => Object.assign(data, { source: "mock" })],
    ["host timezone", (data: WeatherForecastResponse) => Object.assign(data, { timezone: "UTC" })],
    ["other rules", (data: WeatherForecastResponse) => Object.assign(data, { guidance_rule: "heuristic" })],
    ["wrong request echo", (data: WeatherForecastResponse) => { data.request.start_date = "2027-01-01"; }],
    ["wrong count", (data: WeatherForecastResponse) => { data.days.pop(); }],
    ["array-position/unsorted dates", (data: WeatherForecastResponse) => { data.days.reverse(); }],
    ["duplicate date", (data: WeatherForecastResponse) => { data.days[1].date = data.days[0].date; }],
    ["wrong coverage", (data: WeatherForecastResponse) => { data.coverage = "none"; }],
    ["missing day", (data: WeatherForecastResponse) => { data.days[0].day = null; }],
    ["filled unavailable", (data: WeatherForecastResponse) => { data.days[0].status = "unavailable"; }],
    ["invented maximum temperature", (data: WeatherForecastResponse) => { Object.assign(data.days[0].day!, { max_temperature: 5 }); }],
    ["temperature string", (data: WeatherForecastResponse) => { Object.assign(data.days[0].day!, { temperature_celsius: "0" }); }],
    ["temperature boolean", (data: WeatherForecastResponse) => { Object.assign(data.days[0].day!, { temperature_celsius: false }); }],
    ["temperature infinity", (data: WeatherForecastResponse) => { data.days[0].day!.temperature_celsius = Infinity; }],
    ["temperature out of bound", (data: WeatherForecastResponse) => { data.days[0].day!.temperature_celsius = -101; }],
    ["empty description", (data: WeatherForecastResponse) => { data.days[0].day!.weather = ""; }],
    ["array description", (data: WeatherForecastResponse) => { Object.assign(data.days[0].day!, { weather: [] }); }],
    ["untrimmed wind", (data: WeatherForecastResponse) => { data.days[0].day!.wind_direction = " 东 "; }],
    ["control character", (data: WeatherForecastResponse) => { data.days[0].day!.wind_power = "1\n3"; }],
    ["BOM inside text", (data: WeatherForecastResponse) => { data.days[0].day!.wind_power = "1\ufeff3"; }],
    ["different basis", (data: WeatherForecastResponse) => { data.days[0].night!.precipitation_basis = "晴"; }],
    ["wrong precipitation", (data: WeatherForecastResponse) => { data.days[0].night!.precipitation = "none"; }],
    ["illegal query date", (data: WeatherForecastResponse) => { data.queried_at = "2026-02-30T00:00:00Z"; }],
    ["query missing timezone", (data: WeatherForecastResponse) => { data.queried_at = "2026-12-31T00:00:00"; }],
    ["query invalid hour", (data: WeatherForecastResponse) => { data.queried_at = "2026-12-31T24:00:00Z"; }],
    ["reported without Shanghai offset", (data: WeatherForecastResponse) => { data.reported_at = "2026-12-31T07:00:00"; }],
    ["reported invalid date", (data: WeatherForecastResponse) => { data.reported_at = "2026-02-30T00:00:00+08:00"; }],
    ["reported year anomaly", (data: WeatherForecastResponse) => { data.reported_at = "1999-12-31T00:00:00+08:00"; data.freshness_at_query = "stale"; }],
    ["reported too far in future", (data: WeatherForecastResponse) => { data.reported_at = "2026-12-31T08:06:00+08:00"; }],
    ["missing source masked as fresh", (data: WeatherForecastResponse) => { data.reported_at = null; data.report_time_status = "missing"; }],
    ["old source masked as fresh", (data: WeatherForecastResponse) => { data.reported_at = "2026-12-28T07:00:00+08:00"; }],
  ])("rejects inconsistent response: %s", (_, mutate) => { const data = response(); mutate(data); expect(isWeatherForecastResponse(data, request())).toBe(false); });
});

describe("weather request boundary and safe errors", () => {
  it("sends one date-only batch request to the backend, snapshots input and validates its echo", async () => {
    const data = response(); stubResponse(data); const input = request(); const work = queryWeatherForecast(input); input.start_date = "2027-01-01";
    expect(await work).toEqual(data); expect(fetch).toHaveBeenCalledTimes(1);
    const [url, init] = vi.mocked(fetch).mock.calls[0]; expect(url).toBe("http://127.0.0.1:8000/weather/forecast"); expect(init?.method).toBe("POST"); expect(JSON.parse(String(init?.body))).toEqual(request());
  });
  it.each([422, 502, 503, 504, 500, 429])("masks error body on HTTP %i and does not retry", async (status) => {
    const json = vi.fn(async () => ({ detail: "sensitive upstream URL" })); vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status, json }));
    await expect(queryWeatherForecast(request())).rejects.not.toThrow("sensitive"); expect(json).not.toHaveBeenCalled(); expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("never treats malformed successful data as a no-coverage result", async () => {
    stubResponse({ days: [] }); await expect(queryWeatherForecast(request())).rejects.toThrow("数据不完整或格式无效");
  });
  it("masks fetch and JSON exceptions", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("sensitive URL"))); await expect(queryWeatherForecast(request())).rejects.toThrow("无法查询天气");
    vi.mocked(fetch).mockResolvedValue({ ok: true, json: async () => { throw new SyntaxError("sensitive payload"); } } as unknown as Response);
    await expect(queryWeatherForecast(request())).rejects.toThrow("无法查询天气");
  });
  it("aborts at 15 seconds, cleans timer and never retries", async () => {
    vi.useFakeTimers(); vi.stubGlobal("fetch", vi.fn((_url, init) => new Promise((_resolve, reject) => init.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError"))))));
    const work = queryWeatherForecast(request()).catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(14_999); expect(vi.mocked(fetch).mock.calls[0][1]?.signal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1); expect(await work).toMatchObject({ kind: "timeout" }); expect(fetch).toHaveBeenCalledTimes(1); expect(vi.getTimerCount()).toBe(0);
  });
  it("caller abort wins over late successful response and an already aborted signal never sends", async () => {
    vi.useFakeTimers(); let resolve!: (data: unknown) => void; vi.stubGlobal("fetch", vi.fn(() => new Promise((done) => { resolve = done; })));
    const controller = new AbortController(); const work = queryWeatherForecast(request(), controller.signal).catch((error: unknown) => error); controller.abort();
    resolve({ ok: true, json: async () => response() }); expect(await work).toMatchObject({ name: "AbortError" }); expect(vi.getTimerCount()).toBe(0);
    await expect(queryWeatherForecast(request(), controller.signal)).rejects.toMatchObject({ name: "AbortError" }); expect(fetch).toHaveBeenCalledTimes(1);
  });
});
