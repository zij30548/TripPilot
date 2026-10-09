import { afterEach, describe, expect, it, vi } from "vitest";
import { querySchedulePreview } from "./schedule-api";
import { isScheduleRequest, isScheduleResponse, scheduleClock, scheduleDates, type ScheduleEdge, type ScheduleItem, type ScheduleRequest, type ScheduleResponse } from "@/types/schedule";
import type { Place } from "@/types/place";
import type { TransitRoute } from "@/types/transit";

const place = (id: string, longitude = 121.41): Place => ({ id, name: id, address: "测试地址", latitude: 31.2, longitude, category: "原类别", source: "amap" });
function request(): ScheduleResponse["request"] { return {
  transport_mode: "walking",
  start_date: "2026-10-10", end_date: "2026-10-11", daily_start_time: "09:00", daily_end_time: "18:00",
  accommodation_place: place("hotel", 121.40000014), must_visit_places: [place("required")],
  duration_settings: [{ place_id: "required", minutes: 60, source: "default" }], lunch: { enabled: true, start_time: "12:00", end_time: "13:00" },
}; }
const endpoint = (p: Place) => ({ place_id: p.id, longitude: Number(p.longitude.toFixed(6)), latitude: Number(p.latitude.toFixed(6)) });
const item = (kind: ScheduleItem["kind"], start: string, end: string, duration: number, extra: Partial<ScheduleItem> = {}): ScheduleItem => ({
  kind, start_time: start, end_time: end, duration_minutes: duration, place_id: null, from_place_id: null, to_place_id: null, edge_id: null, duration_source: null, ...extra,
});
function edge(id: string, origin: Place, destination: Place, seconds = 120): ScheduleEdge { return {
  transport_mode: "walking", transit_route: null, selection_rule: null,
  id, origin: endpoint(origin), destination: endpoint(destination), status: "ok", duration_seconds: seconds, duration_minutes: Math.ceil(seconds / 60),
  distance_meters: 123, source: "amap", queried_at: "2026-10-06T03:00:00Z", message: null, used: true,
}; }
function response(input: ScheduleRequest = request()): ScheduleResponse {
  return { status: "complete", generated_at: "2026-10-06T03:00:00.123456Z", request: { ...structuredClone(input), transport_mode: input.transport_mode ?? "walking" },
    rules: ["固定必去输入顺序"], unknowns: ["尚未校验营业时间"], unscheduled: [], optional_results: [],
    edges: [edge("out", input.accommodation_place, input.must_visit_places[0], 61.2), edge("back", input.must_visit_places[0], input.accommodation_place)],
    days: scheduleDates(input.start_date, input.end_date)!.map((date, index) => ({ date, return_time: index ? null : "10:04", items: index ? [] : [
      item("walk", "09:00", "09:02", 2, { from_place_id: input.accommodation_place.id, to_place_id: input.must_visit_places[0].id, edge_id: "out" }),
      item("visit", "09:02", "10:02", 60, { place_id: input.must_visit_places[0].id, duration_source: input.duration_settings[0].source }),
      item("walk", "10:02", "10:04", 2, { from_place_id: input.must_visit_places[0].id, to_place_id: input.accommodation_place.id, edge_id: "back" }),
    ] })),
  };
}
function mockResponse(data: unknown) { vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => data })); }
function withOptional(): { input: ScheduleResponse["request"]; data: ScheduleResponse } {
  const input = request(); const optional = place("optional", 121.42); input.optional_places = [optional];
  input.duration_settings.push({ place_id: optional.id, minutes: 60, source: "default" });
  const data = response(input); data.edges[1].used = false;
  data.edges.push(edge("tail-option", input.must_visit_places[0], optional), edge("option-home", optional, input.accommodation_place));
  data.days[0].items.splice(2, 1,
    item("walk", "10:02", "10:04", 2, { from_place_id: "required", to_place_id: "optional", edge_id: "tail-option" }),
    item("visit", "10:04", "11:04", 60, { place_id: "optional", duration_source: "default" }),
    item("walk", "11:04", "11:06", 2, { from_place_id: "optional", to_place_id: "hotel", edge_id: "option-home" }));
  data.days[0].return_time = "11:06";
  data.optional_results = [{ place_id: "optional", scheduled_date: input.start_date, not_attempted_reason: null,
    attempts: [{ date: input.start_date, outcome: "scheduled", message: "该日末尾已排入。" }] }];
  return { input, data };
}
function transitRoute(seconds: number): TransitRoute {
  return {
    duration_seconds: seconds, walking_distance_meters: 251, fare_cny: null, geometry_complete: false,
    legs: [
      { mode: "walking", distance_meters: 251, duration_seconds: null, instruction: "步行至测试站", line_name: null, departure_stop: null, arrival_stop: null, geometry: [], geometry_complete: false },
      { mode: "subway", distance_meters: null, duration_seconds: null, instruction: null, line_name: "测试地铁线路", departure_stop: "测试上车站", arrival_stop: "测试下车站", geometry: [], geometry_complete: false },
    ],
  };
}
function asTransit(input: ScheduleRequest, data: ScheduleResponse): void {
  input.transport_mode = "transit"; data.request.transport_mode = "transit";
  for (const entry of data.edges) {
    entry.transport_mode = "transit"; entry.distance_meters = null;
    if (entry.status === "ok") { entry.transit_route = transitRoute(entry.duration_seconds!); entry.selection_rule = "first_supported_complete"; }
  }
  for (const day of data.days) for (const entry of day.items) if (entry.kind === "walk") entry.kind = "transit";
}
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("schedule preview request and response contract", () => {
  it("accepts legacy omitted mode only on the request and requires the explicit walking echo", async () => {
    const input: ScheduleRequest = request(); delete input.transport_mode;
    const data = response(input); mockResponse(data);
    expect(await querySchedulePreview(input)).toEqual(data);
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[0][1]?.body)).transport_mode).toBeUndefined();
    delete (data.request as ScheduleRequest).transport_mode;
    expect(isScheduleResponse(data, input)).toBe(false);
  });
  it("accepts legacy omitted optional request with normalized empty echo/results", async () => {
    const input = request(); const data = response(input); data.request.optional_places = []; mockResponse(data);
    expect(await querySchedulePreview(input)).toEqual(data);
  });
  it("accepts optional appended only after the required tail without rewriting required stops", async () => {
    const { input, data } = withOptional(); mockResponse(data); expect(await querySchedulePreview(input)).toEqual(data);
    expect(data.days[0].items.filter((entry) => entry.kind === "visit").map((entry) => entry.place_id)).toEqual(["required", "optional"]);
  });
  it("allows optional-only input but never an entirely empty selection", () => {
    const { input, data } = withOptional(); input.must_visit_places = []; input.duration_settings = input.duration_settings.slice(1);
    data.request = structuredClone(input); data.edges = [edge("out", input.accommodation_place, input.optional_places![0]), edge("back", input.optional_places![0], input.accommodation_place)];
    data.days[0] = { date: input.start_date, return_time: "10:04", items: [
      item("walk", "09:00", "09:02", 2, { from_place_id: "hotel", to_place_id: "optional", edge_id: "out" }),
      item("visit", "09:02", "10:02", 60, { place_id: "optional", duration_source: "default" }),
      item("walk", "10:02", "10:04", 2, { from_place_id: "optional", to_place_id: "hotel", edge_id: "back" }),
    ] };
    expect(isScheduleRequest(input)).toBe(true); expect(isScheduleResponse(data, input)).toBe(true);
    input.optional_places = []; input.duration_settings = []; expect(isScheduleRequest(input)).toBe(false);
  });
  it("preserves optional input order while recording per-day slot use and later success", () => {
    const { input, data } = withOptional(); const second = place("secondOptional", 121.43);
    input.optional_places!.push(second); input.duration_settings.push({ place_id: second.id, minutes: 60, source: "default" }); data.request = structuredClone(input);
    data.edges.push(edge("out2", input.accommodation_place, second), edge("back2", second, input.accommodation_place));
    data.days[1] = { date: input.end_date, return_time: "10:04", items: [item("walk", "09:00", "09:02", 2, { from_place_id: "hotel", to_place_id: second.id, edge_id: "out2" }),
      item("visit", "09:02", "10:02", 60, { place_id: second.id, duration_source: "default" }), item("walk", "10:02", "10:04", 2, { from_place_id: second.id, to_place_id: "hotel", edge_id: "back2" })] };
    data.optional_results.push({ place_id: second.id, scheduled_date: input.end_date, not_attempted_reason: null, attempts: [
      { date: input.start_date, outcome: "day_slot_used", message: "该日已有一个可选地点。" }, { date: input.end_date, outcome: "scheduled", message: "已排入。" },
    ] }); expect(isScheduleResponse(data, input)).toBe(true);
    data.optional_results[1].attempts[0].outcome = "failed";
    expect(isScheduleResponse(data, input)).toBe(false); // The earlier optional already used this day's slot.
    data.optional_results[1].attempts[0].outcome = "day_slot_used";
    input.optional_places!.reverse(); data.request.optional_places!.reverse(); data.optional_results.reverse();
    expect(isScheduleResponse(data, input)).toBe(false); // An earlier candidate cannot blame a later winner without trying.
    data.optional_results[0].attempts[0].outcome = "time_window";
    expect(isScheduleResponse(data, input)).toBe(true);
  });
  it("unfinished required prefix prevents all optional attempts and reports must_incomplete", () => {
    const { input } = withOptional(); const data = response(input); data.status = "unscheduled"; data.edges = [];
    data.days = data.days.map((day) => ({ ...day, items: [], return_time: null }));
    data.unscheduled = [{ place_id: "required", reason: "time_window", message: "每日时间不足。" }];
    data.optional_results = [{ place_id: "optional", scheduled_date: null, not_attempted_reason: "must_incomplete", attempts: [] }];
    expect(isScheduleResponse(data, input)).toBe(true);
  });
  it("budget-exhausted optional edges remain unused and report each affected date", () => {
    const { input } = withOptional(); const data = response(input); data.status = "partial";
    data.edges.push({ ...edge("not-started", input.must_visit_places[0], input.optional_places![0]), status: "budget_exhausted", used: false,
      duration_seconds: null, duration_minutes: null, distance_meters: null, message: "本批查询预算已用尽，未启动。" });
    data.edges.push({ ...data.edges.at(-1)!, id: "day-two-not-started", origin: endpoint(input.accommodation_place) });
    data.optional_results = [{ place_id: "optional", scheduled_date: null, not_attempted_reason: null,
      attempts: data.days.map((day) => ({ date: day.date, outcome: "budget_exhausted", message: "本次未启动。" })) }];
    expect(isScheduleResponse(data, input)).toBe(true);
    data.edges.pop(); expect(isScheduleResponse(data, input)).toBe(false);
  });
  it.each([
    ["unsupported mode", (input: ScheduleRequest) => { Object.assign(input, { transport_mode: "driving" }); }],
    ["null mode", (input: ScheduleRequest) => { Object.assign(input, { transport_mode: null }); }],
    ["undefined explicit mode", (input: ScheduleRequest) => { Object.assign(input, { transport_mode: undefined }); }],
    ["four optional places", (input: ScheduleRequest) => { input.optional_places = Array.from({ length: 4 }, (_, index) => place(`o${index}`)); }],
    ["duplicate optional ID", (input: ScheduleRequest) => { input.optional_places!.push(input.optional_places![0]); }],
    ["optional is lodging", (input: ScheduleRequest) => { input.optional_places![0] = input.accommodation_place; }],
    ["optional is required", (input: ScheduleRequest) => { input.optional_places![0] = input.must_visit_places[0]; }],
    ["missing optional duration", (input: ScheduleRequest) => { input.duration_settings.pop(); }],
    ["null optional list", (input: ScheduleRequest) => { Object.assign(input, { optional_places: null }); }],
  ])("rejects %s", async (_, change) => {
    const { input } = withOptional(); change(input); expect(isScheduleRequest(input)).toBe(false);
    await expect(querySchedulePreview(input)).rejects.toThrow("请检查"); expect(fetch).not.toHaveBeenCalled();
  });
  it.each([
    ["optional snapshot changed", (data: ScheduleResponse) => { data.request.optional_places![0].address = "other"; }],
    ["optional result missing", (data: ScheduleResponse) => { data.optional_results = []; }],
    ["optional result ID replaced", (data: ScheduleResponse) => { data.optional_results[0].place_id = "fake"; }],
    ["optional scheduled date mismatches visit", (data: ScheduleResponse) => { data.optional_results[0].scheduled_date = "2026-10-11"; }],
    ["attempt omitted", (data: ScheduleResponse) => { data.optional_results[0].attempts = []; }],
    ["attempt claims another date", (data: ScheduleResponse) => { data.optional_results[0].attempts[0].date = "2026-10-09"; }],
    ["false failure for scheduled visit", (data: ScheduleResponse) => { data.optional_results[0].attempts[0].outcome = "time_window"; }],
    ["false must-incomplete claim", (data: ScheduleResponse) => { data.optional_results[0].not_attempted_reason = "must_incomplete"; }],
    ["same optional visited twice", (data: ScheduleResponse) => { data.days[1] = { ...data.days[0], date: "2026-10-11" }; }],
    ["illegal optional self-edge", (data: ScheduleResponse) => { const p = data.request.optional_places![0]; data.edges.push({ ...edge("bad", p, p), status: "same_place", source: "same_place", duration_minutes: 0, duration_seconds: 0, distance_meters: 0, used: false }); }],
    ["used budget failure", (data: ScheduleResponse) => { data.edges[2].status = "budget_exhausted"; }],
    ["wrong partial status for complete union", (data: ScheduleResponse) => { data.status = "partial"; }],
  ])("rejects inconsistent optional metadata: %s", async (_, change) => {
    const { input, data } = withOptional(); change(data); mockResponse(data);
    await expect(querySchedulePreview(input)).rejects.toThrow("数据不完整或时间不一致");
  });
  it("requires full per-date failures, rejects false slot-use and false success without a visit", () => {
    const { input } = withOptional(); const data = response(input); data.status = "partial";
    data.optional_results = [{ place_id: "optional", scheduled_date: null, not_attempted_reason: null,
      attempts: data.days.map((day) => ({ date: day.date, outcome: "time_window", message: "该日剩余时间不足。" })) }];
    expect(isScheduleResponse(data, input)).toBe(true);
    data.optional_results[0].attempts[0].outcome = "day_slot_used"; expect(isScheduleResponse(data, input)).toBe(false);
    data.optional_results[0].attempts[0].outcome = "scheduled"; expect(isScheduleResponse(data, input)).toBe(false);
    data.optional_results[0].attempts[0].outcome = "time_window"; data.optional_results[0].attempts.pop(); expect(isScheduleResponse(data, input)).toBe(false);
  });
  it("captures a complete immutable submitted snapshot while response is pending", async () => {
    const { input, data } = withOptional(); let resolve!: (value: unknown) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise((done) => { resolve = done; })));
    const work = querySchedulePreview(input); input.optional_places![0].name = "changed later"; input.duration_settings[1].minutes = 200;
    resolve({ ok: true, json: async () => data }); expect(await work).toEqual(data);
  });
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
    const input = request(); input.must_visit_places.push(place("second", 121.42)); input.duration_settings.push({ place_id: "second", minutes: 60, source: "default" });
    const data = response(input);
    data.edges.push(edge("out2", input.accommodation_place, input.must_visit_places[1]), edge("back2", input.must_visit_places[1], input.accommodation_place),
      { ...edge("unused", input.must_visit_places[0], input.must_visit_places[1]), used: false, status: "failed", duration_seconds: null, duration_minutes: null, distance_meters: null, message: "查询未完成。" });
    data.days[1] = { date: input.end_date, return_time: "10:04", items: [
      item("walk", "09:00", "09:02", 2, { from_place_id: "hotel", to_place_id: "second", edge_id: "out2" }),
      item("visit", "09:02", "10:02", 60, { place_id: "second", duration_source: "default" }),
      item("walk", "10:02", "10:04", 2, { from_place_id: "second", to_place_id: "hotel", edge_id: "back2" }),
    ] };
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

