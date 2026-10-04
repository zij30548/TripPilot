import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import TripPlanResult from "@/components/trip-plan-result";
import type { AMapSDK } from "@/lib/amap-loader";
import type { Place } from "@/types/place";
import type { TripPlan } from "@/types/trip";

const sdkLoader = vi.hoisted(() => ({ loadAMap: vi.fn(), getShanghaiCenter: vi.fn() }));
vi.mock("@/lib/amap-loader", () => sdkLoader);

// These names, coordinates, costs and dates are fictional offline test fixtures.
const places: Place[] = [
  { id: "binding-test-poi-one", name: "测试 POI 一", address: "测试地址一", category: "测试类别", longitude: 120, latitude: 30, source: "amap" },
  { id: "binding-test-poi-two", name: "测试 POI 二", address: null, category: null, longitude: 121, latitude: 31, source: "amap" },
];

function makePlan(): TripPlan {
  return {
    destination: "上海",
    request: {
      start_date: "2026-10-10", end_date: "2026-10-11", budget: 3000, travelers: 2,
      accommodation_location: "测试住宿区域", pace: "balanced", interests: [],
      must_visit: [], avoid_places: [], daily_start_time: "09:00", daily_end_time: "21:00",
    },
    budget_breakdown: { transport: 0, food: 20, tickets: 0, other: 10 },
    estimated_cost: 30, currency: "CNY", is_mock: true, notice: "测试 Mock 行程说明。",
    days: [
      {
        day: 1, date: "2026-10-10", title: "测试第一天", transports: [],
        activities: [
          { id: "shared-activity", name: "测试午餐", category: "food", estimated_cost: 10, description: "测试第一天午餐说明。", start_time: "12:00", end_time: "13:00" },
          { id: "other-activity", name: "测试漫步", category: "sightseeing", estimated_cost: 10, description: "测试漫步说明。", start_time: "14:00", end_time: "15:00" },
        ],
        weather: { date: "2026-10-10", condition: "测试多云", min_temperature: 20, max_temperature: 25, rain_risk: 10 },
      },
      {
        day: 2, date: "2026-10-11", title: "测试第二天", transports: [],
        // Name and id intentionally repeat across days: they are not a binding key alone.
        activities: [
          { id: "shared-activity", name: "测试午餐", category: "food", estimated_cost: 10, description: "测试第二天午餐说明。", start_time: "12:00", end_time: "13:00" },
        ],
        weather: { date: "2026-10-11", condition: "测试小雨", min_temperature: 19, max_temperature: 24, rain_risk: 30 },
      },
    ],
  };
}

function freezePlan(plan: TripPlan): TripPlan {
  function freeze(value: unknown): void {
    if (typeof value !== "object" || value === null || Object.isFrozen(value)) return;
    Object.values(value).forEach(freeze);
    Object.freeze(value);
  }
  freeze(plan);
  return plan;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => { resolve = resolvePromise; });
  return { promise, resolve };
}

const maps: MockMap[] = [];
const markers: MockMarker[] = [];

class MockMap {
  destroyed = false;
  setFitView = vi.fn();
  setZoomAndCenter = vi.fn();
  destroy = vi.fn(() => { this.destroyed = true; });

  constructor(readonly container: HTMLElement, readonly options: Record<string, unknown>) {
    maps.push(this);
  }
}

class MockMarker {
  attached = true;
  private handlers = new Map<string, () => void>();
  on = vi.fn((name: string, handler: () => void) => { this.handlers.set(name, handler); });
  off = vi.fn((name: string, handler: () => void) => {
    if (this.handlers.get(name) === handler) this.handlers.delete(name);
  });
  setMap = vi.fn((map: MockMap | null) => { this.attached = map !== null; });
  setzIndex = vi.fn();

  constructor(readonly options: { position: [number, number]; title: string; content: HTMLElement; map: MockMap }) {
    markers.push(this);
  }

  click() {
    this.handlers.get("click")?.();
  }
}

const sdk = { Map: MockMap, Marker: MockMarker } as unknown as AMapSDK;
let fetchMock: ReturnType<typeof vi.fn<typeof fetch>>;

