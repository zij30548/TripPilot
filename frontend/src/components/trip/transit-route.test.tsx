import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import TripPlanResult from "@/components/trip-plan-result";
import TripPlanner from "@/components/trip/trip-planner";
import type { AMapSDK } from "@/lib/amap-loader";
import type { Place } from "@/types/place";
import type { TripPlan } from "@/types/trip";

const controls = vi.hoisted(() => ({ loadAMap: vi.fn(), getShanghaiCenter: vi.fn(), nextPlan: null as TripPlan | null }));
vi.mock("@/lib/amap-loader", () => ({ loadAMap: controls.loadAMap, getShanghaiCenter: controls.getShanghaiCenter }));
vi.mock("@/components/trip-request-form", () => ({
  default: ({ onSuccess }: { onSuccess: (plan: TripPlan) => void }) => <button type="button" onClick={() => {
    if (controls.nextPlan) onSuccess(controls.nextPlan);
  }}>测试生成行程</button>,
}));

// Intentionally fictional coordinates, metrics, lines and stations. No live upstream/SDK is loaded.
const places: Place[] = [
  { id: "fixture-a", name: "测试地点甲", address: "测试甲地址", category: "测试", longitude: 120, latitude: 30, source: "amap" },
  { id: "fixture-b", name: "测试地点乙", address: "测试乙地址", category: "测试", longitude: 120.02, latitude: 30.02, source: "amap" },
  { id: "fixture-c", name: "测试地点丙", address: "测试丙地址", category: "测试", longitude: 120.04, latitude: 30.04, source: "amap" },
  { id: "fixture-alias", name: "测试相同坐标", address: null, category: null, longitude: 120, latitude: 30, source: "amap" },
];
const transit = {
  status: "ok", source: "amap", queried_at: "2026-10-05T01:02:03Z", selection_rule: "first_supported_complete",
  route: { duration_seconds: 901, walking_distance_meters: 1320, fare_cny: 4, geometry_complete: true, legs: [
    { mode: "walking", distance_meters: 320, duration_seconds: 240, instruction: "沿测试通道步行", line_name: null, departure_stop: null, arrival_stop: null,
      geometry: [[[120, 30], [120.003, 30.003]]], geometry_complete: true },
    { mode: "subway", distance_meters: 2000, duration_seconds: 400, instruction: null, line_name: "测试地铁甲线", departure_stop: "测试上车甲站", arrival_stop: "测试换乘乙站",
      geometry: [[[120.004, 30.004], [120.01, 30.01]]], geometry_complete: true },
    { mode: "bus", distance_meters: null, duration_seconds: null, instruction: null, line_name: "测试公交乙线", departure_stop: "测试换乘乙站", arrival_stop: "测试下车丙站",
      geometry: [[[120.011, 30.011], [120.019, 30.019]]], geometry_complete: true },
    { mode: "walking", distance_meters: 1000, duration_seconds: 0, instruction: null, line_name: null, departure_stop: null, arrival_stop: null,
      geometry: [[[120.0195, 30.0195], [120.02, 30.02]]], geometry_complete: true },
  ] },
};
const otherTransit = { ...transit, route: { ...transit.route, duration_seconds: 1801, walking_distance_meters: 520, fare_cny: null } };
const walking = {
  status: "ok", source: "amap", queried_at: transit.queried_at,
  route: { distance_meters: 2510, duration_seconds: 1901, segments: [[[120, 30], [120.01, 30.01], [120.02, 30.02]]] },
};

