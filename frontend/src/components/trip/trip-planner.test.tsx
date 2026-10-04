import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import TripPlanner from "@/components/trip/trip-planner";
import type { AMapSDK } from "@/lib/amap-loader";
import type { Place } from "@/types/place";
import type { TripPlan } from "@/types/trip";

const controls = vi.hoisted(() => ({
  nextPlan: null as TripPlan | null,
  loadAMap: vi.fn(),
  getShanghaiCenter: vi.fn(),
}));

// Exercise the real planner/result lifecycle without duplicating form validation
// tests or making HTTP requests to the trip-planning service.
vi.mock("@/components/trip-request-form", () => ({
  default: ({ onSuccess }: { onSuccess: (plan: TripPlan) => void }) => (
    <section aria-label="测试旅行需求表单">
      <input aria-label="测试需求" defaultValue="保留的需求" />
      <button type="button" onClick={() => {
        if (controls.nextPlan) onSuccess(controls.nextPlan);
      }}>测试生成行程</button>
    </section>
  ),
}));

vi.mock("@/lib/amap-loader", () => ({
  loadAMap: controls.loadAMap,
  getShanghaiCenter: controls.getShanghaiCenter,
}));

// Only fictional fixture POIs are used; the global fetch guard prevents real calls.
const firstPlace: Place = {
  id: "fixture-place-one", name: "测试绑定地点一", address: "测试地址一",
  category: "测试类别", longitude: 120, latitude: 30, source: "amap",
};
const secondPlace: Place = {
  id: "fixture-place-two", name: "测试绑定地点二", address: "测试地址二",
  category: "测试类别", longitude: 121, latitude: 31, source: "amap",
};

function makePlan(): TripPlan {
  return {
    destination: "上海", estimated_cost: 50, currency: "CNY", is_mock: true,
    notice: "测试 Mock 行程，真实地点绑定不会验证费用、交通和天气。",
    request: {
      start_date: "2026-10-10", end_date: "2026-10-11", budget: 3000,
      travelers: 2, accommodation_location: "测试住宿", pace: "balanced",
      interests: [], must_visit: [], avoid_places: [],
      daily_start_time: "09:00", daily_end_time: "21:00",
    },
    budget_breakdown: { transport: 10, food: 20, tickets: 20, other: 0 },
    days: [1, 2].map((day) => ({
      day, date: `2026-10-${day === 1 ? "10" : "11"}`, title: `测试第${day}天`,
      // Intentionally reuse names and ids on different dates.
      activities: [{
        id: "shared-activity-id", name: "测试同名活动", category: "sightseeing",
        estimated_cost: 20, description: "保留原始 Mock 活动描述。",
        start_time: "09:00", end_time: "10:00",
      }],
      transports: [],
      weather: {
        date: `2026-10-${day === 1 ? "10" : "11"}`, condition: "Mock 晴",
        min_temperature: 18, max_temperature: 25, rain_risk: 10,
      },
    })),
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => { resolve = resolvePromise; });
  return { promise, resolve };
}

const maps: MockMap[] = [];
const markers: MockMarker[] = [];

class MockMap {
  setFitView = vi.fn();
  setZoomAndCenter = vi.fn();
  destroy = vi.fn();
  constructor() { maps.push(this); }
}

class MockMarker {
  on = vi.fn();
  off = vi.fn();
  setMap = vi.fn();
  setzIndex = vi.fn();
  constructor(readonly options: { title: string; map: MockMap }) { markers.push(this); }
}

let fetchMock: ReturnType<typeof vi.fn<typeof fetch>>;

beforeEach(() => {
  controls.nextPlan = makePlan();
  controls.loadAMap.mockReset().mockResolvedValue({ Map: MockMap, Marker: MockMarker } as unknown as AMapSDK);
  controls.getShanghaiCenter.mockReset().mockResolvedValue([120, 30]);
  maps.length = 0;
  markers.length = 0;
  fetchMock = vi.fn<typeof fetch>();
  vi.stubGlobal("fetch", fetchMock);
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    callback(0);
    return 1;
  });
});

function generate() {
  fireEvent.click(screen.getByRole("button", { name: "测试生成行程" }));
}

function beginBinding() {
  fireEvent.click(screen.getByRole("button", { name: "为测试同名活动绑定地点" }));
}

function search(keyword = "测试关键词") {
  fireEvent.change(screen.getByRole("textbox", { name: "搜索上海地点" }), { target: { value: keyword } });
  fireEvent.click(screen.getByRole("button", { name: "搜索" }));
}

async function bind(place = firstPlace) {
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify([place])));
  beginBinding();
  search();
  fireEvent.click(await screen.findByRole("button", { name: new RegExp(place.name) }));
  fireEvent.click(screen.getByRole("button", { name: "确认绑定" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "在地图查看测试同名活动" })).toBeTruthy());
}

function activityCard() {
  return screen.getByRole("article", { name: "测试同名活动" });
}

