import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import TripPlanResult from "@/components/trip-plan-result";
import TripPlanner from "@/components/trip/trip-planner";
import type { AMapSDK } from "@/lib/amap-loader";
import { CandidateError } from "@/lib/candidates-api";
import type { CandidateResponse } from "@/types/candidates";
import type { Place } from "@/types/place";
import type { TripPlan, TripRequest } from "@/types/trip";

const controls = vi.hoisted(() => ({
  queryCandidates: vi.fn(), searchPlaces: vi.fn(), planTrip: vi.fn(),
  loadAMap: vi.fn(), getShanghaiCenter: vi.fn(),
}));
vi.mock("@/lib/candidates-api", async (original) => ({
  ...await original<typeof import("@/lib/candidates-api")>(), queryCandidates: controls.queryCandidates,
}));
vi.mock("@/lib/places-api", () => ({ searchPlaces: controls.searchPlaces }));
vi.mock("@/lib/api", () => ({ planTrip: controls.planTrip }));
vi.mock("@/lib/amap-loader", () => ({ loadAMap: controls.loadAMap, getShanghaiCenter: controls.getShanghaiCenter }));

// Fictional test snapshots only. HTTP and SDK are mocked; no real AMap calls.
const lodging: Place = {
  id: "candidate-fixture-lodging", name: "测试住宿参考", address: "测试住宿地址", category: "测试地标",
  longitude: 120, latitude: 30, source: "amap",
};
const required: Place = {
  id: "candidate-fixture-required", name: "测试必去地点", address: "测试必去地址", category: "测试景点",
  longitude: 120.01, latitude: 30.01, source: "amap",
};
const optional: Place = {
  id: "candidate-fixture-optional", name: "测试可选地点", address: "测试可选地址甲", category: "测试公园",
  longitude: 120.02, latitude: 30.02, source: "amap",
};
const other: Place = { ...optional, id: "candidate-fixture-other", address: "测试可选地址乙", longitude: 120.03, latitude: 30.03 };
const replacement: Place = { ...optional, id: "candidate-fixture-new", name: "测试新批次地点", longitude: 120.04, latitude: 30.04 };

function request(): TripRequest {
  return {
    start_date: "2026-10-10", end_date: "2026-10-11", budget: 3000, travelers: 2,
    accommodation_location: lodging.name, accommodation_place: lodging,
    must_visit: [required.name], must_visit_places: [required], pace: "balanced",
    interests: ["摄影", "美食"], avoid_places: ["测试不想去的类型"], daily_start_time: "09:00", daily_end_time: "21:00",
  };
}
function makePlan(submitted = request()): TripPlan {
  return {
    destination: "上海", request: submitted, estimated_cost: 40, currency: "CNY", is_mock: true,
    notice: "测试 Mock 示例，活动时间、交通、费用和天气未按需求验证。",
    budget_breakdown: { transport: 0, food: 0, tickets: 40, other: 0 },
    days: [1, 2].map((day) => ({
      day, date: day === 1 ? "2026-10-10" : "2026-10-11", title: `测试第${day}天`,
      activities: ["甲", "乙"].map((name, index) => ({
        id: `activity-${index}`, name: `活动${name}`, category: "sightseeing" as const,
        estimated_cost: 10, description: "不可改写的 Mock 活动", start_time: "09:00", end_time: "10:00",
      })), transports: [],
      weather: { date: day === 1 ? "2026-10-10" : "2026-10-11", condition: "Mock 晴", min_temperature: 18, max_temperature: 25, rain_risk: 10 },
    })),
  };
}
function response(places: Place[] = [optional], status: CandidateResponse["status"] = "success"): CandidateResponse {
  return {
    status, queried_at: "2026-10-06T03:00:00Z", keywords: ["公园", "餐厅"],
    queries: [
      { interest: "摄影", keyword: "公园", status: status === "failed" ? "failed" : "success", result_count: status === "failed" ? 0 : places.length, message: status === "failed" ? "测试安全失败提示" : null },
      { interest: "美食", keyword: "餐厅", status: status === "success" ? "success" : "timeout", result_count: 0, message: status === "success" ? null : "测试安全超时提示" },
    ],
    candidates: [
      { place: required, role: "must_visit", retrieval_sources: [] },
      ...(status === "failed" ? [] : places.map((place) => ({ place, role: "optional" as const, retrieval_sources: [{ interest: "摄影" as const, keyword: "公园" }] }))),
    ],
  };
}
function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (reason: Error) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
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
  constructor(readonly options: { title: string; map: MockMap; content: HTMLElement }) { markers.push(this); }
  click() { this.handlers.get("click")?.(); }
}
class MockPolyline {
  currentMap: MockMap | null = null;
  setMap = vi.fn((map: MockMap | null) => { this.currentMap = map; });
  constructor() { lines.push(this); }
}
const activeLines = () => lines.filter((line) => line.currentMap && !line.currentMap.destroyed);
let walkingFetch: ReturnType<typeof vi.fn<typeof fetch>>;