function makePlan(): TripPlan {
  return {
    destination: "上海", estimated_cost: 45, currency: "CNY", is_mock: true, notice: "测试 Mock 行程，活动时间、交通、费用和天气未验证。",
    request: {
      start_date: "2026-10-10", end_date: "2026-10-11", budget: 3000, travelers: 3, accommodation_location: "测试住宿",
      pace: "balanced", interests: [], must_visit: [], avoid_places: [], daily_start_time: "09:00", daily_end_time: "21:00",
    },
    budget_breakdown: { transport: 5, food: 20, tickets: 20, other: 0 },
    days: [1, 2].map((day) => ({
      day, date: `2026-10-${day === 1 ? "10" : "11"}`, title: `测试第${day}天`,
      activities: ["甲", "乙", "丙"].map((name, index) => ({
        id: `activity-${index}`, name: `活动${name}`, category: "sightseeing" as const, estimated_cost: 10,
        description: "原始 Mock 活动描述", start_time: `${9 + index}:00`, end_time: `${10 + index}:00`,
      })),
      transports: [{ from_activity_id: "activity-0", to_activity_id: "activity-1", mode: "metro" as const, duration_minutes: 17, estimated_cost: 5, description: "原始 Mock 地铁示例" }],
      weather: { date: `2026-10-${day === 1 ? "10" : "11"}`, condition: "Mock 晴", min_temperature: 18, max_temperature: 25, rain_risk: 10 },
    })),
  };
}

const maps: MockMap[] = [], markers: MockMarker[] = [], lines: MockPolyline[] = [];
class MockMap {
  destroyed = false;
  setFitView = vi.fn(); setZoomAndCenter = vi.fn(); destroy = vi.fn(() => { this.destroyed = true; });
  constructor() { maps.push(this); }
}
class MockMarker {
  attached = true; handlers = new Map<string, () => void>();
  on = vi.fn((event: string, handler: () => void) => { this.handlers.set(event, handler); });
  off = vi.fn((event: string) => { this.handlers.delete(event); });
  setMap = vi.fn((map: MockMap | null) => { this.attached = map !== null; }); setzIndex = vi.fn();
  constructor(readonly options: { map: MockMap; title: string; content: HTMLElement; position: [number, number] }) { markers.push(this); }
  click() { this.handlers.get("click")?.(); }
}
class MockPolyline {
  currentMap: MockMap | null = null;
  setMap = vi.fn((map: MockMap | null) => { this.currentMap = map; });
  constructor(readonly options: { path: [number, number][][] }) { lines.push(this); }
}
const sdk = { Map: MockMap, Marker: MockMarker, Polyline: MockPolyline } as unknown as AMapSDK;
let transitFetch: ReturnType<typeof vi.fn<typeof fetch>>;
let walkingFetch: ReturnType<typeof vi.fn<typeof fetch>>;
beforeEach(() => {
  maps.length = 0; markers.length = 0; lines.length = 0;
  controls.loadAMap.mockReset().mockResolvedValue(sdk); controls.getShanghaiCenter.mockReset().mockResolvedValue([120, 30]);
  controls.nextPlan = makePlan();
  transitFetch = vi.fn<typeof fetch>(() => Promise.resolve(new Response(JSON.stringify(transit))));
  walkingFetch = vi.fn<typeof fetch>(() => Promise.resolve(new Response(JSON.stringify(walking))));
  vi.stubGlobal("fetch", vi.fn<typeof fetch>((input, options) => {
    const path = new URL(String(input)).pathname;
    if (path === "/places/search") return Promise.resolve(new Response(JSON.stringify(places)));
    if (path === "/routes/walking") return walkingFetch(input, options);
    if (path === "/routes/transit") return transitFetch(input, options);
    return Promise.reject(new Error("Unexpected unmocked request"));
  }));
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => { callback(0); return 1; });
});

