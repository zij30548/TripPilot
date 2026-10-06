import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import TripPlanResult from "@/components/trip-plan-result";
import TripPlanner from "./trip-planner";
import type { AMapSDK } from "@/lib/amap-loader";
import { ScheduleError } from "@/lib/schedule-api";
import type { Place } from "@/types/place";
import type { ScheduleEdge, ScheduleItem, ScheduleRequest, ScheduleResponse } from "@/types/schedule";
import type { TripPlan, TripRequest } from "@/types/trip";

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
const stamp = "2026-10-06T03:00:00Z";

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
function makeSchedule(request: ScheduleRequest, count = request.must_visit_places.length): ScheduleResponse {
  const edges: ScheduleEdge[] = [], items: ScheduleItem[] = [];
  let time = minute(request.daily_start_time), origin = request.accommodation_place;
  function append(kind: ScheduleItem["kind"], duration: number, values: Partial<ScheduleItem> = {}) {
    items.push({ kind, start_time: clock(time), end_time: clock(time + duration), duration_minutes: duration, place_id: null, from_place_id: null, to_place_id: null, edge_id: null, duration_source: null, ...values });
    time += duration;
  }
  function walk(destination: Place) {
    const edge: ScheduleEdge = { id: `edge-${edges.length}`, origin: { place_id: origin.id, longitude: origin.longitude, latitude: origin.latitude }, destination: { place_id: destination.id, longitude: destination.longitude, latitude: destination.latitude }, status: "ok", duration_seconds: 61.2, duration_minutes: 2, distance_meters: 80, source: "amap", queried_at: stamp, message: null, used: true };
    edges.push(edge); append("walk", 2, { from_place_id: origin.id, to_place_id: destination.id, edge_id: edge.id }); origin = destination;
  }
  for (const place of request.must_visit_places.slice(0, count)) {
    walk(place);
    const stay = request.duration_settings.find((setting) => setting.place_id === place.id)!;
    append("visit", stay.minutes, { place_id: place.id, duration_source: stay.source });
  }
  if (count > 0) {
    if (request.lunch.enabled && time <= minute(request.lunch.start_time)) {
      if (time < minute(request.lunch.start_time)) append("wait", minute(request.lunch.start_time) - time);
      append("lunch", minute(request.lunch.end_time) - time);
    }
    walk(request.accommodation_place);
  }
  const days = Math.round((Date.parse(request.end_date) - Date.parse(request.start_date)) / 86_400_000) + 1;
  return {
    status: count === request.must_visit_places.length ? "complete" : count === 0 ? "unscheduled" : "partial", generated_at: stamp, request, edges,
    days: Array.from({ length: days }, (_, index) => ({ date: new Date(Date.parse(`${request.start_date}T00:00:00Z`) + index * 86_400_000).toISOString().slice(0, 10), items: index === 0 ? items : [], return_time: index === 0 && count > 0 ? clock(time) : null })),
    unscheduled: request.must_visit_places.slice(count).map((place, index) => ({ place_id: place.id, reason: index === 0 ? "time_window" : "current_order_not_continued", message: index === 0 ? "当前地点在本次时间窗口内放不下。" : "本次固定顺序未继续尝试后续地点。" })),
    rules: ["保持确认顺序，不做最优排序。", "每条步行秒数分别向上取整为分钟。"],
    unknowns: ["营业时间与预约要求未知。", "预算与门票、餐费未知。", "午餐没有选择餐厅，餐厅绕路未计。"],
  };
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
  controls.queryCandidates.mockReset().mockResolvedValue({ status: "success", queried_at: stamp, keywords: ["公园"], queries: [{ interest: "摄影", keyword: "公园", status: "success", result_count: 1, message: null }], candidates: [{ place: first, role: "must_visit", retrieval_sources: [] }, { place: second, role: "must_visit", retrieval_sources: [] }, { place: optional, role: "optional", retrieval_sources: [{ interest: "摄影", keyword: "公园" }] }] });
  controls.searchPlaces.mockReset().mockResolvedValue([lodging, first, second]);
  controls.planTrip.mockReset().mockImplementation(async (request: TripRequest) => makePlan(request));
  controls.loadAMap.mockReset().mockResolvedValue({ Map: MockMap, Marker: MockMarker, Polyline: MockPolyline } as unknown as AMapSDK);
  controls.getShanghaiCenter.mockReset().mockResolvedValue([120, 30]);
  routeFetch = vi.fn<typeof fetch>().mockImplementation(async () => new Response(JSON.stringify({ status: "ok", source: "amap", queried_at: stamp, route: { distance_meters: 500, duration_seconds: 400, segments: [[[120.01, 30.01], [120.02, 30.02]]] } })));
  vi.stubGlobal("fetch", routeFetch);
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => { callback(0); return 1; });
});

