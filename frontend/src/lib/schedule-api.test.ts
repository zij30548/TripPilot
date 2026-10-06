import { afterEach, describe, expect, it, vi } from "vitest";
import { querySchedulePreview } from "./schedule-api";
import { isScheduleRequest, isScheduleResponse, scheduleClock, scheduleDates, type ScheduleEdge, type ScheduleItem, type ScheduleRequest, type ScheduleResponse } from "@/types/schedule";
import type { Place } from "@/types/place";

const place = (id: string, longitude = 121.41): Place => ({ id, name: id, address: "测试地址", latitude: 31.2, longitude, category: "原类别", source: "amap" });
function request(): ScheduleRequest { return {
  start_date: "2026-10-10", end_date: "2026-10-11", daily_start_time: "09:00", daily_end_time: "18:00",
  accommodation_place: place("hotel", 121.40000014), must_visit_places: [place("required")],
  duration_settings: [{ place_id: "required", minutes: 60, source: "default" }], lunch: { enabled: true, start_time: "12:00", end_time: "13:00" },
}; }
const endpoint = (p: Place) => ({ place_id: p.id, longitude: Number(p.longitude.toFixed(6)), latitude: Number(p.latitude.toFixed(6)) });
const item = (kind: ScheduleItem["kind"], start: string, end: string, duration: number, extra: Partial<ScheduleItem> = {}): ScheduleItem => ({
  kind, start_time: start, end_time: end, duration_minutes: duration, place_id: null, from_place_id: null, to_place_id: null, edge_id: null, duration_source: null, ...extra,
});
function edge(id: string, origin: Place, destination: Place, seconds = 120): ScheduleEdge { return {
  id, origin: endpoint(origin), destination: endpoint(destination), status: "ok", duration_seconds: seconds, duration_minutes: Math.ceil(seconds / 60),
  distance_meters: 123, source: "amap", queried_at: "2026-10-06T03:00:00Z", message: null, used: true,
}; }
function response(input = request()): ScheduleResponse {
  return { status: "complete", generated_at: "2026-10-06T03:00:00.123456Z", request: structuredClone(input),
    rules: ["固定必去输入顺序"], unknowns: ["尚未校验营业时间"], unscheduled: [],
    edges: [edge("out", input.accommodation_place, input.must_visit_places[0], 61.2), edge("back", input.must_visit_places[0], input.accommodation_place)],
    days: scheduleDates(input.start_date, input.end_date)!.map((date, index) => ({ date, return_time: index ? null : "10:04", items: index ? [] : [
      item("walk", "09:00", "09:02", 2, { from_place_id: input.accommodation_place.id, to_place_id: input.must_visit_places[0].id, edge_id: "out" }),
      item("visit", "09:02", "10:02", 60, { place_id: input.must_visit_places[0].id, duration_source: input.duration_settings[0].source }),
      item("walk", "10:02", "10:04", 2, { from_place_id: input.must_visit_places[0].id, to_place_id: input.accommodation_place.id, edge_id: "back" }),
    ] })),
  };
}
function mockResponse(data: unknown) { vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => data })); }
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("schedule preview request and response contract", () => {
  it("posts only the independent inputs and accepts real seconds/ceiling minutes and rounded coordinates", async () => {
    const input = request(); const data = response(input); mockResponse(data);
    expect(await querySchedulePreview(input)).toEqual(data); expect(fetch).toHaveBeenCalledTimes(1);
    expect(vi.mocked(fetch).mock.calls[0][0]).toBe("http://127.0.0.1:8000/trips/schedule-preview");
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[0][1]?.body))).toEqual(input);
    expect(data.edges[0].duration_seconds).toBe(61.2); expect(data.edges[0].duration_minutes).toBe(2);
    expect(data.edges[0].origin.longitude).toBe(121.4); expect(input.accommodation_place.longitude).toBe(121.40000014);
  });
  it("supports exactly one to three actual calendar dates and only zero legacy seconds", () => {
    expect(scheduleDates("2026-10-10", "2026-10-10")).toEqual(["2026-10-10"]);
    expect(scheduleDates("2026-10-10", "2026-10-12")).toHaveLength(3);
    expect(scheduleDates("2026-10-10", "2026-10-13")).toBeNull(); expect(scheduleDates("2026-02-31", "2026-03-01")).toBeNull();
    expect(scheduleClock("09:00:00")).toBe("09:00"); expect(scheduleClock("09:00:01")).toBe("09:00:01");
  });
  it("accepts shared accommodation as a real required visit with one zero-move edge reused", () => {
    const input = request(); input.must_visit_places = [input.accommodation_place]; input.duration_settings = [{ place_id: "hotel", minutes: 60, source: "user" }];
    const data = response(input); data.edges = [{ ...edge("zero", input.accommodation_place, input.accommodation_place), status: "same_place", source: "same_place", duration_seconds: 0, duration_minutes: 0, distance_meters: 0 }];
    data.days[0].items = [item("walk", "09:00", "09:00", 0, { from_place_id: "hotel", to_place_id: "hotel", edge_id: "zero" }),
      item("visit", "09:00", "10:00", 60, { place_id: "hotel", duration_source: "user" }),
      item("walk", "10:00", "10:00", 0, { from_place_id: "hotel", to_place_id: "hotel", edge_id: "zero" })];
    data.days[0].return_time = "10:00"; expect(isScheduleResponse(data, input)).toBe(true);
  });
  it("accepts wait/lunch before a whole visit, never inventing place IDs or duration source for the lunch", () => {
    const input = request(); input.duration_settings[0] = { ...input.duration_settings[0], minutes: 180, source: "user" };
    const data = response(input); data.days[0].items = [data.days[0].items[0], item("wait", "09:02", "12:00", 178), item("lunch", "12:00", "13:00", 60),
      item("visit", "13:00", "16:00", 180, { place_id: "required", duration_source: "user" }),
      item("walk", "16:00", "16:02", 2, { from_place_id: "required", to_place_id: "hotel", edge_id: "back" })];
    data.days[0].return_time = "16:02"; expect(isScheduleResponse(data, input)).toBe(true);
  });
  it("accepts partial prefix with a failed unused edge, but never calls it complete", () => {
    const input = request(); input.must_visit_places.push(place("second", 121.42)); input.duration_settings.push({ place_id: "second", minutes: 60, source: "default" });
    const data = response(input); data.status = "partial"; data.unscheduled = [{ place_id: "second", reason: "route_timeout", message: "所需步行信息超时。" }];
    data.edges.push({ ...edge("failed", input.must_visit_places[0], input.must_visit_places[1]), used: false, status: "timeout", duration_seconds: null, duration_minutes: null, distance_meters: null, message: "查询超时。" });
    expect(isScheduleResponse(data, input)).toBe(true);
  });
  it("accepts a completed schedule despite unrelated unused failure", () => {
    const input = request(); const data = response(input);
    data.edges.push({ ...edge("unused", input.must_visit_places[0], input.must_visit_places[0]), used: false, status: "failed", duration_seconds: null, duration_minutes: null, distance_meters: null, message: "查询未完成。" });
    expect(isScheduleResponse(data, input)).toBe(true);
  });
  it("accepts all unscheduled with all requested dates empty", () => {
    const input = request(); const data = response(input); data.status = "unscheduled"; data.edges = [];
    data.days = data.days.map((day) => ({ ...day, items: [], return_time: null })); data.unscheduled = [{ place_id: "required", reason: "time_window", message: "每日时间不足。" }];
    expect(isScheduleResponse(data, input)).toBe(true);
  });
  it.each([
    ["empty must list", (input: ScheduleRequest) => { input.must_visit_places = []; input.duration_settings = []; }],
    ["seven musts", (input: ScheduleRequest) => { input.must_visit_places = Array.from({ length: 7 }, (_, index) => place(`${index}`)); }],
    ["duplicate must ID", (input: ScheduleRequest) => { input.must_visit_places.push(input.must_visit_places[0]); }],
    ["missing setting", (input: ScheduleRequest) => { input.duration_settings = []; }],
    ["unknown setting ID", (input: ScheduleRequest) => { input.duration_settings[0].place_id = "other"; }],
    ["fractional duration", (input: ScheduleRequest) => { input.duration_settings[0].minutes = 60.2; }],
    ["string duration", (input: ScheduleRequest) => { Object.assign(input.duration_settings[0], { minutes: "60" }); }],
    ["too short duration", (input: ScheduleRequest) => { input.duration_settings[0].minutes = 14; }],
    ["too long duration", (input: ScheduleRequest) => { input.duration_settings[0].minutes = 481; }],
    ["false default", (input: ScheduleRequest) => { input.duration_settings[0].minutes = 120; }],
    ["unknown source", (input: ScheduleRequest) => { Object.assign(input.duration_settings[0], { source: "amap" }); }],
    ["invalid coordinate", (input: ScheduleRequest) => { input.accommodation_place.longitude = NaN; }],
    ["unsafe POI ID", (input: ScheduleRequest) => { input.accommodation_place.id = "https://example.invalid"; }],
    ["overlong POI ID", (input: ScheduleRequest) => { input.accommodation_place.id = "A".repeat(129); }],
    ["invented Place time", (input: ScheduleRequest) => { Object.assign(input.must_visit_places[0], { stay: 60 }); }],
    ["four days", (input: ScheduleRequest) => { input.end_date = "2026-10-13"; }],
    ["invalid calendar date", (input: ScheduleRequest) => { input.start_date = "2026-02-31"; }],
    ["hidden nonzero seconds", (input: ScheduleRequest) => { input.daily_start_time = "09:00:30"; }],
    ["invalid lunch order", (input: ScheduleRequest) => { input.lunch.start_time = "14:00"; }],
    ["lunch outside daily window", (input: ScheduleRequest) => { input.lunch.end_time = "19:00"; }],
    ["arbitrary city", (input: ScheduleRequest) => { Object.assign(input, { city: "北京" }); }],
    ["candidate optional list", (input: ScheduleRequest) => { Object.assign(input, { optional_places: [place("extra")] }); }],
  ])("rejects request %s without network", async (_, mutate) => {
    const input = request(); mutate(input); expect(isScheduleRequest(input)).toBe(false);
    await expect(querySchedulePreview(input)).rejects.toThrow("请检查"); expect(fetch).not.toHaveBeenCalled();
  });
  it.each([
    ["changed accommodation", (data: ScheduleResponse) => { data.request.accommodation_place.name = "another"; }],
    ["changed visit minutes", (data: ScheduleResponse) => { data.request.duration_settings[0].minutes = 120; data.request.duration_settings[0].source = "user"; }],
    ["changed dates", (data: ScheduleResponse) => { data.days[0].date = "2026-10-12"; }],
    ["missing requested date", (data: ScheduleResponse) => { data.days.pop(); }],
    ["guessed endpoint coordinate", (data: ScheduleResponse) => { data.edges[0].origin.longitude = 121.5; }],
    ["unknown endpoint ID", (data: ScheduleResponse) => { data.edges[0].origin.place_id = "other"; }],
    ["missing source", (data: ScheduleResponse) => { Object.assign(data.edges[0], { source: null }); }],
    ["non-ceiling minutes", (data: ScheduleResponse) => { data.edges[0].duration_minutes = 1; }],
    ["NaN seconds", (data: ScheduleResponse) => { data.edges[0].duration_seconds = NaN; }],
    ["negative distance", (data: ScheduleResponse) => { data.edges[0].distance_meters = -1; }],
    ["fake same-place status", (data: ScheduleResponse) => { Object.assign(data.edges[0], { status: "same_place", source: "same_place", duration_seconds: 0, duration_minutes: 0, distance_meters: 0 }); }],
    ["unknown edge reference", (data: ScheduleResponse) => { data.days[0].items[0].edge_id = "missing"; }],
    ["used flag false on actual edge", (data: ScheduleResponse) => { data.edges[0].used = false; }],
    ["duplicate edge ID", (data: ScheduleResponse) => { data.edges.push(data.edges[0]); }],
    ["duplicate directed edge", (data: ScheduleResponse) => { data.edges.push({ ...data.edges[0], id: "other" }); }],
    ["unknown visit ID", (data: ScheduleResponse) => { data.days[0].items[1].place_id = "other"; }],
    ["changed visit provenance", (data: ScheduleResponse) => { data.days[0].items[1].duration_source = "user"; }],
    ["timeline gap", (data: ScheduleResponse) => { data.days[0].items[1].start_time = "09:03"; }],
    ["timeline overlap", (data: ScheduleResponse) => { data.days[0].items[1].start_time = "09:01"; }],
    ["duration and clocks disagree", (data: ScheduleResponse) => { data.days[0].items[1].duration_minutes = 59; }],
    ["no return leg", (data: ScheduleResponse) => { data.days[0].items.pop(); }],
    ["return time inconsistent", (data: ScheduleResponse) => { data.days[0].return_time = "10:05"; }],
    ["fictitious empty day lunch", (data: ScheduleResponse) => { data.days[1].items = [item("lunch", "12:00", "13:00", 60)]; data.days[1].return_time = "13:00"; }],
    ["scheduled and unscheduled overlap", (data: ScheduleResponse) => { data.unscheduled = [{ place_id: "required", reason: "time_window", message: "无时间" }]; }],
    ["wrong aggregate outcome", (data: ScheduleResponse) => { data.status = "partial"; }],
    ["unstructured rules", (data: ScheduleResponse) => { Object.assign(data, { rules: { best: true } }); }],
    ["missing unknown notices", (data: ScheduleResponse) => { data.unknowns = []; }],
    ["invalid time stamp", (data: ScheduleResponse) => { data.generated_at = "yesterday"; }],
  ])("rejects malformed response %s", async (_, mutate) => {
    const data = response(); mutate(data); mockResponse(data);
    await expect(querySchedulePreview(request())).rejects.toThrow("数据不完整或时间不一致");
  });
  it("rejects skipping original required order even if all IDs are present", () => {
    const input = request(); input.must_visit_places.unshift(place("first", 121.42)); input.duration_settings.unshift({ place_id: "first", minutes: 60, source: "default" });
    const data = response(request()); data.request = input; data.status = "partial"; data.unscheduled = [{ place_id: "first", reason: "time_window", message: "时间不足" }];
    expect(isScheduleResponse(data, input)).toBe(false);
  });
  it.each([422, 500, 502, 503, 504])("HTTP %i does not expose response text or auto retry", async (status) => {
    const json = vi.fn(async () => ({ detail: "sensitive raw upstream URL" })); vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status, json }));
    await expect(querySchedulePreview(request())).rejects.not.toThrow("sensitive"); expect(json).not.toHaveBeenCalled(); expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("masks arbitrary transport and JSON errors", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("sensitive URL"))); await expect(querySchedulePreview(request())).rejects.toThrow("无法生成");
    vi.mocked(fetch).mockResolvedValue({ ok: true, json: async () => { throw new SyntaxError("sensitive payload"); } } as unknown as Response);
    await expect(querySchedulePreview(request())).rejects.toThrow("无法生成");
  });
  it("uses a 25 second timeout and abort cleanup without retry", async () => {
    vi.useFakeTimers(); vi.stubGlobal("fetch", vi.fn((_url, init) => new Promise((_resolve, reject) => init.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError"))))));
    const outcome = querySchedulePreview(request()).catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(24_999); expect(vi.mocked(fetch).mock.calls[0][1]?.signal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1); expect(await outcome).toMatchObject({ kind: "timeout" }); expect(fetch).toHaveBeenCalledTimes(1); expect(vi.getTimerCount()).toBe(0);
  });
  it("ignores late success after explicit abort, including fetches that ignore abort", async () => {
    vi.useFakeTimers(); let resolve!: (value: unknown) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise((done) => { resolve = done; })));
    const abort = new AbortController(); const work = querySchedulePreview(request(), abort.signal).catch((error: unknown) => error);
    abort.abort(); resolve({ ok: true, json: async () => response() }); expect(await work).toMatchObject({ name: "AbortError" }); expect(vi.getTimerCount()).toBe(0);
    await expect(querySchedulePreview(request(), abort.signal)).rejects.toMatchObject({ name: "AbortError" }); expect(fetch).toHaveBeenCalledTimes(1);
  });
});