describe("schedule transit contract", () => {
  it("posts one selected mode and uses the total exactly once with unknown leg times, fare and incomplete geometry", async () => {
    const input = request(); const data = response(input); asTransit(input, data); mockResponse(data);
    expect(isScheduleRequest(input)).toBe(true); expect(await querySchedulePreview(input)).toEqual(data);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[0][1]?.body)).transport_mode).toBe("transit");
    expect(data.edges[0]).toMatchObject({ duration_seconds: 61.2, duration_minutes: 2, distance_meters: null,
      transit_route: { duration_seconds: 61.2, walking_distance_meters: 251, fare_cny: null, geometry_complete: false } });
    expect(data.days[0].return_time).toBe("10:04");
  });
  it("does not sum individual legs or apply walking-specific distance limits to transit", () => {
    const input = request(); const data = response(input); asTransit(input, data);
    const route = data.edges[0].transit_route!; route.legs[0].duration_seconds = 0; route.legs[1].duration_seconds = 3600;
    route.walking_distance_meters = 100_001; route.legs[1].distance_meters = 200_000;
    expect(isScheduleResponse(data, input)).toBe(true);
    expect(data.edges[0].duration_minutes).toBe(2);
  });
  it("preserves a same-place required visit with zero transit movement and no invented ride or fare", () => {
    const input = request(); input.must_visit_places = [input.accommodation_place]; input.duration_settings[0].place_id = "hotel";
    const data = response(input); data.edges = [{ ...edge("zero", input.accommodation_place, input.accommodation_place), status: "same_place", source: "same_place", duration_seconds: 0, duration_minutes: 0, distance_meters: 0 }];
    data.days[0].items = [item("walk", "09:00", "09:00", 0, { from_place_id: "hotel", to_place_id: "hotel", edge_id: "zero" }),
      item("visit", "09:00", "10:00", 60, { place_id: "hotel", duration_source: "default" }),
      item("walk", "10:00", "10:00", 0, { from_place_id: "hotel", to_place_id: "hotel", edge_id: "zero" })];
    data.days[0].return_time = "10:00"; asTransit(input, data);
    expect(isScheduleResponse(data, input)).toBe(true); expect(data.edges[0].transit_route).toBeNull();
    data.edges[0].transit_route = transitRoute(1); expect(isScheduleResponse(data, input)).toBe(false);
  });
  it("accepts transit optional tails without moving required visits, and preserves lunch exclusion", () => {
    const { input, data } = withOptional(); const required = structuredClone(data.days[0].items[1]); asTransit(input, data);
    expect(isScheduleResponse(data, input)).toBe(true); expect(data.days[0].items[1]).toEqual(required);
    data.request.lunch.start_time = input.lunch.start_time = "10:02";
    data.request.lunch.end_time = input.lunch.end_time = "10:03";
    expect(isScheduleResponse(data, input)).toBe(false);
  });
  it.each([
    ["no_route", "no_route"], ["unsupported", "route_unsupported"], ["timeout", "route_timeout"],
    ["data_error", "route_data_error"], ["failed", "route_failed"], ["budget_exhausted", "route_budget_exhausted"],
  ] as const)("keeps %s distinct with no summary or invented transport estimate", (status, reason) => {
    const input = request(); const data = response(input); asTransit(input, data);
    data.status = "unscheduled"; data.days = data.days.map((day) => ({ ...day, items: [], return_time: null }));
    data.unscheduled = [{ place_id: "required", reason, message: "本次参考路线不可用。" }];
    data.edges = [{ ...data.edges[0], status, used: false, duration_seconds: null, duration_minutes: null, distance_meters: null, transit_route: null, selection_rule: null, message: "本次查询状态。" }];
    expect(isScheduleResponse(data, input)).toBe(true);
    data.edges[0].transit_route = transitRoute(61.2); expect(isScheduleResponse(data, input)).toBe(false);
  });
  it("accepts unsupported optional attempts without recasting them as no-route or required failure", () => {
    const { input } = withOptional(); const data = response(input); asTransit(input, data);
    data.status = "partial";
    data.edges.push({ ...edge("unsupported", input.must_visit_places[0], input.optional_places![0]), transport_mode: "transit", status: "unsupported", used: false,
      duration_seconds: null, duration_minutes: null, distance_meters: null, message: "没有本阶段可展示的公共交通方案。" });
    data.edges.push({ ...data.edges.at(-1)!, id: "day-two-unsupported", origin: endpoint(input.accommodation_place) });
    data.optional_results = [{ place_id: "optional", scheduled_date: null, not_attempted_reason: null,
      attempts: data.days.map((day) => ({ date: day.date, outcome: "unsupported", message: "该日所需方案不受支持。" })) }];
    expect(isScheduleResponse(data, input)).toBe(true); expect(data.unscheduled).toEqual([]);
    data.edges.pop(); expect(isScheduleResponse(data, input)).toBe(false);
  });
  it.each([
    ["echoed other mode", (data: ScheduleResponse) => { data.request.transport_mode = "walking"; }],
    ["missing echoed mode", (data: ScheduleResponse) => { delete (data.request as ScheduleRequest).transport_mode; }],
    ["edge other mode", (data: ScheduleResponse) => { data.edges[0].transport_mode = "walking"; }],
    ["missing edge mode", (data: ScheduleResponse) => { Reflect.deleteProperty(data.edges[0], "transport_mode"); }],
    ["walk item in transit", (data: ScheduleResponse) => { data.days[0].items[0].kind = "walk"; }],
    ["walk return item in transit", (data: ScheduleResponse) => { data.days[0].items.at(-1)!.kind = "walk"; }],
    ["missing transit summary", (data: ScheduleResponse) => { data.edges[0].transit_route = null; }],
    ["missing selection rule", (data: ScheduleResponse) => { data.edges[0].selection_rule = null; }],
    ["invented selection rule", (data: ScheduleResponse) => { Object.assign(data.edges[0], { selection_rule: "fastest" }); }],
    ["walking distance as total distance", (data: ScheduleResponse) => { data.edges[0].distance_meters = 251; }],
    ["unknown distance as zero", (data: ScheduleResponse) => { data.edges[0].distance_meters = 0; }],
    ["double-counted duration", (data: ScheduleResponse) => { data.edges[0].duration_seconds! += 60; data.edges[0].duration_minutes! += 1; }],
    ["summary and edge duration differ", (data: ScheduleResponse) => { data.edges[0].transit_route!.duration_seconds = 60; }],
    ["infinite plan duration", (data: ScheduleResponse) => { data.edges[0].transit_route!.duration_seconds = Infinity; }],
    ["negative access distance", (data: ScheduleResponse) => { data.edges[0].transit_route!.walking_distance_meters = -1; }],
    ["missing access distance", (data: ScheduleResponse) => { Reflect.deleteProperty(data.edges[0].transit_route!, "walking_distance_meters"); }],
    ["string access distance", (data: ScheduleResponse) => { Object.assign(data.edges[0].transit_route!, { walking_distance_meters: "251" }); }],
    ["negative fare", (data: ScheduleResponse) => { data.edges[0].transit_route!.fare_cny = -1; }],
    ["NaN fare", (data: ScheduleResponse) => { data.edges[0].transit_route!.fare_cny = NaN; }],
    ["string fare", (data: ScheduleResponse) => { Object.assign(data.edges[0].transit_route!, { fare_cny: "2" }); }],
    ["missing fare", (data: ScheduleResponse) => { Reflect.deleteProperty(data.edges[0].transit_route!, "fare_cny"); }],
    ["negative leg time", (data: ScheduleResponse) => { data.edges[0].transit_route!.legs[1].duration_seconds = -1; }],
    ["missing ride line", (data: ScheduleResponse) => { data.edges[0].transit_route!.legs[1].line_name = null; }],
    ["blank boarding stop", (data: ScheduleResponse) => { data.edges[0].transit_route!.legs[1].departure_stop = " "; }],
    ["missing arrival stop", (data: ScheduleResponse) => { data.edges[0].transit_route!.legs[1].arrival_stop = null; }],
    ["unsupported rail leg", (data: ScheduleResponse) => { Object.assign(data.edges[0].transit_route!.legs[1], { mode: "railway" }); }],
    ["walking-only pretending transit", (data: ScheduleResponse) => { data.edges[0].transit_route!.legs.pop(); }],
    ["access distance without access steps", (data: ScheduleResponse) => { data.edges[0].transit_route!.legs.shift(); }],
    ["invented complete geometry", (data: ScheduleResponse) => { data.edges[0].transit_route!.geometry_complete = true; }],
    ["invalid geometry coordinates", (data: ScheduleResponse) => { data.edges[0].transit_route!.legs[1].geometry = [[[31, 121], [32, 122]]]; }],
    ["extra sensitive upstream field", (data: ScheduleResponse) => { Object.assign(data.edges[0].transit_route!, { upstream_url: "https://example.invalid" }); }],
    ["invented exact departure time", (data: ScheduleResponse) => { Object.assign(data.edges[0].transit_route!.legs[1], { departure_time: "09:01" }); }],
  ])("rejects %s", async (_, mutate) => {
    const input = request(); const data = response(input); asTransit(input, data); mutate(data); mockResponse(data);
    expect(isScheduleResponse(data, input)).toBe(false);
    await expect(querySchedulePreview(input)).rejects.toThrow("数据不完整或时间不一致");
  });
  it("walking responses cannot carry transit success, movement kind or unsupported status", () => {
    const input = request(); const data = response(input);
    data.edges[0].transit_route = transitRoute(61.2); expect(isScheduleResponse(data, input)).toBe(false);
    data.edges[0].transit_route = null; data.days[0].items[0].kind = "transit"; expect(isScheduleResponse(data, input)).toBe(false);
    data.days[0].items[0].kind = "walk"; data.edges[0].status = "unsupported"; expect(isScheduleResponse(data, input)).toBe(false);
  });
  it("never retries or switches to walking after a transit HTTP failure", async () => {
    const input = request(); input.transport_mode = "transit";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 504 }));
    await expect(querySchedulePreview(input)).rejects.toMatchObject({ kind: "timeout" });
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[0][1]?.body)).transport_mode).toBe("transit");
  });
});