beforeEach(() => {
  maps.length = 0;
  markers.length = 0;
  sdkLoader.loadAMap.mockReset().mockResolvedValue(sdk);
  sdkLoader.getShanghaiCenter.mockReset().mockResolvedValue([120, 30]);
  fetchMock = vi.fn<typeof fetch>(() => Promise.reject(new Error("Unexpected unmocked network request")));
  vi.stubGlobal("fetch", fetchMock);
});

function activity(name: string) {
  return screen.getByRole("article", { name });
}

function beginBinding(name = "测试午餐", replacing = false) {
  fireEvent.click(within(activity(name)).getByRole("button", {
    name: replacing ? `为${name}更换地点` : `为${name}绑定地点`,
  }));
}

function submitSearch(keyword = "测试搜索关键词") {
  fireEvent.change(screen.getByRole("textbox", { name: "搜索上海地点" }), { target: { value: keyword } });
  fireEvent.click(screen.getByRole("button", { name: "搜索" }));
}

async function candidate(place: Place) {
  const list = await screen.findByRole("list", { name: "地点搜索结果" });
  return within(list).getByRole("button", { name: new RegExp(place.name) });
}

function liveMarkers() {
  return markers.filter((marker) => marker.attached && !marker.options.map.destroyed);
}

function currentMap() {
  const map = maps.filter((item) => !item.destroyed).at(-1);
  if (!map) throw new Error("Expected a live map");
  return map;
}

async function bind(name: string, place: Place, replacing = false) {
  beginBinding(name, replacing);
  submitSearch();
  fireEvent.click(await candidate(place));
  fireEvent.click(screen.getByRole("button", { name: "确认绑定" }));
  await waitFor(() => expect(within(activity(name)).getByText(place.name)).toBeTruthy());
}

function showBoundPlaces() {
  fireEvent.click(screen.getByRole("button", { name: "当天已绑定地点" }));
}

