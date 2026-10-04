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

// All coordinates, routes, times and POIs below are fictional offline fixtures.
const places: Place[] = [
  { id: "fixture-a", name: "测试地点甲", address: "测试甲地址", category: "测试", longitude: 120, latitude: 30, source: "amap" },
  { id: "fixture-b", name: "测试地点乙", address: "测试乙地址", category: "测试", longitude: 120.01, latitude: 30.01, source: "amap" },
  { id: "fixture-c", name: "测试地点丙", address: "测试丙地址", category: "测试", longitude: 120.02, latitude: 30.02, source: "amap" },
  { id: "fixture-a-alias", name: "测试同坐标地点", address: "测试别名地址", category: "测试", longitude: 120, latitude: 30, source: "amap" },
];
const routeResult = {
  status: "ok", source: "amap", queried_at: "2026-10-05T01:02:03Z",
  route: { distance_meters: 1250, duration_seconds: 901, segments: [[[120, 30], [120.005, 30.005], [120.01, 30.01]]] },
};
const otherResult = {
  ...routeResult, route: { distance_meters: 520, duration_seconds: 301, segments: [[[120.01, 30.01], [120.02, 30.02]]] },
};

function makePlan(): TripPlan {
  return {
    destination: "上海", estimated_cost: 45, currency: "CNY", is_mock: true, notice: "测试 Mock 行程，活动时间、交通、费用和天气未验证。",
    request: {
      start_date: "2026-10-10", end_date: "2026-10-11", budget: 3000, travelers: 2,
      accommodation_location: "测试住宿", pace: "balanced", interests: [], must_visit: [], avoid_places: [], daily_start_time: "09:00", daily_end_time: "21:00",
    },
    budget_breakdown: { transport: 5, food: 20, tickets: 20, other: 0 },
    days: [1, 2].map((day) => ({
      day, date: `2026-10-${day === 1 ? "10" : "11"}`, title: `测试第${day}天`,
      // Deliberately reuse all activity IDs and names on another date.
      activities: ["甲", "乙", "丙"].map((name, index) => ({
        id: `activity-${index}`, name: `活动${name}`, category: "sightseeing" as const,
        estimated_cost: 10, description: "原始 Mock 活动描述", start_time: `${9 + index}:00`, end_time: `${10 + index}:00`,
      })),
      transports: [{ from_activity_id: "activity-0", to_activity_id: "activity-1", mode: "metro" as const, duration_minutes: 17, estimated_cost: 5, description: "原始 Mock 地铁示例" }],
      weather: { date: `2026-10-${day === 1 ? "10" : "11"}`, condition: "Mock 晴", min_temperature: 18, max_temperature: 25, rain_risk: 10 },
    })),
  };
}

const maps: MockMap[] = [];
const markers: MockMarker[] = [];
const lines: MockPolyline[] = [];
class MockMap {
  destroyed = false;
  setFitView = vi.fn();
  setZoomAndCenter = vi.fn();
  destroy = vi.fn(() => { this.destroyed = true; });
  constructor() { maps.push(this); }
}
class MockMarker {
  attached = true;
  handlers = new Map<string, () => void>();
  on = vi.fn((event: string, handler: () => void) => { this.handlers.set(event, handler); });
  off = vi.fn((event: string) => { this.handlers.delete(event); });
  setMap = vi.fn((map: MockMap | null) => { this.attached = map !== null; });
  setzIndex = vi.fn();
  constructor(readonly options: { map: MockMap; title: string; content: HTMLElement; position: [number, number] }) { markers.push(this); }
  click() { this.handlers.get("click")?.(); }
}
class MockPolyline {
  currentMap: MockMap | null = null;
  setMap = vi.fn((map: MockMap | null) => { this.currentMap = map; });
  constructor(readonly options: { path: [number, number][][] }) { lines.push(this); }
}
const sdk = { Map: MockMap, Marker: MockMarker, Polyline: MockPolyline } as unknown as AMapSDK;
let routeFetch: ReturnType<typeof vi.fn<typeof fetch>>;
let fetchMock: ReturnType<typeof vi.fn<typeof fetch>>;