beforeEach(() => {
  maps.length = 0; markers.length = 0; lines.length = 0;
  controls.queryCandidates.mockReset().mockResolvedValue(response());
  controls.searchPlaces.mockReset().mockResolvedValue([lodging, required, optional, replacement]);
  controls.planTrip.mockReset().mockImplementation(async (submitted: TripRequest) => makePlan(submitted));
  controls.loadAMap.mockReset().mockResolvedValue({ Map: MockMap, Marker: MockMarker, Polyline: MockPolyline } as unknown as AMapSDK);
  controls.getShanghaiCenter.mockReset().mockResolvedValue([120, 30]);
  walkingFetch = vi.fn<typeof fetch>().mockImplementation(async () => new Response(JSON.stringify({
    status: "ok", source: "amap", queried_at: "2026-10-06T03:00:00Z",
    route: { distance_meters: 1250, duration_seconds: 901, segments: [[[120.01, 30.01], [120.02, 30.02]]] },
  })));
  vi.stubGlobal("fetch", vi.fn<typeof fetch>((input, options) => {
    if (new URL(String(input)).pathname === "/routes/walking") return walkingFetch(input, options);
    return Promise.reject(new Error("Unexpected unmocked network request"));
  }));
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => { callback(0); return 1; });
});

function pool() { return screen.getByRole("region", { name: "候选地点准备" }); }
function click(name: string | RegExp) { fireEvent.click(screen.getByRole("button", { name })); }
function getCandidates() { fireEvent.click(within(pool()).getByRole("button", { name: /^(?:重新)?获取候选地点$/ })); }
function candidate(place: Place) {
  const name = within(pool()).getAllByText(place.name).find((node) => node.closest("li")?.textContent?.includes(place.address!));
  expect(name).toBeTruthy();
  return name!.closest("li")!;
}
async function obtain(places = [optional]) {
  getCandidates();
  await waitFor(() => {
    expect(within(pool()).getByRole("button", { name: "重新获取候选地点" })).toBeTruthy();
    for (const place of places) expect(candidate(place)).toBeTruthy();
  });
}
function search() {
  fireEvent.change(screen.getByRole("textbox", { name: "搜索上海地点" }), { target: { value: "测试地点" } });
  click("搜索");
}
async function bind(name: string, place: Place) {
  click(`为${name}绑定地点`); search();
  const list = await screen.findByRole("list", { name: "地点搜索结果" });
  fireEvent.click(within(list).getByRole("button", { name: new RegExp(`${place.name}.*${place.address}`) }));
  click("确认绑定");
}
function submitTrip() { fireEvent.submit(screen.getByRole("button", { name: "生成我的行程" }).closest("form")!); }
async function plannerResult() {
  render(<TripPlanner><p>测试旅行说明</p></TripPlanner>);
  for (const [name, value] of [[/开始日期/, "2026-10-10"], [/结束日期/, "2026-10-11"], [/总预算/, "3000"]] as const) {
    fireEvent.change(screen.getByLabelText(name), { target: { value } });
  }
  click("选择住宿参考点"); search();
  fireEvent.click(await within(screen.getByRole("region", { name: "住宿参考点选择器" })).findByRole("button", { name: new RegExp(lodging.name) }));
  click("确认住宿参考点");
  click("添加必去地点"); search();
  fireEvent.click(await within(screen.getByRole("region", { name: "必去地点选择器" })).findByRole("button", { name: new RegExp(required.name) }));
  click("确认必去地点");
  fireEvent.click(screen.getByRole("checkbox", { name: /摄影/ }));
  fireEvent.click(screen.getByRole("checkbox", { name: /美食/ }));
  submitTrip(); await screen.findByRole("button", { name: "修改旅行需求" });
}