function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (reason: Error) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
function activity(name: string) { return screen.getByRole("article", { name }); }
function segment(from = "活动甲", to = "活动乙", mode = "公交／地铁") { return screen.getByRole("region", { name: `真实${mode}路线：${from} → ${to}` }); }
function mode(name: "步行" | "公交／地铁") { fireEvent.click(within(screen.getByRole("group", { name: "选择真实交通方式" })).getByRole("button", { name })); }
function beginBinding(name: string, replace = false) { fireEvent.click(within(activity(name)).getByRole("button", { name: `为${name}${replace ? "更换" : "绑定"}地点` })); }
async function bind(name: string, place: Place, replace = false) {
  beginBinding(name, replace);
  fireEvent.change(screen.getByRole("textbox", { name: "搜索上海地点" }), { target: { value: "测试搜索" } });
  fireEvent.click(screen.getByRole("button", { name: "搜索" }));
  const list = await screen.findByRole("list", { name: "地点搜索结果" });
  fireEvent.click(within(list).getByRole("button", { name: new RegExp(place.name) }));
  fireEvent.click(screen.getByRole("button", { name: "确认绑定" }));
  expect(within(activity(name)).getByText(place.name)).toBeTruthy();
}
async function pair() { await bind("活动甲", places[0]); await bind("活动乙", places[1]); }
function query(from = "活动甲", to = "活动乙", method = "公交／地铁") { fireEvent.click(within(segment(from, to, method)).getByRole("button", { name: `查询${method}路线` })); }
const currentLines = () => lines.filter((line) => line.currentMap && !line.currentMap.destroyed);
async function success(from = "活动甲", to = "活动乙") {
  expect(await within(segment(from, to)).findByText(/测试地铁甲线/)).toBeTruthy();
  await waitFor(() => expect(currentLines()).toHaveLength(4));
}
function clear(name = "活动甲") { fireEvent.click(within(activity(name)).getByRole("button", { name: `解除${name}的地点绑定` })); }
async function completePending(pending: ReturnType<typeof deferred<Response>>, outcome: "success" | "error") {
  await act(async () => {
    if (outcome === "success") pending.resolve(new Response(JSON.stringify(transit)));
    else pending.reject(new Error("fixture-private-late-error"));
  });
}