beforeEach(() => {
  maps.length = 0; markers.length = 0; lines.length = 0;
  controls.loadAMap.mockReset().mockResolvedValue(sdk);
  controls.getShanghaiCenter.mockReset().mockResolvedValue([120, 30]);
  controls.nextPlan = makePlan();
  routeFetch = vi.fn<typeof fetch>(() => Promise.resolve(new Response(JSON.stringify(routeResult))));
  fetchMock = vi.fn<typeof fetch>((input, options) => {
    const path = new URL(String(input)).pathname;
    if (path === "/places/search") return Promise.resolve(new Response(JSON.stringify(places)));
    if (path === "/routes/walking") return routeFetch(input, options);
    return Promise.reject(new Error("Unexpected unmocked request"));
  });
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => { callback(0); return 1; });
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}
function activity(name: string) { return screen.getByRole("article", { name }); }
function segment(from = "活动甲", to = "活动乙") { return screen.getByRole("region", { name: `真实步行路线：${from} → ${to}` }); }
function beginBinding(name: string, replacing = false) {
  fireEvent.click(within(activity(name)).getByRole("button", { name: `为${name}${replacing ? "更换" : "绑定"}地点` }));
}
async function bind(name: string, place: Place, replacing = false) {
  beginBinding(name, replacing);
  fireEvent.change(screen.getByRole("textbox", { name: "搜索上海地点" }), { target: { value: "测试搜索" } });
  fireEvent.click(screen.getByRole("button", { name: "搜索" }));
  const list = await screen.findByRole("list", { name: "地点搜索结果" });
  fireEvent.click(within(list).getByRole("button", { name: new RegExp(place.name) }));
  fireEvent.click(screen.getByRole("button", { name: "确认绑定" }));
  expect(within(activity(name)).getByText(place.name)).toBeTruthy();
}
async function bindPair() { await bind("活动甲", places[0]); await bind("活动乙", places[1]); }
function query(from = "活动甲", to = "活动乙") {
  fireEvent.click(within(segment(from, to)).getByRole("button", { name: "查询步行路线" }));
}
function currentLines() { return lines.filter((line) => line.currentMap && !line.currentMap.destroyed); }
async function expectSuccess(from = "活动甲", to = "活动乙") {
  expect(await within(segment(from, to)).findByText(/来源：高德步行路线/)).toBeTruthy();
  await waitFor(() => expect(currentLines()).toHaveLength(1));
}
function clearBinding(name = "活动甲") {
  fireEvent.click(within(activity(name)).getByRole("button", { name: `解除${name}的地点绑定` }));
}

