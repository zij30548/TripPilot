import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import TripPlanResult from "@/components/trip-plan-result";
import TripPlanner from "./trip-planner";
import type { AMapSDK } from "@/lib/amap-loader";
import { ScheduleError } from "@/lib/schedule-api";
import type { Place } from "@/types/place";
import type { CandidateResponse } from "@/types/candidates";
import type { OptionalScheduleResult, ScheduleEdge, ScheduleItem, ScheduleRequest, ScheduleResponse } from "@/types/schedule";
import type { TripPlan, TripRequest } from "@/types/trip";
import type { TransitRoute } from "@/types/transit";

const controls = vi.hoisted(() => ({ querySchedulePreview: vi.fn(), queryCandidates: vi.fn(), searchPlaces: vi.fn(), planTrip: vi.fn(), loadAMap: vi.fn(), getShanghaiCenter: vi.fn() }));
vi.mock("@/lib/schedule-api", async (original) => ({ ...await original<typeof import("@/lib/schedule-api")>(), querySchedulePreview: controls.querySchedulePreview }));
vi.mock("@/lib/candidates-api", async (original) => ({ ...await original<typeof import("@/lib/candidates-api")>(), queryCandidates: controls.queryCandidates }));
vi.mock("@/lib/places-api", () => ({ searchPlaces: controls.searchPlaces }));
vi.mock("@/lib/api", () => ({ planTrip: controls.planTrip }));
vi.mock("@/lib/amap-loader", () => ({ loadAMap: controls.loadAMap, getShanghaiCenter: controls.getShanghaiCenter }));

// Fictional snapshots, schedules and SDK only; no live POI or routing requests.
const lodging: Place = { id: "schedule-fixture-home", name: "测试住宿参考", address: "测试住宿地址", category: "测试地标", longitude: 120, latitude: 30, source: "amap" };
const first: Place = { ...lodging, id: "schedule-fixture-first", name: "测试必去甲", address: "测试甲地址", longitude: 120.01, latitude: 30.01 };
const second: Place = { ...lodging, id: "schedule-fixture-second", name: "测试必去乙", address: "测试乙地址", longitude: 120.02, latitude: 30.02 };
const optional: Place = { ...lodging, id: "schedule-fixture-optional", name: "测试可选地点", address: "测试可选地址", longitude: 120.03, latitude: 30.03 };
const moreOptional = [optional, ...Array.from({ length: 4 }, (_, index) => ({ ...optional, id: `schedule-fixture-optional-${index + 2}`, name: `测试可选地点${index + 2}`, address: `测试可选地址${index + 2}` }))];
const stamp = "2026-10-06T03:00:00Z";
const transitRoute = (): TransitRoute => ({ duration_seconds: 61.2, walking_distance_meters: 37, fare_cny: null, geometry_complete: false, legs: [
  { mode: "walking", distance_meters: 37, duration_seconds: 17, instruction: "沿测试街道前往车站", line_name: null, departure_stop: null, arrival_stop: null, geometry: [], geometry_complete: false },
  { mode: "bus", distance_meters: null, duration_seconds: null, instruction: null, line_name: "测试公交甲线", departure_stop: "测试起点站", arrival_stop: "测试终点站", geometry: [], geometry_complete: false },
] });