describe("TripPlanResult explicit activity-to-POI binding", () => {
  it("requires search, candidate selection and a separate confirmation before binding", async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify(places)));
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await waitFor(() => expect(maps).toHaveLength(1));

    expect(fetchMock).not.toHaveBeenCalled();
    beginBinding();
    expect(fetchMock).not.toHaveBeenCalled();
    expect((screen.getByRole("button", { name: "确认绑定" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/Day\s*1\s*·\s*测试午餐/)).toBeTruthy();

    submitSearch();
    const first = await candidate(places[0]);
    expect(first.getAttribute("aria-pressed")).toBe("false");
    expect((screen.getByRole("button", { name: "确认绑定" }) as HTMLButtonElement).disabled).toBe(true);
    expect(within(activity("测试午餐")).queryByText(places[0].name)).toBeNull();
    await waitFor(() => expect(liveMarkers()).toHaveLength(2));

    act(() => { liveMarkers()[0].click(); });
    expect(first.getAttribute("aria-pressed")).toBe("true");
    expect(within(activity("测试午餐")).queryByText(places[0].name)).toBeNull();
    expect(within(activity("测试漫步")).queryByText(places[0].name)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "确认绑定" }));

    expect(within(activity("测试午餐")).getByText(places[0].name)).toBeTruthy();
    expect(within(activity("测试午餐")).getByText("来源：高德地图")).toBeTruthy();
    expect(within(activity("测试午餐")).getByRole("button", { name: "为测试午餐更换地点" })).toBeTruthy();
    expect(within(activity("测试漫步")).queryByText(places[0].name)).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("replaces and removes only the selected activity binding", async () => {
    fetchMock.mockImplementation(() => Promise.resolve(new Response(JSON.stringify(places))));
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await bind("测试午餐", places[0]);
    await bind("测试漫步", places[0]);
    await bind("测试午餐", places[1], true);

    expect(within(activity("测试午餐")).queryByText(places[0].name)).toBeNull();
    expect(within(activity("测试午餐")).getByText(places[1].name)).toBeTruthy();
    expect(within(activity("测试漫步")).getByText(places[0].name)).toBeTruthy();

    fireEvent.click(within(activity("测试午餐")).getByRole("button", { name: "解除测试午餐的地点绑定" }));
    expect(within(activity("测试午餐")).queryByText(places[1].name)).toBeNull();
    expect(within(activity("测试午餐")).getByRole("button", { name: "为测试午餐绑定地点" })).toBeTruthy();
    expect(within(activity("测试漫步")).getByText(places[0].name)).toBeTruthy();
  });

  it("keeps bindings separate across days even when activity name and id repeat", async () => {
    fetchMock.mockImplementation(() => Promise.resolve(new Response(JSON.stringify(places))));
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await bind("测试午餐", places[0]);

    fireEvent.click(screen.getByRole("button", { name: /Day 2/ }));
    expect(within(activity("测试午餐")).queryByText(places[0].name)).toBeNull();
    await bind("测试午餐", places[1]);
    expect(within(activity("测试午餐")).getByText(places[1].name)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Day 1/ }));
    expect(within(activity("测试午餐")).getByText(places[0].name)).toBeTruthy();
    expect(within(activity("测试午餐")).queryByText(places[1].name)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Day 2/ }));
    expect(within(activity("测试午餐")).getByText(places[1].name)).toBeTruthy();
  });

  it("retains a confirmed binding when replacement search fails, returns empty or is cancelled", async () => {
    fetchMock
      .mockResolvedValueOnce(new Response(JSON.stringify(places)))
      .mockResolvedValueOnce(new Response("private upstream detail", { status: 504 }))
      .mockResolvedValueOnce(new Response("[]"));
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await bind("测试午餐", places[0]);
    beginBinding("测试午餐", true);
    submitSearch("失败搜索");
    expect(await screen.findByText(/地点搜索超时/)).toBeTruthy();
    expect(screen.queryByText("private upstream detail")).toBeNull();
    expect(within(activity("测试午餐")).getByText(places[0].name)).toBeTruthy();
    expect((screen.getByRole("button", { name: "确认绑定" }) as HTMLButtonElement).disabled).toBe(true);

    submitSearch("空结果搜索");
    expect(await screen.findByText("没有找到匹配地点，请更换关键词。")).toBeTruthy();
    expect(within(activity("测试午餐")).getByText(places[0].name)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "取消绑定" }));
    expect(within(activity("测试午餐")).getByText(places[0].name)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "确认绑定" })).toBeNull();
  });

  it("starts a fresh candidate session when reopening the same activity after cancellation or another target", async () => {
    fetchMock.mockImplementation(() => Promise.resolve(new Response(JSON.stringify(places))));
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    beginBinding("测试午餐");
    submitSearch();
    fireEvent.click(await candidate(places[0]));
    expect((screen.getByRole("button", { name: "确认绑定" }) as HTMLButtonElement).disabled).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "取消绑定" }));
    beginBinding("测试午餐");
    expect(screen.queryByRole("list", { name: "地点搜索结果" })).toBeNull();
    expect((screen.getByRole("button", { name: "确认绑定" }) as HTMLButtonElement).disabled).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    submitSearch();
    fireEvent.click(await candidate(places[1]));
    beginBinding("测试漫步");
    beginBinding("测试午餐");
    expect(screen.queryByRole("list", { name: "地点搜索结果" })).toBeNull();
    expect((screen.getByRole("button", { name: "确认绑定" }) as HTMLButtonElement).disabled).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(within(activity("测试午餐")).queryByText(places[0].name)).toBeNull();
    expect(within(activity("测试午餐")).queryByText(places[1].name)).toBeNull();
  });

  it("aborts an old target search and cannot attach its late results to another activity", async () => {
    const pending = deferred<Response>();
    fetchMock.mockReturnValueOnce(pending.promise).mockResolvedValueOnce(new Response(JSON.stringify([places[1]])));
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    beginBinding("测试午餐");
    submitSearch("旧活动关键词");
    const signal = fetchMock.mock.calls[0][1]?.signal;
    beginBinding("测试漫步");
    expect(signal?.aborted).toBe(true);
    expect((screen.getByRole("button", { name: "确认绑定" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByRole("list", { name: "地点搜索结果" })).toBeNull();
    await act(async () => { pending.resolve(new Response(JSON.stringify([places[0]]))); });
    expect(screen.queryByRole("list", { name: "地点搜索结果" })).toBeNull();

    submitSearch("新活动关键词");
    fireEvent.click(await candidate(places[1]));
    fireEvent.click(screen.getByRole("button", { name: "确认绑定" }));
    expect(within(activity("测试漫步")).getByText(places[1].name)).toBeTruthy();
    expect(within(activity("测试午餐")).queryByText(places[1].name)).toBeNull();
    expect(within(activity("测试漫步")).queryByText(places[0].name)).toBeNull();
  });

  it("ignores a cancelled search arriving after the same activity is reopened", async () => {
    const pending = deferred<Response>();
    fetchMock.mockReturnValueOnce(pending.promise).mockResolvedValueOnce(new Response(JSON.stringify([places[1]])));
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    beginBinding("测试午餐");
    submitSearch("取消前关键词");
    const oldSignal = fetchMock.mock.calls[0][1]?.signal;
    fireEvent.click(screen.getByRole("button", { name: "取消绑定" }));
    expect(oldSignal?.aborted).toBe(true);
    beginBinding("测试午餐");
    expect((screen.getByRole("button", { name: "确认绑定" }) as HTMLButtonElement).disabled).toBe(true);

    await act(async () => { pending.resolve(new Response(JSON.stringify([places[0]]))); });
    expect(screen.queryByRole("list", { name: "地点搜索结果" })).toBeNull();
    expect((screen.getByRole("button", { name: "确认绑定" }) as HTMLButtonElement).disabled).toBe(true);
    expect(within(activity("测试午餐")).queryByText(places[0].name)).toBeNull();
    submitSearch("重新进入后的关键词");
    fireEvent.click(await candidate(places[1]));
    fireEvent.click(screen.getByRole("button", { name: "确认绑定" }));
    expect(within(activity("测试午餐")).getByText(places[1].name)).toBeTruthy();
  });

  it("does not reuse pending A or B responses when the target changes A → B → A", async () => {
    const oldA = deferred<Response>();
    const oldB = deferred<Response>();
    const currentA = deferred<Response>();
    fetchMock.mockReturnValueOnce(oldA.promise).mockReturnValueOnce(oldB.promise).mockReturnValueOnce(currentA.promise);
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    beginBinding("测试午餐");
    submitSearch("旧 A 搜索");
    const signalA = fetchMock.mock.calls[0][1]?.signal;
    beginBinding("测试漫步");
    submitSearch("旧 B 搜索");
    const signalB = fetchMock.mock.calls[1][1]?.signal;
    beginBinding("测试午餐");
    expect(signalA?.aborted).toBe(true);
    expect(signalB?.aborted).toBe(true);
    submitSearch("新 A 搜索");

    await act(async () => { currentA.resolve(new Response(JSON.stringify([places[1]]))); });
    fireEvent.click(await candidate(places[1]));
    await act(async () => {
      oldB.resolve(new Response(JSON.stringify([places[0]])));
      oldA.resolve(new Response(JSON.stringify([places[0]])));
    });
    expect(within(screen.getByRole("list", { name: "地点搜索结果" })).queryByRole("button", { name: new RegExp(places[0].name) })).toBeNull();
    expect((screen.getByRole("button", { name: "确认绑定" }) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "确认绑定" }));
    expect(within(activity("测试午餐")).getByText(places[1].name)).toBeTruthy();
    expect(within(activity("测试漫步")).queryByText(places[1].name)).toBeNull();
  });

  it("invalidates the selected replacement before a subsequent failed search", async () => {
    fetchMock
      .mockResolvedValueOnce(new Response(JSON.stringify([places[0]])))
      .mockResolvedValueOnce(new Response(JSON.stringify([places[1]])))
      .mockResolvedValueOnce(new Response("private response", { status: 502 }));
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await bind("测试午餐", places[0]);
    beginBinding("测试午餐", true);
    submitSearch("替换候选");
    fireEvent.click(await candidate(places[1]));
    expect((screen.getByRole("button", { name: "确认绑定" }) as HTMLButtonElement).disabled).toBe(false);
    submitSearch("失败的新查询");
    expect((screen.getByRole("button", { name: "确认绑定" }) as HTMLButtonElement).disabled).toBe(true);
    await screen.findByRole("alert");
    expect(screen.queryByRole("list", { name: "地点搜索结果" })).toBeNull();
    expect(within(activity("测试午餐")).getByText(places[0].name)).toBeTruthy();
    expect(within(activity("测试午餐")).queryByText(places[1].name)).toBeNull();
    expect(screen.queryByText("private response")).toBeNull();
  });

  it("clears selected candidates and aborts pending search on a date switch while preserving bindings", async () => {
    const pending = deferred<Response>();
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify(places))).mockReturnValueOnce(pending.promise);
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await bind("测试午餐", places[0]);
    beginBinding("测试漫步");
    submitSearch("切换前关键词");
    const signal = fetchMock.mock.calls[1][1]?.signal;

    fireEvent.click(screen.getByRole("button", { name: /Day 2/ }));
    expect(signal?.aborted).toBe(true);
    expect(screen.queryByRole("button", { name: "确认绑定" })).toBeNull();
    expect(screen.queryByRole("list", { name: "地点搜索结果" })).toBeNull();
    await act(async () => { pending.resolve(new Response(JSON.stringify([places[1]]))); });
    expect(screen.queryByRole("list", { name: "地点搜索结果" })).toBeNull();
    expect(within(activity("测试午餐")).queryByText(places[1].name)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /Day 1/ }));
    expect(within(activity("测试午餐")).getByText(places[0].name)).toBeTruthy();
    expect(within(activity("测试漫步")).queryByText(places[1].name)).toBeNull();
  });

  it("connects bound activity map viewing, marker selection and fit-all without another search", async () => {
    const scroll = vi.spyOn(HTMLElement.prototype, "scrollIntoView").mockImplementation(() => undefined);
    fetchMock.mockImplementation(() => Promise.resolve(new Response(JSON.stringify(places))));
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await bind("测试午餐", places[0]);
    await bind("测试漫步", places[1]);
    showBoundPlaces();
    await waitFor(() => expect(liveMarkers()).toHaveLength(2));
    scroll.mockClear();
    fireEvent.click(within(activity("测试午餐")).getByRole("button", { name: "在地图查看测试午餐" }));
    await waitFor(() => expect(currentMap().setZoomAndCenter).toHaveBeenLastCalledWith(16, [120, 30]));
    expect(liveMarkers().find((marker) => marker.options.title === places[0].name)?.setzIndex).toHaveBeenLastCalledWith(200);

    scroll.mockClear();
    const secondMarker = liveMarkers().find((marker) => marker.options.title === places[1].name);
    expect(secondMarker).toBeTruthy();
    act(() => { secondMarker!.click(); });
    await waitFor(() => expect(currentMap().setZoomAndCenter).toHaveBeenLastCalledWith(16, [121, 31]));
    expect(secondMarker!.setzIndex).toHaveBeenLastCalledWith(200);
    expect(scroll.mock.contexts).toContain(activity("测试漫步"));

    currentMap().setFitView.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "查看当天全部地点" }));
    expect(currentMap().setFitView).toHaveBeenCalledTimes(1);
    expect(currentMap().setFitView.mock.calls[0][0]).toEqual(liveMarkers());
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("uses one bound marker for a shared POI, retains both activity bindings and never changes the Mock plan", async () => {
    const plan = freezePlan(makePlan());
    const original = JSON.stringify(plan);
    const scroll = vi.spyOn(HTMLElement.prototype, "scrollIntoView").mockImplementation(() => undefined);
    fetchMock.mockImplementation(() => Promise.resolve(new Response(JSON.stringify([places[0]]))));
    render(<TripPlanResult plan={plan} onEdit={vi.fn()} />);
    await bind("测试午餐", places[0]);
    await bind("测试漫步", places[0]);
    showBoundPlaces();
    await waitFor(() => expect(liveMarkers()).toHaveLength(1));
    expect(within(activity("测试午餐")).getByText(places[0].name)).toBeTruthy();
    expect(within(activity("测试漫步")).getByText(places[0].name)).toBeTruthy();

    fireEvent.click(within(activity("测试漫步")).getByRole("button", { name: "在地图查看测试漫步" }));
    scroll.mockClear();
    act(() => { liveMarkers()[0].click(); });
    expect(scroll.mock.contexts).toContain(activity("测试漫步"));
    fireEvent.click(within(activity("测试漫步")).getByRole("button", { name: "解除测试漫步的地点绑定" }));
    expect(within(activity("测试午餐")).getByText(places[0].name)).toBeTruthy();
    expect(within(activity("测试漫步")).queryByText(places[0].name)).toBeNull();
    await waitFor(() => expect(liveMarkers()).toHaveLength(1));
    scroll.mockClear();
    act(() => { liveMarkers()[0].click(); });
    expect(scroll.mock.contexts).toContain(activity("测试午餐"));
    expect(JSON.stringify(plan)).toBe(original);
  });

  it("centers the marker from an accessible activity-title button without changing the binding flow", async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify([places[0]])));
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await bind("测试午餐", places[0]);
    await waitFor(() => expect(liveMarkers()).toHaveLength(1));
    currentMap().setZoomAndCenter.mockClear();
    fireEvent.click(within(activity("测试午餐")).getByRole("button", { name: "在地图定位测试午餐" }));
    expect(currentMap().setZoomAndCenter).toHaveBeenLastCalledWith(16, [120, 30]);
    expect(liveMarkers()[0].options.content.style.background).toBe("rgb(189, 76, 53)");

    beginBinding("测试午餐", true);
    expect(screen.getByRole("textbox", { name: "搜索上海地点" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "确认绑定" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "搜索候选地点" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("keeps another day's shared POI binding when the same POI is removed on this day", async () => {
    fetchMock.mockImplementation(() => Promise.resolve(new Response(JSON.stringify([places[0]]))));
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await bind("测试午餐", places[0]);
    fireEvent.click(screen.getByRole("button", { name: /Day 2/ }));
    await bind("测试午餐", places[0]);
    await waitFor(() => expect(liveMarkers()).toHaveLength(1));
    fireEvent.click(within(activity("测试午餐")).getByRole("button", { name: "解除测试午餐的地点绑定" }));
    await waitFor(() => expect(liveMarkers()).toHaveLength(0));
    fireEvent.click(screen.getByRole("button", { name: /Day 1/ }));
    expect(within(activity("测试午餐")).getByText(places[0].name)).toBeTruthy();
    showBoundPlaces();
    await waitFor(() => expect(liveMarkers()).toHaveLength(1));
  });

  it("allows an explicit card binding while map loading is unavailable", async () => {
    sdkLoader.loadAMap.mockRejectedValue(new Error("测试地图不可用"));
    fetchMock.mockResolvedValue(new Response(JSON.stringify([places[0]])));
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    expect(await screen.findByText("测试地图不可用")).toBeTruthy();
    await bind("测试午餐", places[0]);
    expect(within(activity("测试午餐")).getByText(places[0].name)).toBeTruthy();
    expect(maps).toHaveLength(0);
  });

  it("clears all local bindings when results are unmounted and rebuilt for a new plan", async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify([places[0]])));
    const view = render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await bind("测试午餐", places[0]);
    const oldMaps = [...maps];
    view.unmount();
    for (const map of oldMaps) expect(map.destroy).toHaveBeenCalledTimes(1);

    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    expect(within(activity("测试午餐")).queryByText(places[0].name)).toBeNull();
    expect(within(activity("测试午餐")).getByRole("button", { name: "为测试午餐绑定地点" })).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: "确认绑定" })).toBeNull();
  });
});