describe("candidate preparation UI and result-local lifecycle", () => {
  it("shows confirmed must-visits before any manual query and never offers their exclusion", async () => {
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    expect(within(candidate(required)).getByText("必去")).toBeTruthy();
    expect(within(candidate(required)).queryByRole("button", { name: /排除|恢复/ })).toBeNull();
    expect(within(pool()).getByRole("button", { name: "获取候选地点" })).toBeTruthy();
    expect(pool().textContent).toMatch(/不会排程|未.*安排|不是.*行程/);
    expect(pool().textContent).toContain("测试不想去的类型");
    expect(pool().textContent).toMatch(/未.*过滤|不.*自动.*过滤/);
    await act(async () => undefined);
    expect(controls.queryCandidates).not.toHaveBeenCalled();
    expect(controls.searchPlaces).not.toHaveBeenCalled();
    expect(walkingFetch).not.toHaveBeenCalled();
  });

  it("fetches only on demand and displays true snapshot fields, provenance and the query time", async () => {
    const plan = makePlan(), original = JSON.stringify(plan);
    render(<TripPlanResult plan={plan} onEdit={vi.fn()} />);
    await obtain();
    expect(controls.queryCandidates).toHaveBeenCalledTimes(1);
    expect(controls.queryCandidates.mock.calls[0][0]).toMatchObject({ accommodation_place: lodging, must_visit_places: [required], interests: ["摄影", "美食"] });
    const card = candidate(optional);
    expect(card.textContent).toContain(optional.name);
    expect(card.textContent).toContain(optional.address);
    expect(card.textContent).toContain(optional.category);
    expect(card.textContent).toMatch(/高德/);
    expect(card.textContent).toContain("摄影"); expect(card.textContent).toContain("公园");
    expect(within(pool()).getByText(/^当前候选检索时间：/).textContent)
      .toContain(new Date(response().queried_at).toLocaleString("zh-CN", { hour12: false }));
    expect(within(candidate(required)).queryByRole("button", { name: /排除|恢复/ })).toBeNull();
    expect(JSON.stringify(plan)).toBe(original);
    expect(screen.queryByRole("button", { name: "在地图查看活动甲" })).toBeNull();
    expect(walkingFetch).not.toHaveBeenCalled();
  });

  it("does not guess coordinates for a legacy text-only lodging and explains how to enable retrieval", async () => {
    const legacy = request(); legacy.accommodation_place = null;
    render(<TripPlanResult plan={makePlan(legacy)} onEdit={vi.fn()} />);
    const button = within(pool()).getByRole("button", { name: "获取候选地点" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(pool().textContent).toMatch(/确认住宿参考点/);
    expect(pool().textContent).toMatch(/不会.*猜测坐标/);
    expect(candidate(required)).toBeTruthy(); fireEvent.click(button);
    await act(async () => undefined);
    expect(controls.queryCandidates).not.toHaveBeenCalled();
  });

  it("labels the one generic search honestly when no interests or must-visits were supplied", async () => {
    const generalRequest = { ...request(), interests: [], must_visit: [], must_visit_places: [] };
    controls.queryCandidates.mockResolvedValueOnce({
      status: "success", queried_at: response().queried_at, keywords: ["旅游景点"],
      queries: [{ interest: null, keyword: "旅游景点", status: "success", result_count: 1, message: null }],
      candidates: [{ place: optional, role: "optional", retrieval_sources: [{ interest: null, keyword: "旅游景点" }] }],
    } satisfies CandidateResponse);
    render(<TripPlanResult plan={makePlan(generalRequest)} onEdit={vi.fn()} />);
    expect(pool().textContent).toMatch(/没有已确认的必去地点/);
    await obtain();
    expect(candidate(optional).textContent).toContain("通用候选");
    expect(candidate(optional).textContent).toContain("旅游景点");
    expect(controls.queryCandidates.mock.calls[0][0]).toMatchObject({ interests: [], must_visit_places: [] });
    expect(within(pool()).queryByRole("list", { name: "候选中的必去地点" })).toBeNull();
  });

  it("keeps same-name different-ID optional places separate and excludes/restores without another query", async () => {
    controls.queryCandidates.mockResolvedValueOnce(response([optional, other]));
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await obtain([optional, other]);
    fireEvent.click(within(candidate(optional)).getByRole("button", { name: `排除可选地点：${optional.name}` }));
    expect(within(candidate(optional)).getByRole("button", { name: `恢复可选地点：${optional.name}` })).toBeTruthy();
    expect(within(candidate(other)).getByRole("button", { name: `排除可选地点：${other.name}` })).toBeTruthy();
    fireEvent.click(within(candidate(optional)).getByRole("button", { name: `恢复可选地点：${optional.name}` }));
    expect(within(candidate(optional)).getByRole("button", { name: `排除可选地点：${optional.name}` })).toBeTruthy();
    expect(controls.queryCandidates).toHaveBeenCalledTimes(1);
    expect(controls.searchPlaces).not.toHaveBeenCalled();
  });

  it("retains must-visits throughout loading and renders a successful empty optional pool honestly", async () => {
    const pending = deferred<CandidateResponse>();
    controls.queryCandidates.mockReturnValueOnce(pending.promise);
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    getCandidates();
    expect(pool().textContent).toMatch(/获取中|正在获取/);
    expect(candidate(required)).toBeTruthy();
    await act(async () => { pending.resolve(response([])); });
    expect(pool().textContent).toMatch(/没有.*可选|暂无.*可选|未找到.*可选/);
    expect(candidate(required)).toBeTruthy();
    expect(within(pool()).queryByRole("button", { name: /^排除可选地点/ })).toBeNull();
  });

  it("shows partial retrieval and replaces optional batches without mixing the old and new pool", async () => {
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await obtain();
    controls.queryCandidates.mockResolvedValueOnce(response([replacement], "partial"));
    await obtain([replacement]);
    expect(pool().textContent).toMatch(/部分/);
    expect(pool().textContent).toMatch(/超时|失败/);
    expect(within(pool()).queryByText(optional.name)).toBeNull();
    expect(candidate(required)).toBeTruthy();
    expect(controls.queryCandidates).toHaveBeenCalledTimes(2);
  });

  it("preserves the prior valid batch and exclusions after total failure, labelled as previous results", async () => {
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await obtain(); click(`排除可选地点：${optional.name}`);
    controls.queryCandidates.mockResolvedValueOnce(response([], "failed")); getCandidates();
    expect((await within(pool()).findByRole("alert")).textContent).toMatch(/失败/);
    expect(pool().textContent).toMatch(/上次|之前|上一/);
    expect(candidate(optional)).toBeTruthy();
    expect(within(candidate(optional)).getByRole("button", { name: `恢复可选地点：${optional.name}` })).toBeTruthy();
    expect(candidate(required)).toBeTruthy();
  });

  it("remembers an exclusion when the POI disappears from one fresh batch and returns in a later batch", async () => {
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await obtain(); click(`排除可选地点：${optional.name}`);
    controls.queryCandidates.mockResolvedValueOnce(response([replacement])); await obtain([replacement]);
    expect(within(pool()).queryByText(optional.name)).toBeNull();
    controls.queryCandidates.mockResolvedValueOnce(response([optional])); await obtain();
    expect(within(candidate(optional)).getByRole("button", { name: `恢复可选地点：${optional.name}` })).toBeTruthy();
    expect(within(pool()).queryByText(replacement.name)).toBeNull();
    expect(controls.queryCandidates).toHaveBeenCalledTimes(3);
  });

  it.each(["failed", "error", "timeout"] as const)("shows first-attempt %s without inventing optional candidates or losing must-visits", async (outcome) => {
    if (outcome === "failed") controls.queryCandidates.mockResolvedValueOnce(response([], "failed"));
    else controls.queryCandidates.mockRejectedValueOnce(new CandidateError(outcome, outcome === "timeout" ? "候选地点获取超时，请重试。" : "候选地点暂时不可用，请重试。"));
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    getCandidates();
    expect((await within(pool()).findByRole("alert")).textContent).toMatch(/失败|超时|不可用|未能获取/);
    expect(candidate(required)).toBeTruthy();
    expect(within(pool()).queryByRole("button", { name: /^排除可选地点/ })).toBeNull();
    expect(within(pool()).getByRole("button", { name: "重新获取候选地点" })).toBeTruthy();
  });

  it("never changes an existing activity binding, walking result or map polyline when excluding or restoring", async () => {
    const plan = makePlan(), original = JSON.stringify(plan);
    render(<TripPlanResult plan={plan} onEdit={vi.fn()} />);
    await obtain();
    await bind("活动甲", required); await bind("活动乙", optional);
    click("查询步行路线");
    expect(await screen.findByText(/来源：高德步行路线/)).toBeTruthy();
    await waitFor(() => expect(activeLines()).toHaveLength(1));
    const line = activeLines()[0];
    const searches = controls.searchPlaces.mock.calls.length;
    click(`排除可选地点：${optional.name}`);
    expect(within(screen.getByRole("article", { name: "活动乙" })).getByText(optional.name)).toBeTruthy();
    expect(screen.getByText(/来源：高德步行路线/)).toBeTruthy(); expect(activeLines()).toEqual([line]);
    click(`恢复可选地点：${optional.name}`);
    expect(activeLines()).toEqual([line]);
    expect(walkingFetch).toHaveBeenCalledTimes(1);
    expect(controls.queryCandidates).toHaveBeenCalledTimes(1);
    expect(controls.searchPlaces).toHaveBeenCalledTimes(searches);
    expect(JSON.stringify(plan)).toBe(original);
  });

  it("preserves candidate exclusions across day, transport mode and Marker selection without refetching", async () => {
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await obtain(); click(`排除可选地点：${optional.name}`);
    await bind("活动甲", required); await bind("活动乙", optional);
    const marker = markers.findLast((item) => item.attached && item.options.title === required.name)!;
    expect(marker).toBeTruthy(); act(() => marker.click());
    expect(document.activeElement).toBe(screen.getByRole("article", { name: "活动甲" }));
    click("在地图查看活动乙");
    expect(maps.at(-1)?.setZoomAndCenter).toHaveBeenLastCalledWith(16, [optional.longitude, optional.latitude]);
    const modes = within(screen.getByRole("group", { name: "选择真实交通方式" }));
    fireEvent.click(modes.getByRole("button", { name: "公交／地铁" }));
    fireEvent.click(modes.getByRole("button", { name: "步行" }));
    click(/Day 2/); click(/Day 1/);
    expect(within(candidate(optional)).getByRole("button", { name: `恢复可选地点：${optional.name}` })).toBeTruthy();
    expect(candidate(required)).toBeTruthy();
    expect(controls.queryCandidates).toHaveBeenCalledTimes(1);
    expect(walkingFetch).not.toHaveBeenCalled();
  });

  it("keeps an in-flight trip-wide candidate query alive across day and transport mode switches", async () => {
    const pending = deferred<CandidateResponse>(); controls.queryCandidates.mockReturnValueOnce(pending.promise);
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />); getCandidates();
    const signal = controls.queryCandidates.mock.calls[0][1] as AbortSignal;
    click(/Day 2/);
    fireEvent.click(within(screen.getByRole("group", { name: "选择真实交通方式" })).getByRole("button", { name: "公交／地铁" }));
    expect(signal.aborted).toBe(false);
    await act(async () => { pending.resolve(response()); });
    expect(candidate(optional)).toBeTruthy(); click(/Day 1/);
    expect(candidate(optional)).toBeTruthy();
    expect(controls.queryCandidates).toHaveBeenCalledTimes(1);
    expect(walkingFetch).not.toHaveBeenCalled();
  });

  it("editing and regeneration keep confirmed form requirements but clear candidate exclusions, bindings and routes", async () => {
    await plannerResult(); await obtain(); click(`排除可选地点：${optional.name}`);
    await bind("活动甲", required); await bind("活动乙", optional); click("查询步行路线");
    expect(await screen.findByText(/来源：高德步行路线/)).toBeTruthy();
    await waitFor(() => expect(activeLines()).toHaveLength(1));
    click("修改旅行需求");
    expect(within(screen.getByRole("region", { name: "已确认住宿参考点" })).getByText(lodging.name)).toBeTruthy();
    expect(within(screen.getByRole("region", { name: "已确认必去地点" })).getByText(required.name)).toBeTruthy();
    expect(activeLines()).toHaveLength(0); submitTrip();
    await screen.findByRole("button", { name: "修改旅行需求" });
    expect(within(pool()).getByRole("button", { name: "获取候选地点" })).toBeTruthy();
    expect(within(pool()).queryByText(optional.name)).toBeNull(); expect(candidate(required)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "在地图查看活动乙" })).toBeNull();
    expect(screen.queryByText(/来源：高德步行路线/)).toBeNull();
    expect(controls.queryCandidates).toHaveBeenCalledTimes(1);
    await obtain();
    expect(within(candidate(optional)).getByRole("button", { name: `排除可选地点：${optional.name}` })).toBeTruthy();
    expect(controls.planTrip.mock.calls[1][0]).toEqual(controls.planTrip.mock.calls[0][0]);
    expect(walkingFetch).toHaveBeenCalledTimes(1);
  });

  it.each(["success", "error"] as const)("ignores a late %s from a result edited and regenerated with identical requirements", async (outcome) => {
    const pending = deferred<CandidateResponse>();
    controls.queryCandidates.mockReturnValueOnce(pending.promise).mockResolvedValueOnce(response([replacement]));
    await plannerResult(); getCandidates();
    const oldSignal = controls.queryCandidates.mock.calls[0][1] as AbortSignal;
    expect(oldSignal.aborted).toBe(false); click("修改旅行需求"); expect(oldSignal.aborted).toBe(true);
    submitTrip(); await screen.findByRole("button", { name: "修改旅行需求" });
    await obtain([replacement]);
    await act(async () => {
      if (outcome === "success") pending.resolve(response());
      else pending.reject(new CandidateError("error", "不可泄漏到新结果的旧请求错误"));
    });
    expect(candidate(replacement)).toBeTruthy();
    expect(within(pool()).queryByText(optional.name)).toBeNull();
    expect(pool().textContent).not.toContain("不可泄漏到新结果");
    expect(controls.queryCandidates).toHaveBeenCalledTimes(2);
  });

  it("a refresh-equivalent remount clears result-local pool and exclusions and aborts pending requests", async () => {
    const pending = deferred<CandidateResponse>();
    const view = render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await obtain(); click(`排除可选地点：${optional.name}`);
    controls.queryCandidates.mockReturnValueOnce(pending.promise); getCandidates();
    const oldSignal = controls.queryCandidates.mock.calls.at(-1)![1] as AbortSignal;
    view.unmount(); expect(oldSignal.aborted).toBe(true);
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await act(async () => { pending.resolve(response([replacement])); });
    expect(within(pool()).getByRole("button", { name: "获取候选地点" })).toBeTruthy();
    expect(candidate(required)).toBeTruthy();
    expect(within(pool()).queryByText(optional.name)).toBeNull();
    expect(within(pool()).queryByText(replacement.name)).toBeNull();
  });
});