describe("TripPlanner result-local binding lifetime", () => {
  it("clears confirmed bindings on edit and regeneration, preserves the form and leaves the Mock TripPlan unchanged", async () => {
    const original = JSON.stringify(controls.nextPlan);
    render(<TripPlanner><p>旅行需求说明</p></TripPlanner>);
    generate();
    await bind();
    expect(within(activityCard()).getByText(firstPlace.name)).toBeTruthy();
    expect(JSON.stringify(controls.nextPlan)).toBe(original);

    fireEvent.click(screen.getByRole("button", { name: "修改旅行需求" }));
    expect(screen.queryByRole("article", { name: "测试同名活动" })).toBeNull();
    expect((screen.getByRole("textbox", { name: "测试需求" }) as HTMLInputElement).value).toBe("保留的需求");
    // The same plan instance must still start a clean result-local state.
    generate();
    expect(within(activityCard()).queryByText(firstPlace.name)).toBeNull();
    expect(screen.queryByRole("button", { name: "在地图查看测试同名活动" })).toBeNull();
    expect(screen.queryByRole("button", { name: "解除测试同名活动的地点绑定" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Day 2/ }));
    expect(within(activityCard()).queryByText(firstPlace.name)).toBeNull();
    expect(JSON.stringify(controls.nextPlan)).toBe(original);
  });

  it("aborts a pending replacement on edit and ignores its late response after a new plan/search starts", async () => {
    render(<TripPlanner><p>旅行需求说明</p></TripPlanner>);
    generate();
    await bind();

    const pending = deferred<Response>();
    fetchMock.mockReturnValueOnce(pending.promise);
    fireEvent.click(screen.getByRole("button", { name: "为测试同名活动更换地点" }));
    search("旧行程慢查询");
    const oldSignal = fetchMock.mock.calls[1][1]?.signal;
    expect(oldSignal?.aborted).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "修改旅行需求" }));
    expect(oldSignal?.aborted).toBe(true);
    controls.nextPlan = makePlan();
    generate();
    expect(screen.queryByRole("button", { name: "在地图查看测试同名活动" })).toBeNull();

    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify([secondPlace])));
    beginBinding();
    search("新行程查询");
    const newCandidate = await screen.findByRole("button", { name: new RegExp(secondPlace.name) });
    expect((screen.getByRole("button", { name: "确认绑定" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(newCandidate);

    await act(async () => { pending.resolve(new Response(JSON.stringify([firstPlace]))); });
    expect(screen.queryByRole("button", { name: new RegExp(firstPlace.name) })).toBeNull();
    expect(newCandidate.getAttribute("aria-pressed")).toBe("true");
    expect(within(activityCard()).queryByText(firstPlace.name)).toBeNull();
    expect(within(activityCard()).queryByText(secondPlace.name)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "确认绑定" }));
    expect(within(activityCard()).getByText(secondPlace.name)).toBeTruthy();
    expect(within(activityCard()).queryByText(firstPlace.name)).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("remounts with no bindings after a refresh-equivalent full unmount and destroys old map state", async () => {
    const firstView = render(<TripPlanner><p>旅行需求说明</p></TripPlanner>);
    generate();
    await bind();
    await waitFor(() => expect(markers.some((marker) => marker.options.title === firstPlace.name)).toBe(true));
    const oldMaps = [...maps];
    const oldMarkers = [...markers];

    firstView.unmount();
    for (const map of oldMaps) expect(map.destroy).toHaveBeenCalledTimes(1);
    for (const marker of oldMarkers) expect(marker.setMap).toHaveBeenCalledWith(null);
    render(<TripPlanner><p>旅行需求说明</p></TripPlanner>);
    expect(screen.queryByRole("article", { name: "测试同名活动" })).toBeNull();
    generate();
    await waitFor(() => expect(maps.length).toBeGreaterThan(oldMaps.length));
    expect(within(activityCard()).queryByText(firstPlace.name)).toBeNull();
    expect(screen.queryByRole("button", { name: "在地图查看测试同名活动" })).toBeNull();
    expect(screen.queryByRole("list", { name: "地点搜索结果" })).toBeNull();
    expect(markers).toHaveLength(oldMarkers.length);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not leak an unfinished old query into the form or the next result when it resolves before regeneration", async () => {
    const pending = deferred<Response>();
    fetchMock.mockReturnValueOnce(pending.promise);
    render(<TripPlanner><p>旅行需求说明</p></TripPlanner>);
    generate();
    beginBinding();
    search();
    const signal = fetchMock.mock.calls[0][1]?.signal;
    fireEvent.click(screen.getByRole("button", { name: "修改旅行需求" }));
    expect(signal?.aborted).toBe(true);

    await act(async () => { pending.resolve(new Response(JSON.stringify([firstPlace]))); });
    expect(screen.queryByRole("list", { name: "地点搜索结果" })).toBeNull();
    generate();
    beginBinding();
    expect(screen.queryByRole("list", { name: "地点搜索结果" })).toBeNull();
    expect((screen.getByRole("button", { name: "确认绑定" }) as HTMLButtonElement).disabled).toBe(true);
    expect(within(activityCard()).queryByText(firstPlace.name)).toBeNull();
  });
});