function tripRequest(days = 1): TripRequest {
  return {
    start_date: "2026-10-10", end_date: `2026-10-${9 + days}`, daily_start_time: "09:00", daily_end_time: "18:00",
    accommodation_location: lodging.name, accommodation_place: lodging, must_visit: [first.name, second.name], must_visit_places: [first, second],
    budget: 3000, travelers: 2, pace: "balanced", interests: ["摄影"], avoid_places: [],
  };
}
function makePlan(request = tripRequest()): TripPlan {
  return {
    destination: "上海", request, estimated_cost: 40, currency: "CNY", is_mock: true, notice: "仅为不可改写的 Mock 测试示例。",
    budget_breakdown: { transport: 0, food: 0, tickets: 40, other: 0 },
    // The old fixture deliberately remains two days regardless of requested dates.
    days: [1, 2].map((day) => ({
      day, date: `2026-10-${9 + day}`, title: `测试第${day}天`,
      activities: ["甲", "乙"].map((name, index) => ({ id: `activity-${index}`, name: `活动${name}`, category: "sightseeing" as const, estimated_cost: 10, description: "Mock 活动描述", start_time: "09:00", end_time: "10:00" })),
      transports: [], weather: { date: `2026-10-${9 + day}`, condition: "Mock 晴", min_temperature: 18, max_temperature: 25, rain_risk: 10 },
    })),
  };
}
const minute = (clock: string) => Number(clock.slice(0, 2)) * 60 + Number(clock.slice(3, 5));
const clock = (value: number) => `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
function makeSchedule(request: ScheduleRequest, count = request.must_visit_places.length, includeOptional = true): ScheduleResponse {
  const transportMode = request.transport_mode ?? "walking";
  const edges: ScheduleEdge[] = [], items: ScheduleItem[] = [];
  let time = minute(request.daily_start_time), origin = request.accommodation_place;
  function append(kind: ScheduleItem["kind"], duration: number, values: Partial<ScheduleItem> = {}) {
    items.push({ kind, start_time: clock(time), end_time: clock(time + duration), duration_minutes: duration, place_id: null, from_place_id: null, to_place_id: null, edge_id: null, duration_source: null, ...values });
    time += duration;
  }
  function available(duration: number) {
    if (request.lunch.enabled && time < minute(request.lunch.end_time) && time + duration > minute(request.lunch.start_time)) {
      if (time < minute(request.lunch.start_time)) append("wait", minute(request.lunch.start_time) - time);
      append("lunch", minute(request.lunch.end_time) - time);
    }
  }
  function walk(destination: Place) {
    const same = origin.id === destination.id;
    const edge: ScheduleEdge = { id: `edge-${edges.length}`, transport_mode: transportMode, transit_route: !same && transportMode === "transit" ? transitRoute() : null, selection_rule: !same && transportMode === "transit" ? "first_supported_complete" : null,
      origin: { place_id: origin.id, longitude: origin.longitude, latitude: origin.latitude }, destination: { place_id: destination.id, longitude: destination.longitude, latitude: destination.latitude }, status: same ? "same_place" : "ok", duration_seconds: same ? 0 : 61.2, duration_minutes: same ? 0 : 2, distance_meters: transportMode === "transit" ? null : same ? 0 : 80, source: same ? "same_place" : "amap", queried_at: stamp, message: null, used: true };
    available(edge.duration_minutes!); edges.push(edge); append(transportMode === "transit" ? "transit" : "walk", edge.duration_minutes!, { from_place_id: origin.id, to_place_id: destination.id, edge_id: edge.id }); origin = destination;
  }
  for (const place of request.must_visit_places.slice(0, count)) {
    walk(place);
    const stay = request.duration_settings.find((setting) => setting.place_id === place.id)!;
    available(stay.minutes); append("visit", stay.minutes, { place_id: place.id, duration_source: stay.source });
  }
  const selectedOptional = includeOptional && count === request.must_visit_places.length ? request.optional_places?.[0] : undefined;
  if (selectedOptional) {
    walk(selectedOptional);
    const stay = request.duration_settings.find((setting) => setting.place_id === selectedOptional.id)!;
    available(stay.minutes); append("visit", stay.minutes, { place_id: selectedOptional.id, duration_source: stay.source });
  }
  if (count > 0 || selectedOptional) {
    if (request.lunch.enabled && time <= minute(request.lunch.start_time)) {
      if (time < minute(request.lunch.start_time)) append("wait", minute(request.lunch.start_time) - time);
      append("lunch", minute(request.lunch.end_time) - time);
    }
    walk(request.accommodation_place);
  }
  const days = Math.round((Date.parse(request.end_date) - Date.parse(request.start_date)) / 86_400_000) + 1;
  return {
    status: count + (selectedOptional ? 1 : 0) === request.must_visit_places.length + (request.optional_places?.length ?? 0) ? "complete" : count === 0 && !selectedOptional ? "unscheduled" : "partial", generated_at: stamp, request: { ...request, transport_mode: transportMode }, edges,
    days: Array.from({ length: days }, (_, index) => ({ date: new Date(Date.parse(`${request.start_date}T00:00:00Z`) + index * 86_400_000).toISOString().slice(0, 10), items: index === 0 ? items : [], return_time: index === 0 && (count > 0 || selectedOptional) ? clock(time) : null })),
    unscheduled: request.must_visit_places.slice(count).map((place, index) => ({ place_id: place.id, reason: index === 0 ? "time_window" : "current_order_not_continued", message: index === 0 ? "当前地点在本次时间窗口内放不下。" : "本次固定顺序未继续尝试后续地点。" })),
    rules: ["保持确认顺序，不做最优排序。", "每条步行秒数分别向上取整为分钟。"],
    unknowns: ["营业时间与预约要求未知。", "预算与门票、餐费未知。", "午餐没有选择餐厅，餐厅绕路未计。"],
    optional_results: (request.optional_places ?? []).map((place) => ({ place_id: place.id, scheduled_date: place.id === selectedOptional?.id ? request.start_date : null,
      not_attempted_reason: count < request.must_visit_places.length ? "must_incomplete" : null,
      attempts: count < request.must_visit_places.length ? [] : Array.from({ length: place.id === selectedOptional?.id ? 1 : days }, (_, index) => ({ date: new Date(Date.parse(`${request.start_date}T00:00:00Z`) + index * 86_400_000).toISOString().slice(0, 10), outcome: place.id === selectedOptional?.id ? "scheduled" : selectedOptional && index === 0 ? "day_slot_used" : "time_window", message: place.id === selectedOptional?.id ? "已安排在本日尾部。" : selectedOptional && index === 0 ? "本日已安排一个可选地点。" : "该日期的时间窗口不能容纳本次试排。" })),
    })),
  };
}
function candidateResponse(places = [optional], status: CandidateResponse["status"] = "success"): CandidateResponse {
  return { status, queried_at: stamp, keywords: ["公园"], queries: [{ interest: "摄影", keyword: "公园", status: status === "failed" ? "failed" : "success", result_count: status === "failed" ? 0 : places.length, message: status === "failed" ? "本次候选检索失败。" : null }], candidates: [{ place: first, role: "must_visit", retrieval_sources: [] }, { place: second, role: "must_visit", retrieval_sources: [] }, ...(status === "failed" ? [] : places.map((place) => ({ place, role: "optional" as const, retrieval_sources: [{ interest: "摄影" as const, keyword: "公园" }] })))] };
}
function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (reason: Error) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

const maps: MockMap[] = [], markers: MockMarker[] = [], lines: MockPolyline[] = [];
class MockMap {
  destroyed = false; setFitView = vi.fn(); setZoomAndCenter = vi.fn(); destroy = vi.fn(() => { this.destroyed = true; });
  constructor() { maps.push(this); }
}
class MockMarker {
  attached = true; handlers = new Map<string, () => void>();
  on = vi.fn((event: string, handler: () => void) => { this.handlers.set(event, handler); }); off = vi.fn((event: string) => { this.handlers.delete(event); });
  setMap = vi.fn((map: MockMap | null) => { this.attached = map !== null; }); setzIndex = vi.fn();
  constructor(readonly options: { title: string; map: MockMap }) { markers.push(this); }
  click() { this.handlers.get("click")?.(); }
}
class MockPolyline {
  currentMap: MockMap | null = null; setMap = vi.fn((map: MockMap | null) => { this.currentMap = map; });
  constructor() { lines.push(this); }
}
let routeFetch: ReturnType<typeof vi.fn<typeof fetch>>;
beforeEach(() => {
  maps.length = 0; markers.length = 0; lines.length = 0;
  controls.querySchedulePreview.mockReset().mockImplementation(async (request: ScheduleRequest) => makeSchedule(request));
  controls.queryCandidates.mockReset().mockResolvedValue(candidateResponse());
  controls.searchPlaces.mockReset().mockResolvedValue([lodging, first, second]);
  controls.planTrip.mockReset().mockImplementation(async (request: TripRequest) => makePlan(request));
  controls.loadAMap.mockReset().mockResolvedValue({ Map: MockMap, Marker: MockMarker, Polyline: MockPolyline } as unknown as AMapSDK);
  controls.getShanghaiCenter.mockReset().mockResolvedValue([120, 30]);
  routeFetch = vi.fn<typeof fetch>().mockImplementation(async () => new Response(JSON.stringify({ status: "ok", source: "amap", queried_at: stamp, route: { distance_meters: 500, duration_seconds: 400, segments: [[[120.01, 30.01], [120.02, 30.02]]] } })));
  vi.stubGlobal("fetch", routeFetch);
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => { callback(0); return 1; });
});

function preview() { return screen.getByRole("region", { name: "行程草案" }); }
function click(name: string | RegExp) { fireEvent.click(screen.getByRole("button", { name })); }
function generate() { fireEvent.click(within(preview()).getByRole("button", { name: "生成行程草案" })); }
function mode(value: "walking" | "transit") { fireEvent.click(within(within(preview()).getByRole("group", { name: "草案交通方式" })).getByRole("button", { name: value === "walking" ? "步行" : "公交／地铁" })); }
function stay(place = first) { return within(preview()).getByRole("spinbutton", { name: `停留分钟：${place.name}` }) as HTMLInputElement; }
function optionalStay(place = optional) { return within(preview()).getByRole("spinbutton", { name: `可选停留分钟：${place.name}` }) as HTMLInputElement; }
async function fetchCandidates() {
  click(/^(?:重新)?获取候选地点$/);
  await waitFor(() => expect((within(preview()).getByRole("button", { name: "生成行程草案" }) as HTMLButtonElement).disabled).toBe(false));
}
async function generated() {
  generate(); await within(preview()).findByText("已安排本次必去地点");
}
function search() {
  fireEvent.change(screen.getByRole("textbox", { name: "搜索上海地点" }), { target: { value: "测试地点" } }); click("搜索");
}
async function bind(name: string, place: Place) {
  click(`为${name}绑定地点`); search();
  fireEvent.click(within(await screen.findByRole("list", { name: "地点搜索结果" })).getByRole("button", { name: new RegExp(place.name) })); click("确认绑定");
}
function submitTrip() { fireEvent.submit(screen.getByRole("button", { name: "生成我的行程" }).closest("form")!); }
async function plannerResult() {
  render(<TripPlanner><p>测试旅行说明</p></TripPlanner>);
  for (const [label, value] of [[/开始日期/, "2026-10-10"], [/结束日期/, "2026-10-10"], [/总预算/, "3000"]] as const) fireEvent.change(screen.getByLabelText(label), { target: { value } });
  click("选择住宿参考点"); search();
  fireEvent.click(await within(screen.getByRole("region", { name: "住宿参考点选择器" })).findByRole("button", { name: new RegExp(lodging.name) })); click("确认住宿参考点");
  for (const place of [first, second]) {
    click("添加必去地点"); search();
    fireEvent.click(await within(screen.getByRole("region", { name: "必去地点选择器" })).findByRole("button", { name: new RegExp(place.name) })); click("确认必去地点");
  }
  submitTrip(); await screen.findByRole("button", { name: "修改旅行需求" });
}

describe("independent must-visit walking schedule preview", () => {
  it("switches only the draft mode, clears previous output and waits for an explicit query while retaining planning inputs", async () => {
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />); await fetchCandidates();
    fireEvent.change(optionalStay(), { target: { value: "90" } }); await generated();
    mode("transit");
    expect(within(preview()).queryByRole("list", { name: "草案时间线" })).toBeNull();
    expect(optionalStay().value).toBe("90"); expect(stay().value).toBe("60");
    expect((within(preview()).getByLabelText("午餐开始时间") as HTMLInputElement).value).toBe("12:00");
    expect(controls.querySchedulePreview).toHaveBeenCalledTimes(1); expect(controls.queryCandidates).toHaveBeenCalledTimes(1); expect(routeFetch).not.toHaveBeenCalled();
    await generated(); expect(controls.querySchedulePreview.mock.calls[1][0].transport_mode).toBe("transit");
    expect(preview().textContent).toContain("公交／地铁参考");
    mode("walking"); expect(within(preview()).queryByRole("list", { name: "草案时间线" })).toBeNull();
    expect(controls.querySchedulePreview).toHaveBeenCalledTimes(2); await generated();
    expect(controls.querySchedulePreview.mock.calls[2][0].transport_mode).toBe("walking");
    expect(optionalStay().value).toBe("90"); expect(controls.queryCandidates).toHaveBeenCalledTimes(1);
  });
  it("shows whole transit totals once, ordered true line/stops and unknown leg/fare without fabricating precise clocks or total distance", async () => {
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />); mode("transit"); await generated();
    const timeline = within(preview()).getByRole("list", { name: "草案时间线" }); const move = within(timeline).getAllByRole("listitem")[0];
    expect(move.textContent).toContain("09:00—09:02 · 公交／地铁参考交通");
    expect(move.textContent).toContain("公交方案总预计 61.2 秒 → 排程计入 2 分钟");
    expect(move.textContent).toContain("不再累加分段时长"); expect(move.textContent).toContain("接驳步行距离：37 米（不是公交总里程）");
    expect(move.textContent).toContain("人民币参考票价：未知"); expect(move.textContent).not.toContain("¥0.00");
    const detail = move.querySelector("details")!; fireEvent.click(detail.querySelector("summary")!);
    expect(detail.textContent).toContain("测试公交甲线"); expect(detail.textContent).toContain("上车：测试起点站 → 下车：测试终点站");
    expect(detail.textContent).toContain("该步预计耗时：未知"); expect(detail.textContent).toContain("该步距离：未知");
    expect(detail.textContent).toContain("不是精确发车、到站或换乘时刻"); expect(detail.textContent).toContain("不代表最快或最优");
    expect(detail.textContent).toContain("部分地图几何未提供"); expect(move.textContent).toContain("采用本次查询的参考方案估时，尚未验证旅行日期及草案出发时刻的运营班次、等车和换乘可行性。");
    expect(preview().textContent).toContain("预计返回住宿参考点：13:02"); expect(routeFetch).not.toHaveBeenCalled();
  });
  it("renders known transit fares as a reference, without multiplying travelers or putting them in Mock budget", async () => {
    controls.querySchedulePreview.mockImplementationOnce(async (request: ScheduleRequest) => {
      const result = makeSchedule(request); result.edges.forEach((edge) => { if (edge.transit_route) edge.transit_route.fare_cny = 4; }); return result;
    });
    const plan = makePlan(), original = JSON.stringify(plan); render(<TripPlanResult plan={plan} onEdit={vi.fn()} />); mode("transit"); await generated();
    const timeline = within(preview()).getByRole("list", { name: "草案时间线" });
    expect(timeline.textContent).toContain("人民币参考票价：¥4.00"); expect(timeline.textContent).not.toContain("¥8.00");
    expect(timeline.textContent).toContain("不乘旅行人数，不计入预算"); expect(JSON.stringify(plan)).toBe(original);
  });
  it("same-place transit retains the visit and explicitly avoids invented lines, fare and upstream estimates", async () => {
    const request = { ...tripRequest(), must_visit_places: [lodging], must_visit: [lodging.name] };
    render(<TripPlanResult plan={makePlan(request)} onEdit={vi.fn()} />); mode("transit"); await generated();
    const timeline = within(preview()).getByRole("list", { name: "草案时间线" });
    expect(timeline.textContent).toContain("无需交通查询；计入 0 分钟"); expect(timeline.textContent).toContain("停留");
    expect(timeline.textContent).not.toContain("测试公交甲线"); expect(timeline.textContent).not.toContain("人民币参考票价");
    expect(timeline.textContent).toContain("无高德请求"); expect(routeFetch).not.toHaveBeenCalled();
  });
  it.each(["success", "error"] as const)("mode ABA isolates a late %s in the actual panel", async (outcome) => {
    const old = deferred<ScheduleResponse>(); controls.querySchedulePreview.mockReturnValueOnce(old.promise);
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />); generate(); const body = controls.querySchedulePreview.mock.calls[0][0] as ScheduleRequest;
    mode("transit"); mode("walking"); await generated();
    await act(async () => { if (outcome === "success") old.resolve(makeSchedule(body, 0)); else old.reject(new ScheduleError("error", "过期模式错误")); });
    expect(within(preview()).getByText("已安排本次必去地点")).toBeTruthy(); expect(preview().textContent).not.toContain("过期模式错误");
    expect(controls.querySchedulePreview).toHaveBeenCalledTimes(2);
  });
  it("draft transport mode never changes the old map's selected mode, existing polyline, binding or Marker behavior", async () => {
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await bind("活动甲", first); await bind("活动乙", second); click("查询步行路线");
    await waitFor(() => expect(lines.some((line) => line.currentMap)).toBe(true)); const originalLine = lines.find((line) => line.currentMap)!;
    const mapModes = screen.getByRole("group", { name: "选择真实交通方式" });
    mode("transit"); await generated();
    expect(within(mapModes).getByRole("button", { name: "步行" }).getAttribute("aria-pressed")).toBe("true");
    expect(originalLine.currentMap).not.toBeNull(); expect(routeFetch).toHaveBeenCalledTimes(1);
    const marker = markers.findLast((item) => item.attached && item.options.title === first.name)!; act(() => marker.click()); click("在地图查看活动乙");
    const priorTimeline = within(preview()).getByRole("list", { name: "草案时间线" }).textContent;
    fireEvent.click(within(mapModes).getByRole("button", { name: "公交／地铁" }));
    expect(originalLine.currentMap).toBeNull(); expect(within(preview()).getByRole("list", { name: "草案时间线" }).textContent).toBe(priorTimeline);
    expect(within(within(preview()).getByRole("group", { name: "草案交通方式" })).getByRole("button", { name: "公交／地铁" }).getAttribute("aria-pressed")).toBe("true");
    mode("walking"); expect(within(mapModes).getByRole("button", { name: "公交／地铁" }).getAttribute("aria-pressed")).toBe("true");
    expect(within(screen.getByRole("article", { name: "活动甲" })).getByText(first.name)).toBeTruthy();
    expect(controls.querySchedulePreview).toHaveBeenCalledTimes(1); expect(routeFetch).toHaveBeenCalledTimes(1);
  });
  it("initializes editable defaults from confirmed requirements and never auto-generates", async () => {
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    expect(stay().value).toBe("60"); expect(stay(second).value).toBe("60");
    expect((within(preview()).getByRole("checkbox", { name: "安排午餐时间" }) as HTMLInputElement).checked).toBe(true);
    expect((within(preview()).getByLabelText("午餐开始时间") as HTMLInputElement).value).toBe("12:00");
    expect((within(preview()).getByLabelText("午餐结束时间") as HTMLInputElement).value).toBe("13:00");
    expect(preview().textContent).toMatch(/默认规划设置，可修改/);
    expect(preview().textContent).toMatch(/不是高德/);
    expect(screen.getByRole("region", { name: "旧 Mock 行程示例" }).contains(screen.getByText("预计总花费 · Mock"))).toBe(true);
    expect(within(preview()).queryByText("预计总花费 · Mock")).toBeNull();
    await act(async () => undefined);
    expect(controls.querySchedulePreview).not.toHaveBeenCalled(); expect(controls.queryCandidates).not.toHaveBeenCalled(); expect(routeFetch).not.toHaveBeenCalled();
  });

  it.each(["no-accommodation", "no-must", "over-six"] as const)("shows a scope/input restriction for %s rather than claiming the trip is infeasible", (kind) => {
    const request = tripRequest();
    if (kind === "no-accommodation") request.accommodation_place = null;
    if (kind === "no-must") { request.must_visit_places = []; request.must_visit = []; }
    if (kind === "over-six") { request.must_visit_places = Array.from({ length: 7 }, (_, index) => ({ ...first, id: `fixture-${index}`, name: `测试必去${index}` })); request.must_visit = request.must_visit_places.map((place) => place.name); }
    render(<TripPlanResult plan={makePlan(request)} onEdit={vi.fn()} />);
    expect((within(preview()).getByRole("button", { name: "生成行程草案" }) as HTMLButtonElement).disabled).toBe(true);
    expect(within(preview()).getByRole("alert").textContent).toMatch(kind === "no-accommodation" ? /住宿/ : /必去|可选|1.*6|6.*个/);
    expect(within(preview()).getByRole("alert").textContent).not.toMatch(/旅行不可行|无法完成旅行/); expect(controls.querySchedulePreview).not.toHaveBeenCalled();
  });

  it.each([1, 3])("uses %s requested dates, not the fixed two-day Mock example, with independent date selection", async (days) => {
    const plan = makePlan(tripRequest(days)), original = JSON.stringify(plan);
    render(<TripPlanResult plan={plan} onEdit={vi.fn()} />); await generated();
    const tabs = within(within(preview()).getByRole("group", { name: "选择草案日期" })).getAllByRole("button");
    expect(tabs).toHaveLength(days);
    expect(controls.querySchedulePreview.mock.calls[0][0]).toMatchObject({ start_date: plan.request.start_date, end_date: plan.request.end_date });
    expect(within(screen.getByLabelText("选择行程日期")).getAllByRole("button")).toHaveLength(2);
    if (days === 3) {
      fireEvent.click(tabs[2]);
      expect(within(preview()).getByText(/尚未安排景点/)).toBeTruthy();
      expect(within(preview()).queryByRole("list", { name: "草案时间线" })).toBeNull();
      expect(screen.getByRole("button", { name: /Day 1/ }).getAttribute("aria-pressed")).toBe("true");
      click(/Day 2/); expect(tabs[2].getAttribute("aria-pressed")).toBe("true");
    }
    expect(controls.querySchedulePreview).toHaveBeenCalledTimes(1); expect(JSON.stringify(plan)).toBe(original);
  });

  it("renders walk/visit/wait/lunch, a return time, per-edge seconds/ceil minutes, source and explicit unknowns", async () => {
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />); await generated();
    const timeline = within(preview()).getByRole("list", { name: "草案时间线" });
    expect(timeline.textContent).toContain(`${lodging.name} → ${first.name}`);
    expect(timeline.textContent).toContain(`${second.name} → ${lodging.name}`);
    expect(timeline.textContent).toMatch(/步行/); expect(timeline.textContent).toMatch(/停留/);
    expect(timeline.textContent).toMatch(/等待/); expect(timeline.textContent).toMatch(/午餐预留/);
    expect(timeline.textContent).toMatch(/61\.2 秒.*2 分钟.*向上取整/);
    expect(within(timeline).getAllByRole("time")[0].getAttribute("datetime")).toBe(stamp);
    expect(preview().textContent).toContain("预计返回住宿参考点：13:02");
    expect(preview().textContent).toMatch(/营业.*预约.*未知/);
    expect(preview().textContent).toMatch(/门票.*餐费.*未知/);
    expect(preview().textContent).toMatch(/预算尚未核实/);
    expect(preview().textContent).toMatch(/绕行尚未计入/);
    expect(within(preview()).queryByText(/¥40|Mock 晴/)).toBeNull();
  });

  it("changing a duration immediately clears the old result and sends a user-source setting only on another click", async () => {
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />); await generated();
    fireEvent.change(stay(), { target: { value: "90" } });
    expect(within(preview()).queryByRole("list", { name: "草案时间线" })).toBeNull();
    expect(preview().textContent).toMatch(/你修改的规划设置/);
    expect(controls.querySchedulePreview).toHaveBeenCalledTimes(1); await generated();
    expect(controls.querySchedulePreview.mock.calls[1][0].duration_settings).toEqual([{ place_id: first.id, minutes: 90, source: "user" }, { place_id: second.id, minutes: 60, source: "default" }]);
    expect(within(preview()).getByRole("list", { name: "草案时间线" }).textContent).toMatch(/停留来源：你修改的规划设置/);
  });

  it.each(["", "14", "481", "60.5"])("blocks an invalid duration %s and never treats stale settings as current", async (value) => {
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />); await generated();
    fireEvent.change(stay(), { target: { value } });
    expect((within(preview()).getByRole("button", { name: "生成行程草案" }) as HTMLButtonElement).disabled).toBe(true);
    expect(within(preview()).getByRole("alert").textContent).toMatch(/15|480|分钟|整数/);
    expect(within(preview()).queryByRole("list", { name: "草案时间线" })).toBeNull(); expect(controls.querySchedulePreview).toHaveBeenCalledTimes(1);
  });

  it("validates the whole lunch interval inside the daily window and permits disabling lunch", async () => {
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />); await generated();
    fireEvent.change(within(preview()).getByLabelText("午餐开始时间"), { target: { value: "08:00" } });
    expect(within(preview()).queryByRole("list", { name: "草案时间线" })).toBeNull();
    expect((within(preview()).getByRole("button", { name: "生成行程草案" }) as HTMLButtonElement).disabled).toBe(true);
    expect(within(preview()).getByRole("alert").textContent).toMatch(/午餐|窗口/);
    fireEvent.click(within(preview()).getByRole("checkbox", { name: "安排午餐时间" })); await generated();
    expect(controls.querySchedulePreview.mock.calls[1][0].lunch.enabled).toBe(false);
    expect(within(preview()).getByRole("list", { name: "草案时间线" }).textContent).not.toContain("午餐预留");
    expect((within(preview()).getByLabelText("午餐开始时间") as HTMLInputElement).disabled).toBe(true);
  });

  it("reports current-order unscheduled reasons without pronouncing later places or the whole trip impossible", async () => {
    controls.querySchedulePreview.mockImplementationOnce(async (request: ScheduleRequest) => makeSchedule(request, 0));
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />); generate();
    const area = await within(preview()).findByRole("region", { name: "未安排的必去地点" });
    expect(area.textContent).toContain(first.name); expect(area.textContent).toContain(second.name);
    expect(area.textContent).toMatch(/当前地点.*放不下/); expect(area.textContent).toMatch(/固定顺序未继续/);
    expect(area.textContent).toMatch(/不代表.*不可行/);
    expect(within(preview()).getByText(/尚未安排景点/)).toBeTruthy();
    expect(within(preview()).queryByRole("list", { name: "草案时间线" })).toBeNull();
  });

  it.each(["error", "timeout"] as const)("shows a safe %s and does not restore an earlier successful preview", async (kind) => {
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />); await generated();
    controls.querySchedulePreview.mockRejectedValueOnce(new ScheduleError(kind, kind === "timeout" ? "步行草案查询超时，请重试。" : "步行草案服务暂时不可用，请重试。")); generate();
    expect((await within(preview()).findByRole("alert")).textContent).toMatch(/超时|不可用/);
    expect(within(preview()).queryByRole("list", { name: "草案时间线" })).toBeNull();
  });

  it("invalidates only the preview on candidate changes while preserving existing binding and route geometry", async () => {
    const plan = makePlan(), original = JSON.stringify(plan);
    render(<TripPlanResult plan={plan} onEdit={vi.fn()} />);
    click("获取候选地点"); await screen.findByRole("button", { name: `排除可选地点：${optional.name}` }); click(`排除可选地点：${optional.name}`);
    await bind("活动甲", first); await bind("活动乙", second); click("查询步行路线");
    await screen.findByText(/来源：高德步行路线/); await waitFor(() => expect(lines.some((line) => line.currentMap)).toBe(true));
    const oldLine = lines.find((line) => line.currentMap)!; await generated();
    expect(oldLine.currentMap).not.toBeNull(); expect(screen.getByRole("button", { name: `恢复可选地点：${optional.name}` })).toBeTruthy();
    fireEvent.change(stay(), { target: { value: "90" } }); expect(oldLine.currentMap).not.toBeNull();
    expect(within(screen.getByRole("article", { name: "活动乙" })).getByText(second.name)).toBeTruthy();
    await generated(); click(`恢复可选地点：${optional.name}`);
    expect(within(preview()).queryByRole("list", { name: "草案时间线" })).toBeNull();
    expect(oldLine.currentMap).not.toBeNull();
    click("重新获取候选地点");
    await waitFor(() => expect(controls.queryCandidates).toHaveBeenCalledTimes(2));
    await generated();
    const marker = markers.findLast((item) => item.attached && item.options.title === first.name)!; act(() => marker.click());
    click("在地图查看活动乙");
    fireEvent.click(within(screen.getByRole("group", { name: "选择真实交通方式" })).getByRole("button", { name: "公交／地铁" }));
    click(/Day 2/); click(/Day 1/);
    expect(within(preview()).getByRole("list", { name: "草案时间线" })).toBeTruthy();
    expect(controls.querySchedulePreview).toHaveBeenCalledTimes(3); expect(routeFetch).toHaveBeenCalledTimes(1); expect(JSON.stringify(plan)).toBe(original);
  });

  it("edit/regeneration preserves 5A confirmations but clears the draft and restores default planning settings", async () => {
    await plannerResult(); fireEvent.change(stay(), { target: { value: "90" } });
    fireEvent.click(within(preview()).getByRole("checkbox", { name: "安排午餐时间" })); mode("transit"); await generated(); click("修改旅行需求");
    expect(within(screen.getByRole("region", { name: "已确认住宿参考点" })).getByText(lodging.name)).toBeTruthy();
    expect(within(screen.getByRole("region", { name: "已确认必去地点" })).getByText(first.name)).toBeTruthy();
    submitTrip(); await screen.findByRole("button", { name: "修改旅行需求" });
    expect(stay().value).toBe("60"); expect((within(preview()).getByRole("checkbox", { name: "安排午餐时间" }) as HTMLInputElement).checked).toBe(true);
    expect(within(within(preview()).getByRole("group", { name: "草案交通方式" })).getByRole("button", { name: "步行" }).getAttribute("aria-pressed")).toBe("true");
    expect(within(preview()).queryByRole("list", { name: "草案时间线" })).toBeNull(); expect(controls.querySchedulePreview).toHaveBeenCalledTimes(1);
  });

  it.each(["success", "error"] as const)("rejects a late %s after settings A → B → A and a newer generation", async (ending) => {
    const pending = deferred<ScheduleResponse>(); controls.querySchedulePreview.mockReturnValueOnce(pending.promise);
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />); generate();
    const oldRequest = controls.querySchedulePreview.mock.calls[0][0] as ScheduleRequest;
    const signal = controls.querySchedulePreview.mock.calls[0][1] as AbortSignal;
    fireEvent.change(stay(), { target: { value: "90" } }); fireEvent.change(stay(), { target: { value: "60" } });
    expect(signal.aborted).toBe(true); await generated();
    await act(async () => {
      if (ending === "success") pending.resolve(makeSchedule(oldRequest, 0)); else pending.reject(new ScheduleError("error", "测试过期错误不应显示"));
    });
    expect(within(preview()).getByText("已安排本次必去地点")).toBeTruthy();
    expect(within(preview()).queryByRole("region", { name: "未安排的必去地点" })).toBeNull(); expect(preview().textContent).not.toContain("测试过期错误");
  });

  it("explicitly generates only the first three active optional candidates and shows untried count as a scope limit", async () => {
    controls.queryCandidates.mockResolvedValueOnce(candidateResponse(moreOptional));
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />); await fetchCandidates();
    const scope = within(preview()).getByRole("region", { name: "本次可选试排范围" });
    expect(within(scope).getByRole("list", { name: "本次试排可选地点" }).textContent).toContain(moreOptional[2].name);
    expect(scope.textContent).toContain("未纳入本轮试排：2 个");
    expect(scope.textContent).toContain("范围限制，不代表这些地点放不下");
    expect(within(preview()).getByLabelText("本次生成范围").textContent).toContain(moreOptional[2].name);
    expect(optionalStay().value).toBe("60"); expect(stay().value).toBe("60");
    expect(controls.querySchedulePreview).not.toHaveBeenCalled();
    await generated();
    expect(controls.querySchedulePreview.mock.calls[0][0].optional_places).toEqual(moreOptional.slice(0, 3));
    click(`排除可选地点：${optional.name}`);
    expect(within(preview()).queryByRole("list", { name: "草案时间线" })).toBeNull();
    expect(within(preview()).queryByRole("spinbutton", { name: `可选停留分钟：${optional.name}` })).toBeNull();
    expect(optionalStay(moreOptional[3]).value).toBe("60");
    expect(controls.querySchedulePreview).toHaveBeenCalledTimes(1); expect(controls.queryCandidates).toHaveBeenCalledTimes(1);
    await generated(); expect(controls.querySchedulePreview.mock.calls[1][0].optional_places).toEqual(moreOptional.slice(1, 4));
    click(`恢复可选地点：${optional.name}`);
    expect(optionalStay().value).toBe("60"); expect(within(preview()).queryByRole("list", { name: "草案时间线" })).toBeNull();
  });

  it("edits optional stay settings separately, labels their visit role and preserves required durations", async () => {
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />); await fetchCandidates();
    fireEvent.change(optionalStay(), { target: { value: "90" } });
    expect(stay().value).toBe("60"); await generated();
    expect(controls.querySchedulePreview.mock.calls[0][0].duration_settings).toEqual([
      { place_id: first.id, minutes: 60, source: "default" }, { place_id: second.id, minutes: 60, source: "default" }, { place_id: optional.id, minutes: 90, source: "user" },
    ]);
    const timeline = within(preview()).getByRole("list", { name: "草案时间线" });
    const visit = within(timeline).getByText(optional.name).closest("li")!;
    expect(visit.textContent).toContain("可选地点"); expect(visit.textContent).toContain("90 分钟"); expect(visit.textContent).toContain("你修改的规划设置");
    expect(preview().textContent).toContain("必去已安排 2/2 个 · 可选已安排 1/1 个");
    fireEvent.change(optionalStay(), { target: { value: "14" } });
    expect((within(preview()).getByRole("button", { name: "生成行程草案" }) as HTMLButtonElement).disabled).toBe(true);
    expect(within(preview()).queryByRole("list", { name: "草案时间线" })).toBeNull();
    expect(within(preview()).getByRole("alert").textContent).toMatch(/15.*480/);
  });

  it("clears a generated preview synchronously on candidate refresh and disables generation until retrieval settles", async () => {
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />); await fetchCandidates(); await generated();
    const pending = deferred<CandidateResponse>(); controls.queryCandidates.mockReturnValueOnce(pending.promise);
    click("重新获取候选地点");
    expect(within(preview()).queryByRole("list", { name: "草案时间线" })).toBeNull();
    expect((within(preview()).getByRole("button", { name: "生成行程草案" }) as HTMLButtonElement).disabled).toBe(true);
    expect(preview().textContent).toMatch(/候选正在更新/); expect(preview().textContent).not.toContain("最近检索失败");
    generate(); expect(controls.querySchedulePreview).toHaveBeenCalledTimes(1);
    await act(async () => { pending.resolve(candidateResponse([moreOptional[1]])); });
    expect(optionalStay(moreOptional[1]).value).toBe("60");
    expect(within(preview()).queryByRole("list", { name: "草案时间线" })).toBeNull();
    expect((within(preview()).getByRole("button", { name: "生成行程草案" }) as HTMLButtonElement).disabled).toBe(false);
    expect(controls.querySchedulePreview).toHaveBeenCalledTimes(1);
  });

  it.each(["success", "error"] as const)("rejects a late preview %s after candidate refresh has started", async (outcome) => {
    const old = deferred<ScheduleResponse>(); controls.querySchedulePreview.mockReturnValueOnce(old.promise);
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />); await fetchCandidates(); generate();
    const oldRequest = controls.querySchedulePreview.mock.calls[0][0] as ScheduleRequest;
    const signal = controls.querySchedulePreview.mock.calls[0][1] as AbortSignal;
    const pending = deferred<CandidateResponse>(); controls.queryCandidates.mockReturnValueOnce(pending.promise); click("重新获取候选地点");
    expect(signal.aborted).toBe(true);
    await act(async () => { if (outcome === "success") old.resolve(makeSchedule(oldRequest)); else old.reject(new ScheduleError("error", "过期草案错误")); });
    expect(within(preview()).queryByRole("list", { name: "草案时间线" })).toBeNull(); expect(preview().textContent).not.toContain("过期草案错误");
    await act(async () => { pending.resolve(candidateResponse([moreOptional[1]])); }); await generated();
    expect(controls.querySchedulePreview.mock.calls[1][0].optional_places).toEqual([moreOptional[1]]);
  });

  it("marks partial candidates with their timestamp and deliberately permits an older retained pool after refresh failure", async () => {
    controls.queryCandidates.mockResolvedValueOnce({ ...candidateResponse([optional], "partial"), keywords: ["公园", "餐厅"], queries: [...candidateResponse().queries, { interest: "美食", keyword: "餐厅", status: "timeout", result_count: 0, message: "餐厅检索超时。" }] });
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />); await fetchCandidates();
    expect(preview().textContent).toContain("候选检索仅部分成功");
    expect(within(within(preview()).getByRole("region", { name: "本次可选试排范围" })).getByRole("time").getAttribute("datetime")).toBe(stamp);
    await generated(); controls.queryCandidates.mockResolvedValueOnce(candidateResponse([], "failed")); await fetchCandidates();
    expect(preview().textContent).toContain("最近检索失败，保留的是上次有效候选");
    expect(optionalStay().value).toBe("60"); expect(within(preview()).queryByRole("list", { name: "草案时间线" })).toBeNull();
    await generated(); expect(controls.querySchedulePreview.mock.calls[1][0].optional_places).toEqual([optional]);
  });

  it.each(["time_window", "no_route", "unsupported", "timeout", "data_error", "failed", "budget_exhausted", "day_slot_used"] as const)("renders optional %s per date without deleting the successful required prefix", async (outcome) => {
    controls.querySchedulePreview.mockImplementationOnce(async (request: ScheduleRequest) => {
      const result = makeSchedule(request, request.must_visit_places.length, false);
      result.optional_results[0].attempts[0].outcome = outcome;
      result.optional_results[0].attempts[0].message = "只记录该日期的试排结果。";
      return result;
    });
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />); await fetchCandidates(); await generated();
    const results = within(preview()).getByRole("region", { name: "可选地点试排结果" });
    expect(results.textContent).toContain("2026-10-10"); expect(results.textContent).toContain("只记录该日期");
    expect(results.textContent).toContain("某天未安排不代表其他日期也不可行");
    expect(preview().textContent).toContain("必去已安排 2/2 个 · 可选已安排 0/1 个");
    const timeline = within(preview()).getByRole("list", { name: "草案时间线" });
    expect(within(timeline).getByText(first.name)).toBeTruthy(); expect(within(timeline).getByText(second.name)).toBeTruthy();
    expect(preview().textContent).toContain("预计返回住宿参考点：13:02");
    expect(within(preview()).queryByRole("region", { name: "未安排的必去地点" })).toBeNull();
  });

  it("explains that optional places were not attempted when required scheduling is incomplete", async () => {
    controls.querySchedulePreview.mockImplementationOnce(async (request: ScheduleRequest) => makeSchedule(request, 1));
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />); await fetchCandidates(); generate();
    const results = await within(preview()).findByRole("region", { name: "可选地点试排结果" });
    expect(results.textContent).toContain("必去地点尚未全部安排，本次未尝试此可选地点");
    expect(results.textContent).toContain("不代表该地点不可行");
    expect(within(preview()).getByRole("region", { name: "未安排的必去地点" }).textContent).toContain(second.name);
    expect(preview().textContent).toContain("必去已安排 1/2 个 · 可选已安排 0/1 个");
  });

  it("retains the exact required timeline and return when optional routing fails, with no invented budget-exhausted query", async () => {
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />); await generated();
    const before = within(preview()).getByRole("list", { name: "草案时间线" }).textContent;
    await fetchCandidates();
    controls.querySchedulePreview.mockImplementationOnce(async (request: ScheduleRequest) => {
      const response = makeSchedule(request, request.must_visit_places.length, false);
      response.optional_results[0].attempts[0].outcome = "budget_exhausted";
      response.optional_results[0].attempts[0].message = "本轮搜索截止前未开始查询。";
      response.edges.push({ id: "optional-unqueried-edge", transport_mode: request.transport_mode ?? "walking", transit_route: null, selection_rule: null, origin: { place_id: second.id, longitude: second.longitude, latitude: second.latitude }, destination: { place_id: optional.id, longitude: optional.longitude, latitude: optional.latitude }, status: "budget_exhausted", source: "amap", queried_at: stamp, duration_seconds: null, duration_minutes: null, distance_meters: null, used: false, message: "本次搜索截止前未开始该路段查询。" });
      return response;
    });
    await generated();
    expect(within(preview()).getByRole("list", { name: "草案时间线" }).textContent).toBe(before);
    expect(preview().textContent).toContain("预计返回住宿参考点：13:02");
    const record = within(preview()).getByText(/本次查询截止前未开始：/).closest("li")!;
    expect(record.textContent).toContain("未发起高德查询、未取得估时");
    expect(record.textContent).toContain("记录时间"); expect(record.textContent).not.toContain("来源：高德步行路线");
    expect(record.textContent).not.toMatch(/0 秒|0 分钟/);
  });

  it("supports an optional-only draft without claiming all required places are complete", async () => {
    const request = { ...tripRequest(), must_visit_places: [], must_visit: [] };
    controls.queryCandidates.mockResolvedValueOnce({ ...candidateResponse(), candidates: candidateResponse().candidates.filter((candidate) => candidate.role === "optional") });
    render(<TripPlanResult plan={makePlan(request)} onEdit={vi.fn()} />);
    expect((within(preview()).getByRole("button", { name: "生成行程草案" }) as HTMLButtonElement).disabled).toBe(true);
    await fetchCandidates(); generate(); await within(preview()).findByText("本次草案已安排可选地点");
    expect(within(preview()).queryByText("已安排本次必去地点")).toBeNull();
    expect(preview().textContent).toContain("必去已安排 0/0 个 · 可选已安排 1/1 个");
    expect(controls.querySchedulePreview.mock.calls[0][0]).toMatchObject({ must_visit_places: [], optional_places: [optional] });
  });

  it("shows dated attempts and daily optional limits without confusing a later scheduled date with total failure", async () => {
    controls.queryCandidates.mockResolvedValueOnce(candidateResponse(moreOptional.slice(0, 2)));
    controls.querySchedulePreview.mockImplementationOnce(async (request: ScheduleRequest) => {
      const result = makeSchedule(request);
      result.optional_results[1].attempts = [
        { date: "2026-10-10", outcome: "day_slot_used", message: "第一天名额已使用。" },
        { date: "2026-10-11", outcome: "time_window", message: "仅第二天未容纳。" },
        { date: "2026-10-12", outcome: "budget_exhausted", message: "第三天查询未完成。" },
      ] satisfies OptionalScheduleResult["attempts"];
      return result;
    });
    render(<TripPlanResult plan={makePlan(tripRequest(3))} onEdit={vi.fn()} />); await fetchCandidates(); await generated();
    const results = within(preview()).getByRole("region", { name: "可选地点试排结果" });
    for (const date of ["2026-10-10", "2026-10-11", "2026-10-12"]) expect(results.textContent).toContain(date);
    expect(results.textContent).toContain("已安排于 2026-10-10"); expect(results.textContent).toContain("本日可选名额已使用");
    expect(results.textContent).toContain("本次查询截止，未完成试排");
    expect(results.textContent).not.toMatch(/全部日期.*无法|全程.*不可行/);
    click(/草案第 3 天/); expect(controls.querySchedulePreview).toHaveBeenCalledTimes(1);
  });
});