describe("adjacent activity walking route integration", () => {
  it("keeps original adjacency when the middle activity is unbound, and never auto-queries", async () => {
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    expect(screen.getAllByRole("region", { name: /^真实步行路线：/ })).toHaveLength(2);
    expect(screen.queryByRole("region", { name: "真实步行路线：活动甲 → 活动丙" })).toBeNull();
    await bind("活动甲", places[0]);
    await bind("活动丙", places[2]);
    for (const region of screen.getAllByRole("region", { name: /^真实步行路线：/ })) {
      expect(within(region).getByText(/请先为前后两个相邻活动确认绑定地点/)).toBeTruthy();
      expect((within(region).getByRole("button", { name: "查询步行路线" }) as HTMLButtonElement).disabled).toBe(true);
    }
    expect(routeFetch).not.toHaveBeenCalled();
  });

  it("queries only on click and shows normalized units/source/query time while preserving the Mock plan", async () => {
    const plan = makePlan();
    const original = JSON.stringify(plan);
    render(<TripPlanResult plan={plan} onEdit={vi.fn()} />);
    await bindPair();
    expect(routeFetch).not.toHaveBeenCalled();
    query();
    await expectSuccess();
    expect(routeFetch).toHaveBeenCalledTimes(1);
    expect(JSON.parse(String(routeFetch.mock.calls[0][1]?.body))).toEqual({
      origin: { place_id: places[0].id, longitude: 120, latitude: 30 },
      destination: { place_id: places[1].id, longitude: 120.01, latitude: 30.01 },
    });
    expect(within(segment()).getByText(/1\.25\s*公里/)).toBeTruthy();
    expect(within(segment()).getByText(/预计\s*16\s*分钟/)).toBeTruthy();
    expect(within(segment()).getByText(/查询时间/)).toBeTruthy();
    expect(within(segment()).getByRole("time").getAttribute("datetime")).toBe(routeResult.queried_at);
    expect(screen.getByText("原始 Mock 地铁示例")).toBeTruthy();
    expect(screen.getByText("17 分钟")).toBeTruthy();
    expect(JSON.stringify(plan)).toBe(original);
  });

  it.each([0, 3])("does not request a route for identical POI identity or coordinates (fixture %s)", async (index) => {
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await bind("活动甲", places[0]);
    await bind("活动乙", places[index]);
    expect(within(segment()).getByText(/同一地点，无需查询步行路线/)).toBeTruthy();
    expect((within(segment()).getByRole("button", { name: "查询步行路线" }) as HTMLButtonElement).disabled).toBe(true);
    expect(routeFetch).not.toHaveBeenCalled();
    expect(currentLines()).toHaveLength(0);
  });

  it.each([
    { status: 200, payload: { ...routeResult, status: "no_route", route: null }, message: /未找到可用步行路线/ },
    { status: 504, payload: { detail: "fixture-private-upstream-detail" }, message: /步行路线查询超时/ },
    { status: 502, payload: { detail: "fixture-private-upstream-detail" }, message: /步行路线.*(?:不可用|失败)/ },
  ])("distinguishes unavailable route, timeout and failure without fake lines ($status)", async ({ status, payload, message }) => {
    routeFetch.mockResolvedValueOnce(new Response(JSON.stringify(payload), { status }));
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await bindPair();
    query();
    expect(await within(segment()).findByText(message)).toBeTruthy();
    expect(currentLines()).toHaveLength(0);
    expect(within(segment()).queryByText(/1250|1\.25/)).toBeNull();
    expect(screen.queryByText(/fixture-private-upstream-detail/)).toBeNull();
    expect((within(segment()).getByRole("button", { name: "查询步行路线" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("clears the previous line immediately on another segment query and ignores the old segment's late response", async () => {
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await bindPair(); await bind("活动丙", places[2]);
    query(); await expectSuccess();
    const oldLines = [...currentLines()];

    const pending = deferred<Response>();
    routeFetch.mockReturnValueOnce(pending.promise);
    query("活动乙", "活动丙");
    expect(currentLines()).toHaveLength(0);
    expect(oldLines[0].setMap).toHaveBeenCalledWith(null);
    expect(within(segment("活动乙", "活动丙")).getByRole("button", { name: "正在查询步行路线…" })).toBeTruthy();
    const signal = routeFetch.mock.calls[1][1]?.signal;

    query(); await expectSuccess();
    expect(signal?.aborted).toBe(true);
    await act(async () => { pending.resolve(new Response(JSON.stringify(otherResult))); });
    expect(within(segment()).getByText(/1\.25\s*公里/)).toBeTruthy();
    expect(currentLines()[0].options.path).toEqual(routeResult.route.segments);
    expect(within(segment("活动乙", "活动丙")).queryByText(/520\s*米/)).toBeNull();
  });

  it("replaces the selected segment with its own line and keeps activity/marker selection usable", async () => {
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await bindPair(); await bind("活动丙", places[2]);
    query(); await expectSuccess();
    const firstLines = [...currentLines()];
    routeFetch.mockResolvedValueOnce(new Response(JSON.stringify(otherResult)));
    query("活动乙", "活动丙"); await expectSuccess("活动乙", "活动丙");
    expect(firstLines[0].currentMap).toBeNull();
    expect(currentLines()[0].options.path).toEqual(otherResult.route.segments);
    expect(within(segment("活动乙", "活动丙")).getByText(/520\s*米/)).toBeTruthy();
    expect(within(segment("活动乙", "活动丙")).getByText(/预计\s*6\s*分钟/)).toBeTruthy();

    fireEvent.click(within(activity("活动乙")).getByRole("button", { name: "在地图定位活动乙" }));
    expect(maps.at(-1)?.setZoomAndCenter).toHaveBeenLastCalledWith(16, [120.01, 30.01]);
    const liveMarker = markers.filter((marker) => marker.attached && !marker.options.map.destroyed).find((marker) => marker.options.title === places[2].name);
    expect(liveMarker).toBeTruthy();
    act(() => { liveMarker?.click(); });
    expect(document.activeElement).toBe(activity("活动丙"));
    expect(currentLines()).toHaveLength(1);
  });

  it("hides routes in candidate preview and retains the valid route after cancelling an unconfirmed replacement", async () => {
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await bindPair(); query(); await expectSuccess();
    beginBinding("活动甲", true);
    expect(currentLines()).toHaveLength(0);
    expect(within(segment()).getByText(/1\.25\s*公里/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "取消绑定" }));
    expect(within(activity("活动甲")).getByText(places[0].name)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "当天已绑定地点" }));
    await waitFor(() => expect(currentLines()).toHaveLength(1));
    expect(routeFetch).toHaveBeenCalledTimes(1);
  });

  it("a pending route response cannot steal the newer candidate-search view", async () => {
    const pending = deferred<Response>();
    routeFetch.mockReturnValueOnce(pending.promise);
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await bindPair(); query();
    const loadingButton = within(segment()).getByRole("button", { name: "正在查询步行路线…" }) as HTMLButtonElement;
    expect(loadingButton.disabled).toBe(true);
    fireEvent.click(loadingButton);
    expect(routeFetch).toHaveBeenCalledTimes(1);
    beginBinding("活动丙");

    await act(async () => { pending.resolve(new Response(JSON.stringify(routeResult))); });
    expect(within(segment()).getByText(/1\.25\s*公里/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "搜索候选地点" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("textbox", { name: "搜索上海地点" })).toBeTruthy();
    expect(currentLines()).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "取消绑定" }));
    fireEvent.click(within(segment()).getByRole("button", { name: "在地图查看路线" }));
    await waitFor(() => expect(currentLines()).toHaveLength(1));
    expect(routeFetch).toHaveBeenCalledTimes(1);
  });

  it("clears a successful route on confirmed replacement or endpoint removal", async () => {
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await bindPair(); query(); await expectSuccess();
    await bind("活动甲", places[2], true);
    expect(currentLines()).toHaveLength(0);
    expect(within(segment()).queryByText(/1\.25\s*公里/)).toBeNull();
    expect(routeFetch).toHaveBeenCalledTimes(1);

    query(); await expectSuccess();
    clearBinding();
    expect(currentLines()).toHaveLength(0);
    expect(within(segment()).queryByText(/1\.25\s*公里/)).toBeNull();
    expect(within(segment()).getByText(/请先为前后两个相邻活动确认绑定地点/)).toBeTruthy();
  });

  it("does not restore an old request after an endpoint changes A→B→A, even with the same final IDs", async () => {
    const pending = deferred<Response>();
    routeFetch.mockReturnValueOnce(pending.promise);
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await bindPair(); query();
    const signal = routeFetch.mock.calls[0][1]?.signal;
    await bind("活动甲", places[2], true);
    expect(signal?.aborted).toBe(true);
    await bind("活动甲", places[0], true);
    await act(async () => { pending.resolve(new Response(JSON.stringify(routeResult))); });
    expect(within(segment()).queryByText(/1\.25\s*公里/)).toBeNull();
    expect(currentLines()).toHaveLength(0);
    expect(routeFetch).toHaveBeenCalledTimes(1);
    query(); await expectSuccess();
    expect(routeFetch).toHaveBeenCalledTimes(2);
  });

  it("aborts on endpoint removal and cannot resurrect a route when the late request resolves", async () => {
    const pending = deferred<Response>();
    routeFetch.mockReturnValueOnce(pending.promise);
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await bindPair(); query();
    const signal = routeFetch.mock.calls[0][1]?.signal;
    clearBinding("活动乙");
    expect(signal?.aborted).toBe(true);
    await act(async () => { pending.resolve(new Response(JSON.stringify(routeResult))); });
    expect(currentLines()).toHaveLength(0);
    expect(within(segment()).queryByText(/1\.25\s*公里/)).toBeNull();
    expect(within(activity("活动甲")).getByText(places[0].name)).toBeTruthy();
  });

  it("aborts across date switches, keeps bindings date-local and rejects a late response on returning to the same day", async () => {
    const pending = deferred<Response>();
    routeFetch.mockReturnValueOnce(pending.promise);
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await bindPair(); query();
    const signal = routeFetch.mock.calls[0][1]?.signal;
    fireEvent.click(screen.getByRole("button", { name: /Day 2/ }));
    expect(signal?.aborted).toBe(true);
    await bindPair();
    routeFetch.mockResolvedValueOnce(new Response(JSON.stringify(otherResult)));
    query(); await expectSuccess();
    await act(async () => { pending.resolve(new Response(JSON.stringify(routeResult))); });
    expect(within(segment()).getByText(/520\s*米/)).toBeTruthy();
    expect(within(segment()).queryByText(/1\.25\s*公里/)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /Day 1/ }));
    expect(currentLines()).toHaveLength(0);
    expect(within(segment()).queryByText(/520\s*米|1\.25\s*公里/)).toBeNull();
    expect(within(activity("活动甲")).getByText(places[0].name)).toBeTruthy();
    expect(within(activity("活动乙")).getByText(places[1].name)).toBeTruthy();
  });

  it("clears successful routes on day changes without removing the saved bindings", async () => {
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await bindPair(); query(); await expectSuccess();
    fireEvent.click(screen.getByRole("button", { name: /Day 2/ }));
    expect(currentLines()).toHaveLength(0);
    expect(within(segment()).queryByText(/1\.25\s*公里/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Day 1/ }));
    expect(within(activity("活动甲")).getByText(places[0].name)).toBeTruthy();
    expect(within(segment()).queryByText(/1\.25\s*公里/)).toBeNull();
    expect(routeFetch).toHaveBeenCalledTimes(1);
  });

  it("aborts old routes when editing/regenerating the real planner and ignores their late response", async () => {
    const pending = deferred<Response>();
    routeFetch.mockReturnValueOnce(pending.promise);
    render(<TripPlanner><p>测试说明</p></TripPlanner>);
    fireEvent.click(screen.getByRole("button", { name: "测试生成行程" }));
    await bindPair(); query();
    const signal = routeFetch.mock.calls[0][1]?.signal;
    fireEvent.click(screen.getByRole("button", { name: "修改旅行需求" }));
    expect(signal?.aborted).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "测试生成行程" }));
    await bindPair();
    routeFetch.mockResolvedValueOnce(new Response(JSON.stringify(otherResult)));
    query(); await expectSuccess();
    await act(async () => { pending.resolve(new Response(JSON.stringify(routeResult))); });
    expect(within(segment()).getByText(/520\s*米/)).toBeTruthy();
    expect(within(segment()).queryByText(/1\.25\s*公里/)).toBeNull();
  });

  it("destroys a displayed route on full unmount and starts with no bindings or route after remount", async () => {
    const view = render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await bindPair(); query(); await expectSuccess();
    const oldLines = [...currentLines()];
    view.unmount();
    expect(currentLines()).toHaveLength(0);
    expect(oldLines[0].setMap).toHaveBeenCalledWith(null);
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    expect(within(segment()).getByText(/请先为前后两个相邻活动确认绑定地点/)).toBeTruthy();
    expect(within(segment()).queryByText(/1\.25\s*公里/)).toBeNull();
    expect(routeFetch).toHaveBeenCalledTimes(1);
  });

  it("aborts an unfinished route on full unmount without displaying its late response in a remounted result", async () => {
    const pending = deferred<Response>();
    routeFetch.mockReturnValueOnce(pending.promise);
    const view = render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await bindPair(); query();
    const signal = routeFetch.mock.calls[0][1]?.signal;
    view.unmount();
    expect(signal?.aborted).toBe(true);
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await act(async () => { pending.resolve(new Response(JSON.stringify(routeResult))); });
    expect(currentLines()).toHaveLength(0);
    expect(within(segment()).queryByText(/1\.25\s*公里/)).toBeNull();
    expect(within(segment()).getByText(/请先为前后两个相邻活动确认绑定地点/)).toBeTruthy();
  });
});