function preview() { return screen.getByRole("region", { name: "必去地点步行草案" }); }
function click(name: string | RegExp) { fireEvent.click(screen.getByRole("button", { name })); }
function generate() { fireEvent.click(within(preview()).getByRole("button", { name: "生成步行草案" })); }
function stay(place = first) { return within(preview()).getByRole("spinbutton", { name: `停留分钟：${place.name}` }) as HTMLInputElement; }
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
    expect((within(preview()).getByRole("button", { name: "生成步行草案" }) as HTMLButtonElement).disabled).toBe(true);
    expect(within(preview()).getByRole("alert").textContent).toMatch(kind === "no-accommodation" ? /住宿/ : /必去|1.*6|6.*个/);
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
    expect((within(preview()).getByRole("button", { name: "生成步行草案" }) as HTMLButtonElement).disabled).toBe(true);
    expect(within(preview()).getByRole("alert").textContent).toMatch(/15|480|分钟|整数/);
    expect(within(preview()).queryByRole("list", { name: "草案时间线" })).toBeNull(); expect(controls.querySchedulePreview).toHaveBeenCalledTimes(1);
  });

  it("validates the whole lunch interval inside the daily window and permits disabling lunch", async () => {
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />); await generated();
    fireEvent.change(within(preview()).getByLabelText("午餐开始时间"), { target: { value: "08:00" } });
    expect(within(preview()).queryByRole("list", { name: "草案时间线" })).toBeNull();
    expect((within(preview()).getByRole("button", { name: "生成步行草案" }) as HTMLButtonElement).disabled).toBe(true);
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

  it("keeps candidate exclusions and the existing binding/route map independent of schedule generation and settings", async () => {
    const plan = makePlan(), original = JSON.stringify(plan);
    render(<TripPlanResult plan={plan} onEdit={vi.fn()} />);
    click("获取候选地点"); await screen.findByRole("button", { name: `排除可选地点：${optional.name}` }); click(`排除可选地点：${optional.name}`);
    await bind("活动甲", first); await bind("活动乙", second); click("查询步行路线");
    await screen.findByText(/来源：高德步行路线/); await waitFor(() => expect(lines.some((line) => line.currentMap)).toBe(true));
    const oldLine = lines.find((line) => line.currentMap)!; await generated();
    expect(oldLine.currentMap).not.toBeNull(); expect(screen.getByRole("button", { name: `恢复可选地点：${optional.name}` })).toBeTruthy();
    fireEvent.change(stay(), { target: { value: "90" } }); expect(oldLine.currentMap).not.toBeNull();
    expect(within(screen.getByRole("article", { name: "活动乙" })).getByText(second.name)).toBeTruthy();
    await generated(); click(`恢复可选地点：${optional.name}`); click("重新获取候选地点");
    await waitFor(() => expect(controls.queryCandidates).toHaveBeenCalledTimes(2));
    const marker = markers.findLast((item) => item.attached && item.options.title === first.name)!; act(() => marker.click());
    click("在地图查看活动乙");
    fireEvent.click(within(screen.getByRole("group", { name: "选择真实交通方式" })).getByRole("button", { name: "公交／地铁" }));
    click(/Day 2/); click(/Day 1/);
    expect(within(preview()).getByRole("list", { name: "草案时间线" })).toBeTruthy();
    expect(controls.querySchedulePreview).toHaveBeenCalledTimes(2); expect(routeFetch).toHaveBeenCalledTimes(1); expect(JSON.stringify(plan)).toBe(original);
  });

  it("edit/regeneration preserves 5A confirmations but clears the draft and restores default planning settings", async () => {
    await plannerResult(); fireEvent.change(stay(), { target: { value: "90" } });
    fireEvent.click(within(preview()).getByRole("checkbox", { name: "安排午餐时间" })); await generated(); click("修改旅行需求");
    expect(within(screen.getByRole("region", { name: "已确认住宿参考点" })).getByText(lodging.name)).toBeTruthy();
    expect(within(screen.getByRole("region", { name: "已确认必去地点" })).getByText(first.name)).toBeTruthy();
    submitTrip(); await screen.findByRole("button", { name: "修改旅行需求" });
    expect(stay().value).toBe("60"); expect((within(preview()).getByRole("checkbox", { name: "安排午餐时间" }) as HTMLInputElement).checked).toBe(true);
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
});