describe("adjacent public-transport interactions and stale-response isolation", () => {
  it("switches modes without requests, retains original adjacency and disables incomplete endpoint pairs", async () => {
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    mode("公交／地铁");
    await bind("活动甲", places[0]); await bind("活动丙", places[2]);
    expect(screen.getAllByRole("region", { name: /^真实公交／地铁路线：/ })).toHaveLength(2);
    expect(screen.queryByRole("region", { name: "真实公交／地铁路线：活动甲 → 活动丙" })).toBeNull();
    for (const region of screen.getAllByRole("region", { name: /^真实公交／地铁路线：/ })) {
      expect(within(region).getByText(/请先为前后两个相邻活动确认绑定地点/)).toBeTruthy();
      expect((within(region).getByRole("button", { name: "查询公交／地铁路线" }) as HTMLButtonElement).disabled).toBe(true);
    }
    mode("步行"); mode("公交／地铁");
    expect(transitFetch).not.toHaveBeenCalled(); expect(walkingFetch).not.toHaveBeenCalled();
  });

  it("shows normalized ordered steps, total time once and per-person reference fare without modifying Mock data", async () => {
    const plan = makePlan(), original = JSON.stringify(plan);
    render(<TripPlanResult plan={plan} onEdit={vi.fn()} />);
    await pair(); mode("公交／地铁");
    expect(transitFetch).not.toHaveBeenCalled(); query(); await success();
    expect(transitFetch).toHaveBeenCalledTimes(1);
    expect(JSON.parse(String(transitFetch.mock.calls[0][1]?.body))).toEqual({
      origin: { place_id: places[0].id, longitude: 120, latitude: 30 },
      destination: { place_id: places[1].id, longitude: 120.02, latitude: 30.02 },
    });
    const area = within(segment());
    expect(area.getByText(/预计\s*16\s*分钟/)).toBeTruthy();
    expect(area.getByText(/接驳步行.*1\.32.*公里/)).toBeTruthy();
    expect(area.getByText(/参考票价.*4\.00/)).toBeTruthy();
    expect(area.queryByText(/参考票价.*12\.00/)).toBeNull();
    expect(area.getByText(/上车：.*测试上车甲站/)).toBeTruthy();
    expect(area.getByText(/下车：.*测试下车丙站/)).toBeTruthy();
    const content = segment().textContent ?? "";
    expect(content.indexOf("沿测试通道步行")).toBeLessThan(content.indexOf("测试地铁甲线"));
    expect(content.indexOf("测试地铁甲线")).toBeLessThan(content.indexOf("测试公交乙线"));
    expect(area.getByRole("time").getAttribute("datetime")).toBe(transit.queried_at);
    expect(content).toMatch(/未.*旅行日期.*(?:运营|时刻)/);
    expect(content).toMatch(/首.*(?:完整|支持)/);
    expect(screen.getByText("原始 Mock 地铁示例")).toBeTruthy();
    expect(screen.getByText("17 分钟")).toBeTruthy();
    expect(JSON.stringify(plan)).toBe(original);
  });

  it("shows unknown fare and incomplete map honestly while retaining known geometry and complete textual steps", async () => {
    transitFetch.mockResolvedValueOnce(new Response(JSON.stringify({ ...otherTransit, route: { ...otherTransit.route, geometry_complete: false,
      legs: otherTransit.route.legs.map((leg, index) => index === 1 ? { ...leg, geometry: [], geometry_complete: false } : leg),
    } })));
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await pair(); mode("公交／地铁"); query();
    expect(await within(segment()).findByText(/参考票价.*未知/)).toBeTruthy();
    expect(within(segment()).getByText(/预计\s*31\s*分钟/)).toBeTruthy();
    expect(within(segment()).getByText(/接驳步行.*520.*米/)).toBeTruthy();
    expect(within(segment()).getByText(/测试地铁甲线/)).toBeTruthy();
    await waitFor(() => expect(currentLines()).toHaveLength(3));
    expect(screen.getAllByText(/地图不完整/).length).toBeGreaterThan(0);
  });

  it.each([0, 3])("does not query equal POI IDs or equal coordinates (fixture %s)", async (index) => {
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await bind("活动甲", places[0]); await bind("活动乙", places[index]); mode("公交／地铁");
    expect(within(segment()).getByText(/同一地点/)).toBeTruthy();
    expect((within(segment()).getByRole("button", { name: "查询公交／地铁路线" }) as HTMLButtonElement).disabled).toBe(true);
    expect(transitFetch).not.toHaveBeenCalled();
  });

  it.each([
    { status: 200, payload: { ...transit, status: "no_route", route: null }, message: /未找到.*方案/ },
    { status: 200, payload: { ...transit, status: "unsupported", route: null }, message: /(?:没有|暂无|未找到).*支持|不支持|无法.*展示/ },
    { status: 504, payload: { detail: "fixture-private-error" }, message: /查询超时/ },
    { status: 502, payload: { detail: "fixture-private-error" }, message: /(?:暂时不可用|查询失败|服务不可用)/ },
  ])("distinguishes no-route, unsupported, timeout and other failures without invented geometry ($status)", async ({ status, payload, message }) => {
    transitFetch.mockResolvedValueOnce(new Response(JSON.stringify(payload), { status }));
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await pair(); mode("公交／地铁"); query();
    expect(await within(segment()).findByText(message)).toBeTruthy();
    expect(currentLines()).toHaveLength(0);
    expect(screen.queryByText(/fixture-private-error/)).toBeNull();
  });

  it("clears mode results immediately, requires a fresh click and leaves walking/marker behavior usable", async () => {
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await pair(); mode("公交／地铁"); query(); await success();
    const old = [...currentLines()]; mode("步行");
    expect(currentLines()).toHaveLength(0); expect(old.every((line) => line.currentMap === null)).toBe(true);
    expect(walkingFetch).not.toHaveBeenCalled();
    query("活动甲", "活动乙", "步行");
    expect(await within(segment("活动甲", "活动乙", "步行")).findByText(/2\.51.*公里/)).toBeTruthy();
    await waitFor(() => expect(currentLines()).toHaveLength(1));
    mode("公交／地铁"); expect(currentLines()).toHaveLength(0); expect(transitFetch).toHaveBeenCalledTimes(1);
    query(); await success();
    fireEvent.click(within(activity("活动乙")).getByRole("button", { name: "在地图定位活动乙" }));
    expect(maps.at(-1)?.setZoomAndCenter).toHaveBeenLastCalledWith(16, [120.02, 30.02]);
    const marker = markers.findLast((item) => item.attached && !item.options.map.destroyed && item.options.title === places[0].name);
    act(() => marker?.click()); expect(document.activeElement).toBe(activity("活动甲"));
    expect(currentLines()).toHaveLength(4);
  });

  it.each(["success", "error"] as const)("ignores late transit %s after transit→walking→transit, even with identical endpoints", async (outcome) => {
    const pending = deferred<Response>(); transitFetch.mockReturnValueOnce(pending.promise);
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await pair(); mode("公交／地铁"); query(); const signal = transitFetch.mock.calls[0][1]?.signal;
    mode("步行"); mode("公交／地铁"); expect(signal?.aborted).toBe(true);
    transitFetch.mockResolvedValueOnce(new Response(JSON.stringify(otherTransit))); query(); await success();
    await completePending(pending, outcome);
    expect(within(segment()).getByText(/预计\s*31\s*分钟/)).toBeTruthy();
    expect(within(segment()).queryByText(/预计\s*16\s*分钟/)).toBeNull();
    expect(within(segment()).queryByRole("alert")).toBeNull(); expect(currentLines()).toHaveLength(4);
  });

  it.each(["success", "error"] as const)("ignores late walking %s after walking→transit→walking", async (outcome) => {
    const pending = deferred<Response>(); walkingFetch.mockReturnValueOnce(pending.promise);
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await pair(); query("活动甲", "活动乙", "步行"); const signal = walkingFetch.mock.calls[0][1]?.signal;
    mode("公交／地铁"); mode("步行"); expect(signal?.aborted).toBe(true);
    await act(async () => {
      if (outcome === "success") pending.resolve(new Response(JSON.stringify(walking)));
      else pending.reject(new Error("fixture-private-late-walking-error"));
    });
    expect(within(segment("活动甲", "活动乙", "步行")).queryByText(/2\.51.*公里/)).toBeNull();
    expect(within(segment("活动甲", "活动乙", "步行")).queryByRole("alert")).toBeNull();
    expect(currentLines()).toHaveLength(0); expect(walkingFetch).toHaveBeenCalledTimes(1); expect(transitFetch).not.toHaveBeenCalled();
  });

  it.each(["success", "error"] as const)("isolates endpoint A→B→A from an old transit %s", async (outcome) => {
    const pending = deferred<Response>(); transitFetch.mockReturnValueOnce(pending.promise);
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await pair(); mode("公交／地铁"); query(); const signal = transitFetch.mock.calls[0][1]?.signal;
    await bind("活动甲", places[2], true); await bind("活动甲", places[0], true);
    expect(signal?.aborted).toBe(true);
    transitFetch.mockResolvedValueOnce(new Response(JSON.stringify(otherTransit))); query(); await success();
    await completePending(pending, outcome);
    expect(within(segment()).getByText(/预计\s*31\s*分钟/)).toBeTruthy();
    expect(within(segment()).queryByText(/预计\s*16\s*分钟/)).toBeNull();
    expect(within(segment()).queryByRole("alert")).toBeNull();
  });

  it("clears old geometry on a different adjacent-pair query and rejects a late first-pair response", async () => {
    const pending = deferred<Response>(); transitFetch.mockReturnValueOnce(pending.promise);
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await pair(); await bind("活动丙", places[2]); mode("公交／地铁"); query();
    const signal = transitFetch.mock.calls[0][1]?.signal;
    transitFetch.mockResolvedValueOnce(new Response(JSON.stringify(otherTransit))); query("活动乙", "活动丙"); await success("活动乙", "活动丙");
    expect(signal?.aborted).toBe(true); await completePending(pending, "success");
    expect(within(segment("活动乙", "活动丙")).getByText(/预计\s*31\s*分钟/)).toBeTruthy();
    expect(within(segment()).queryByText(/测试地铁甲线/)).toBeNull(); expect(currentLines()).toHaveLength(4);
  });

  it("keeps a cancelled replacement but invalidates on confirmed replacement and unbinding", async () => {
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await pair(); mode("公交／地铁"); query(); await success();
    beginBinding("活动甲", true); expect(currentLines()).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "取消绑定" }));
    expect(within(segment()).getByText(/预计\s*16\s*分钟/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "当天已绑定地点" }));
    await waitFor(() => expect(currentLines()).toHaveLength(4));
    await bind("活动乙", places[2], true);
    expect(currentLines()).toHaveLength(0); expect(within(segment()).queryByText(/测试地铁甲线/)).toBeNull();
    query(); await success(); clear("活动乙");
    expect(currentLines()).toHaveLength(0); expect(within(segment()).queryByText(/测试地铁甲线/)).toBeNull();
    expect(transitFetch).toHaveBeenCalledTimes(2);
  });

  it.each(["success", "error"] as const)("retains per-day bindings while a previous date's late %s cannot overwrite the new date", async (outcome) => {
    const pending = deferred<Response>(); transitFetch.mockReturnValueOnce(pending.promise);
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await pair(); mode("公交／地铁"); query(); const signal = transitFetch.mock.calls[0][1]?.signal;
    fireEvent.click(screen.getByRole("button", { name: /Day 2/ })); expect(signal?.aborted).toBe(true);
    await pair(); mode("公交／地铁"); transitFetch.mockResolvedValueOnce(new Response(JSON.stringify(otherTransit))); query(); await success();
    await completePending(pending, outcome);
    expect(within(segment()).getByText(/预计\s*31\s*分钟/)).toBeTruthy();
    expect(within(segment()).queryByRole("alert")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Day 1/ })); mode("公交／地铁");
    expect(currentLines()).toHaveLength(0); expect(within(segment()).queryByText(/测试地铁甲线/)).toBeNull();
    expect(within(activity("活动甲")).getByText(places[0].name)).toBeTruthy();
    expect(within(activity("活动乙")).getByText(places[1].name)).toBeTruthy();
  });

  it("does not let a late route steal a newer candidate search view", async () => {
    const pending = deferred<Response>(); transitFetch.mockReturnValueOnce(pending.promise);
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await pair(); mode("公交／地铁"); query(); beginBinding("活动丙");
    await completePending(pending, "success");
    expect(within(segment()).getByText(/测试地铁甲线/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "搜索候选地点" }).getAttribute("aria-pressed")).toBe("true");
    expect(currentLines()).toHaveLength(0);
  });

  it.each(["success", "error"] as const)("aborts an edited/regenerated plan and isolates its late %s from a fresh result", async (outcome) => {
    const pending = deferred<Response>(); transitFetch.mockReturnValueOnce(pending.promise);
    render(<TripPlanner><p>测试说明</p></TripPlanner>);
    fireEvent.click(screen.getByRole("button", { name: "测试生成行程" })); await pair(); mode("公交／地铁"); query();
    const signal = transitFetch.mock.calls[0][1]?.signal;
    fireEvent.click(screen.getByRole("button", { name: "修改旅行需求" })); expect(signal?.aborted).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "测试生成行程" })); mode("公交／地铁");
    await completePending(pending, outcome);
    expect(within(segment()).getByText(/请先为前后两个相邻活动确认绑定地点/)).toBeTruthy();
    expect(within(segment()).queryByRole("alert")).toBeNull(); expect(currentLines()).toHaveLength(0);
  });

  it("cleans all displayed segments on unmount and aborts pending requests in an independently remounted result", async () => {
    const view = render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await pair(); mode("公交／地铁"); query(); await success();
    const old = [...currentLines()];
    const pending = deferred<Response>(); transitFetch.mockReturnValueOnce(pending.promise); query();
    expect(old.every((line) => line.currentMap === null)).toBe(true);
    const signal = transitFetch.mock.calls[1][1]?.signal;
    view.unmount(); expect(signal?.aborted).toBe(true);
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />); mode("公交／地铁");
    await completePending(pending, "success");
    expect(currentLines()).toHaveLength(0); expect(within(segment()).queryByText(/测试地铁甲线/)).toBeNull();
  });
});
