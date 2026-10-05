import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import TripRequestForm from "@/components/trip-request-form";
import TripPlanner from "@/components/trip/trip-planner";
import type { AMapSDK } from "@/lib/amap-loader";
import type { Place } from "@/types/place";
import type { TripPlan, TripRequest } from "@/types/trip";

const controls = vi.hoisted(() => ({
  searchPlaces: vi.fn(), planTrip: vi.fn(), loadAMap: vi.fn(), getShanghaiCenter: vi.fn(),
}));
vi.mock("@/lib/places-api", () => ({ searchPlaces: controls.searchPlaces }));
vi.mock("@/lib/api", () => ({ planTrip: controls.planTrip }));
vi.mock("@/lib/amap-loader", () => ({ loadAMap: controls.loadAMap, getShanghaiCenter: controls.getShanghaiCenter }));

// All POIs, coordinates and travel figures in this file are fictional test data.
// Search intentionally ignores AbortSignal so session/version guards are exercised.
const lodging: Place = {
  id: "fixture-lodging", name: "测试住宿地标", address: "测试住宿地址", category: "测试地标",
  longitude: 120, latitude: 30, source: "amap",
};
const first: Place = {
  id: "fixture-first", name: "测试同名景点", address: "测试地址甲", category: "测试景点",
  longitude: 120.01, latitude: 30.01, source: "amap",
};
const second: Place = {
  id: "fixture-second", name: "测试同名景点", address: "测试地址乙", category: "测试景点",
  longitude: 120.02, latitude: 30.02, source: "amap",
};
const replacement: Place = {
  id: "fixture-replacement", name: "测试替换地点", address: "测试替换地址", category: null,
  longitude: 120.03, latitude: 30.03, source: "amap",
};

function makePlan(request: TripRequest): TripPlan {
  return {
    destination: "上海", estimated_cost: 40, currency: "CNY", is_mock: true,
    notice: "测试 Mock 示例，活动时间、交通、费用和天气未按需求验证。",
    request, budget_breakdown: { transport: 0, food: 0, tickets: 40, other: 0 },
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

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

const maps: MockMap[] = [];
const lines: MockPolyline[] = [];
class MockMap {
  setFitView = vi.fn(); setZoomAndCenter = vi.fn(); destroy = vi.fn();
  constructor() { maps.push(this); }
}
class MockMarker {
  on = vi.fn(); off = vi.fn(); setMap = vi.fn(); setzIndex = vi.fn();
}
class MockPolyline {
  currentMap: MockMap | null = null;
  setMap = vi.fn((map: MockMap | null) => { this.currentMap = map; });
  constructor() { lines.push(this); }
}

beforeEach(() => {
  maps.length = 0; lines.length = 0;
  controls.searchPlaces.mockReset().mockResolvedValue([lodging, first, second, replacement]);
  controls.planTrip.mockReset().mockImplementation(async (request: TripRequest) => makePlan(request));
  controls.loadAMap.mockReset().mockResolvedValue({ Map: MockMap, Marker: MockMarker, Polyline: MockPolyline } as unknown as AMapSDK);
  controls.getShanghaiCenter.mockReset().mockResolvedValue([120, 30]);
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => { callback(0); return 1; });
});

function fillBasicFields() {
  fireEvent.change(screen.getByLabelText(/开始日期/), { target: { value: "2026-10-10" } });
  fireEvent.change(screen.getByLabelText(/结束日期/), { target: { value: "2026-10-11" } });
  fireEvent.change(screen.getByLabelText(/总预算/), { target: { value: "3000" } });
  fireEvent.change(screen.getByLabelText(/旅行人数/), { target: { value: "2" } });
}
function click(name: string | RegExp) { fireEvent.click(screen.getByRole("button", { name })); }
function submitTrip() {
  fireEvent.submit(screen.getByRole("button", { name: "生成我的行程" }).closest("form")!);
}
function picker(role: "lodging" | "must" = "lodging") {
  return screen.getByRole("region", { name: role === "lodging" ? "住宿参考点选择器" : "必去地点选择器" });
}
function confirmed(role: "lodging" | "must" = "lodging") {
  return screen.getByRole("region", { name: role === "lodging" ? "已确认住宿参考点" : "已确认必去地点" });
}
function search(keyword = "测试搜索") {
  fireEvent.change(screen.getByRole("textbox", { name: "搜索上海地点" }), { target: { value: keyword } });
  click("搜索");
}
async function preview(place: Place, role: "lodging" | "must" = "lodging") {
  search();
  const candidate = await within(picker(role)).findByRole("button", { name: new RegExp(`${place.name}.*${place.address}`) });
  fireEvent.click(candidate);
  return candidate;
}
async function confirmLodging(place = lodging) {
  click("选择住宿参考点");
  await preview(place);
  click("确认住宿参考点");
}
async function addMust(place = first) {
  click("添加必去地点");
  await preview(place, "must");
  click("确认必去地点");
}
function confirmedButtons(role: "lodging" | "must", action: "更换" | "移除", place: Place) {
  const all = within(confirmed(role)).getAllByRole("button", { name: `${action}必去地点：${place.name}` });
  return all.find((button) => button.closest("li")?.textContent?.includes(place.address!)) ?? all[0];
}

describe("confirmed structured trip requirements", () => {
  it("requires a confirmed lodging reference, not a selected preview", async () => {
    render(<TripRequestForm onSuccess={vi.fn()} />);
    fillBasicFields();
    submitTrip();
    expect(controls.planTrip).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toMatch(/住宿参考点/);

    click("选择住宿参考点");
    expect((screen.getByRole("button", { name: "确认住宿参考点" }) as HTMLButtonElement).disabled).toBe(true);
    const candidate = await preview(lodging);
    expect(candidate.getAttribute("aria-pressed")).toBe("true");
    expect(within(confirmed()).queryByText(lodging.name)).toBeNull();
    submitTrip();
    expect(controls.planTrip).not.toHaveBeenCalled();
    expect(screen.getAllByRole("alert").some((node) => /确认或取消/.test(node.textContent ?? ""))).toBe(true);
    click("取消选择");
    expect(within(confirmed()).queryByText(lodging.name)).toBeNull();
  });

  it("sends the exact confirmed Place and derives legacy text; must-visit may be empty", async () => {
    const onSuccess = vi.fn();
    render(<TripRequestForm onSuccess={onSuccess} />);
    fillBasicFields();
    await confirmLodging();
    expect(within(confirmed()).getByText(lodging.name)).toBeTruthy();
    expect(within(confirmed()).getByText(`地址：${lodging.address}`)).toBeTruthy();
    expect(within(confirmed()).getByText(/高德/)).toBeTruthy();
    submitTrip();
    await waitFor(() => expect(onSuccess).toHaveBeenCalledTimes(1));
    expect(controls.planTrip.mock.calls[0][0]).toMatchObject({
      accommodation_place: lodging, accommodation_location: lodging.name,
      must_visit_places: [], must_visit: [], budget: 3000, travelers: 2,
    });
    expect(screen.queryByRole("region", { name: /选择器$/ })).toBeNull();
  });

  it("keeps same-name different-ID POIs distinct with addresses and derives ordered must-visit names", async () => {
    render(<TripRequestForm onSuccess={vi.fn()} />);
    fillBasicFields();
    await confirmLodging(); await addMust(first); await addMust(second);
    expect(within(confirmed("must")).getAllByText(first.name)).toHaveLength(2);
    expect(within(confirmed("must")).getByText(`地址：${first.address}`)).toBeTruthy();
    expect(within(confirmed("must")).getByText(`地址：${second.address}`)).toBeTruthy();
    submitTrip();
    await waitFor(() => expect(controls.planTrip).toHaveBeenCalledOnce());
    expect(controls.planTrip.mock.calls[0][0]).toMatchObject({ must_visit_places: [first, second], must_visit: [first.name, second.name] });
  });

  it("blocks a duplicate must-visit ID but allows the same POI in the accommodation role", async () => {
    render(<TripRequestForm onSuccess={vi.fn()} />);
    fillBasicFields();
    await confirmLodging(lodging); await addMust(lodging);
    click("添加必去地点");
    await preview(lodging, "must");
    expect((screen.getByRole("button", { name: "确认必去地点" }) as HTMLButtonElement).disabled).toBe(true);
    expect(within(picker("must")).getByRole("alert").textContent).toMatch(/已|重复/);
    click("取消选择");
    submitTrip();
    await waitFor(() => expect(controls.planTrip).toHaveBeenCalledOnce());
    expect(controls.planTrip.mock.calls[0][0]).toMatchObject({ accommodation_place: lodging, must_visit_places: [lodging] });
  });

  it("blocks replacing a must-visit with another already-confirmed ID without changing either item", async () => {
    render(<TripRequestForm onSuccess={vi.fn()} />);
    await addMust(first); await addMust(second);
    fireEvent.click(confirmedButtons("must", "更换", first));
    await preview(second, "must");
    expect((screen.getByRole("button", { name: "确认必去地点" }) as HTMLButtonElement).disabled).toBe(true);
    click("取消选择");
    expect(within(confirmed("must")).getByText(`地址：${first.address}`)).toBeTruthy();
    expect(within(confirmed("must")).getByText(`地址：${second.address}`)).toBeTruthy();
  });

  it("explicitly replaces and clears lodging, then blocks submission until it is confirmed again", async () => {
    render(<TripRequestForm onSuccess={vi.fn()} />);
    fillBasicFields(); await confirmLodging();
    click("更换住宿参考点"); await preview(replacement); click("确认住宿参考点");
    expect(within(confirmed()).queryByText(lodging.name)).toBeNull();
    expect(within(confirmed()).getByText(replacement.name)).toBeTruthy();
    click("清除住宿参考点"); submitTrip();
    expect(controls.planTrip).not.toHaveBeenCalled();
    expect(within(confirmed()).queryByText(replacement.name)).toBeNull();
    await confirmLodging(); submitTrip();
    await waitFor(() => expect(controls.planTrip).toHaveBeenCalledOnce());
  });

  it("explicitly replaces and removes must-visit items without mutating accommodation", async () => {
    render(<TripRequestForm onSuccess={vi.fn()} />);
    fillBasicFields(); await confirmLodging(); await addMust(first);
    fireEvent.click(confirmedButtons("must", "更换", first));
    await preview(replacement, "must"); click("确认必去地点");
    expect(within(confirmed("must")).queryByText(first.name)).toBeNull();
    expect(within(confirmed("must")).getByText(replacement.name)).toBeTruthy();
    fireEvent.click(confirmedButtons("must", "移除", replacement));
    expect(within(confirmed("must")).queryByText(replacement.name)).toBeNull();
    expect(within(confirmed()).getByText(lodging.name)).toBeTruthy();
    submitTrip();
    await waitFor(() => expect(controls.planTrip).toHaveBeenCalledOnce());
    expect(controls.planTrip.mock.calls[0][0].must_visit_places).toEqual([]);
  });

  it.each(["cancel", "empty", "failure"] as const)("preserves original lodging when replacement ends in %s", async (ending) => {
    render(<TripRequestForm onSuccess={vi.fn()} />);
    await confirmLodging(); click("更换住宿参考点");
    if (ending === "cancel") await preview(replacement);
    if (ending === "empty") {
      controls.searchPlaces.mockResolvedValueOnce([]); search();
      expect(await within(picker()).findByText("没有找到匹配地点，请更换关键词。")).toBeTruthy();
    }
    if (ending === "failure") {
      controls.searchPlaces.mockRejectedValueOnce(new Error("测试搜索不可用")); search();
      expect(await within(picker()).findByRole("alert")).toBeTruthy();
    }
    expect(within(confirmed()).getByText(lodging.name)).toBeTruthy();
    click("取消选择");
    expect(within(confirmed()).getByText(lodging.name)).toBeTruthy();
    expect(within(confirmed()).queryByText(replacement.name)).toBeNull();
  });

  it.each(["cancel", "empty", "failure"] as const)("preserves the original must-visit when replacement ends in %s", async (ending) => {
    render(<TripRequestForm onSuccess={vi.fn()} />);
    await addMust(first); fireEvent.click(confirmedButtons("must", "更换", first));
    if (ending === "cancel") await preview(replacement, "must");
    if (ending === "empty") {
      controls.searchPlaces.mockResolvedValueOnce([]); search();
      expect(await within(picker("must")).findByText("没有找到匹配地点，请更换关键词。")).toBeTruthy();
    }
    if (ending === "failure") {
      controls.searchPlaces.mockRejectedValueOnce(new Error("测试搜索不可用")); search();
      expect(await within(picker("must")).findByRole("alert")).toBeTruthy();
    }
    click("取消选择");
    expect(within(confirmed("must")).getByText(`地址：${first.address}`)).toBeTruthy();
    expect(within(confirmed("must")).queryByText(replacement.name)).toBeNull();
  });

  it("clears draft candidates and selection after cancel/reopen and accommodation → must → accommodation", async () => {
    render(<TripRequestForm onSuccess={vi.fn()} />);
    click("选择住宿参考点"); await preview(lodging); click("取消选择"); click("选择住宿参考点");
    expect(within(picker()).queryByRole("button", { name: new RegExp(lodging.name) })).toBeNull();
    expect((screen.getByRole("button", { name: "确认住宿参考点" }) as HTMLButtonElement).disabled).toBe(true);
    await preview(first); click("添加必去地点");
    expect((screen.getByRole("button", { name: "确认必去地点" }) as HTMLButtonElement).disabled).toBe(true);
    await preview(second, "must"); click("选择住宿参考点");
    expect(within(picker()).queryByRole("button", { name: new RegExp(first.address!) })).toBeNull();
    expect(within(picker()).queryByRole("button", { name: new RegExp(second.address!) })).toBeNull();
    expect((screen.getByRole("button", { name: "确认住宿参考点" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it.each(["success", "error"] as const)("isolates late %s across accommodation → must → accommodation even if transport ignores abort", async (outcome) => {
    const pending = deferred<Place[]>();
    controls.searchPlaces.mockReturnValueOnce(pending.promise).mockResolvedValueOnce([replacement]);
    render(<TripRequestForm onSuccess={vi.fn()} />);
    click("选择住宿参考点"); search("旧住宿查询");
    const oldSignal = controls.searchPlaces.mock.calls[0][1] as AbortSignal;
    expect(oldSignal.aborted).toBe(false);
    click("添加必去地点");
    expect(oldSignal.aborted).toBe(true);
    click("选择住宿参考点");
    const newCandidate = await preview(replacement);
    await act(async () => {
      if (outcome === "success") pending.resolve([lodging]);
      else pending.reject(new Error("旧查询错误不应显示"));
    });
    expect(newCandidate.getAttribute("aria-pressed")).toBe("true");
    expect(within(picker()).queryByText("旧查询错误不应显示")).toBeNull();
    expect(within(picker()).queryByRole("button", { name: new RegExp(lodging.address!) })).toBeNull();
    click("确认住宿参考点");
    expect(within(confirmed()).getByText(replacement.name)).toBeTruthy();
    expect(within(confirmed()).queryByText(lodging.name)).toBeNull();
  });

  it.each(["success", "error"] as const)("ignores a late %s from an earlier keyword in the same picker session", async (outcome) => {
    const pending = deferred<Place[]>();
    controls.searchPlaces.mockReturnValueOnce(pending.promise).mockResolvedValueOnce([replacement]);
    render(<TripRequestForm onSuccess={vi.fn()} />);
    click("选择住宿参考点"); search("慢查询");
    const oldSignal = controls.searchPlaces.mock.calls[0][1] as AbortSignal;
    const candidate = await preview(replacement);
    expect(oldSignal.aborted).toBe(true);
    await act(async () => {
      if (outcome === "success") pending.resolve([lodging]);
      else pending.reject(new Error("旧错误不应覆盖新结果"));
    });
    expect(candidate.getAttribute("aria-pressed")).toBe("true");
    expect(within(picker()).queryByRole("button", { name: new RegExp(lodging.name) })).toBeNull();
    expect(within(picker()).queryByRole("alert")).toBeNull();
  });

  it("has sibling forms: search Enter submits only the search form and never generates a trip", async () => {
    render(<TripRequestForm onSuccess={vi.fn()} />);
    fillBasicFields(); await confirmLodging(); click("添加必去地点");
    const input = screen.getByRole("textbox", { name: "搜索上海地点" });
    const searchForm = input.closest("form")!;
    const tripForm = screen.getByRole("button", { name: "生成我的行程" }).closest("form")!;
    expect(searchForm).not.toBe(tripForm);
    expect(searchForm.contains(tripForm)).toBe(false);
    expect(tripForm.contains(searchForm)).toBe(false);
    fireEvent.change(input, { target: { value: "回车搜索词" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" });
    // jsdom does not synthesize the browser's Enter default submit action.
    fireEvent.submit(searchForm);
    await waitFor(() => expect(controls.searchPlaces).toHaveBeenLastCalledWith("回车搜索词", expect.any(AbortSignal)));
    expect(controls.planTrip).not.toHaveBeenCalled();
  });

  it("preserves confirmed items after planning failure and closes the completed picker", async () => {
    controls.planTrip.mockRejectedValueOnce(new Error("测试行程服务暂不可用"));
    render(<TripRequestForm onSuccess={vi.fn()} />);
    fillBasicFields(); await confirmLodging(); await addMust(first);
    submitTrip();
    expect(await screen.findByText("测试行程服务暂不可用")).toBeTruthy();
    expect(within(confirmed()).getByText(lodging.name)).toBeTruthy();
    expect(within(confirmed("must")).getByText(`地址：${first.address}`)).toBeTruthy();
    expect(screen.queryByRole("region", { name: /选择器$/ })).toBeNull();
  });

  it("blocks unfinished sessions; cancel and submit invalidates a pending search before the result hides the form", async () => {
    const pending = deferred<Place[]>();
    render(<TripPlanner><p>测试旅行说明</p></TripPlanner>);
    fillBasicFields(); await confirmLodging();
    controls.searchPlaces.mockReturnValueOnce(pending.promise);
    click("添加必去地点"); search("尚未完成的搜索");
    const signal = controls.searchPlaces.mock.calls.at(-1)![1] as AbortSignal;
    submitTrip(); expect(controls.planTrip).not.toHaveBeenCalled();
    click("取消选择"); expect(signal.aborted).toBe(true); submitTrip();
    await screen.findByRole("button", { name: "修改旅行需求" });
    await act(async () => { pending.resolve([replacement]); });
    click("修改旅行需求");
    expect(screen.queryByRole("region", { name: /选择器$/ })).toBeNull();
    expect(within(confirmed("must")).queryByText(replacement.name)).toBeNull();
    expect(within(confirmed()).getByText(lodging.name)).toBeTruthy();
  });

  it("aborts unfinished search on full unmount and refresh-equivalent remount clears all confirmations", async () => {
    const pending = deferred<Place[]>();
    const view = render(<TripRequestForm onSuccess={vi.fn()} />);
    await confirmLodging(); await addMust(first);
    controls.searchPlaces.mockReturnValueOnce(pending.promise);
    click("添加必去地点"); search();
    const signal = controls.searchPlaces.mock.calls.at(-1)![1] as AbortSignal;
    view.unmount(); expect(signal.aborted).toBe(true);
    render(<TripRequestForm onSuccess={vi.fn()} />);
    await act(async () => { pending.resolve([replacement]); });
    expect(within(confirmed()).queryByText(lodging.name)).toBeNull();
    expect(within(confirmed("must")).queryByText(first.name)).toBeNull();
    expect(screen.queryByRole("region", { name: /选择器$/ })).toBeNull();
  });

  it("echoes submitted Places, retains requirements on edit but never carries result bindings or a walking route into regeneration", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(JSON.stringify({
      status: "ok", source: "amap", queried_at: "2026-10-06T01:00:00Z",
      route: { distance_meters: 1250, duration_seconds: 901, segments: [[[120.01, 30.01], [120.02, 30.02]]] },
    })));
    vi.stubGlobal("fetch", fetchMock);
    render(<TripPlanner><p>测试旅行说明</p></TripPlanner>);
    fillBasicFields(); await confirmLodging(); await addMust(first); await addMust(second); submitTrip();
    await screen.findByRole("button", { name: "修改旅行需求" });
    const request = controls.planTrip.mock.calls[0][0] as TripRequest;
    const submittedPlan = await controls.planTrip.mock.results[0].value as TripPlan;
    const original = JSON.stringify(submittedPlan);
    const echo = within(screen.getByRole("region", { name: "已确认需求地点" }));
    expect(echo.getByText(`地址：${lodging.address}`)).toBeTruthy();
    expect(echo.getByText(`地址：${first.address}`)).toBeTruthy();
    expect(echo.getByText(`地址：${second.address}`)).toBeTruthy();
    expect(echo.getByText(/尚未用于安排.*Mock/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "在地图查看活动甲" })).toBeNull();
    expect(screen.queryByRole("button", { name: "在地图查看活动乙" })).toBeNull();

    for (const [name, place] of [["活动甲", first], ["活动乙", second]] as const) {
      click(`为${name}绑定地点`); search();
      const list = await screen.findByRole("list", { name: "地点搜索结果" });
      fireEvent.click(within(list).getByRole("button", { name: new RegExp(`${place.name}.*${place.address}`) }));
      click("确认绑定");
    }
    click("查询步行路线");
    expect(await screen.findByText(/来源：高德步行路线/)).toBeTruthy();
    await waitFor(() => expect(lines.some((line) => line.currentMap)).toBe(true));
    click("修改旅行需求");
    expect(within(confirmed()).getByText(lodging.name)).toBeTruthy();
    expect(within(confirmed("must")).getByText(`地址：${first.address}`)).toBeTruthy();
    expect(within(confirmed("must")).getByText(`地址：${second.address}`)).toBeTruthy();
    expect(lines.every((line) => line.currentMap === null)).toBe(true);
    submitTrip(); await screen.findByRole("button", { name: "修改旅行需求" });
    expect(controls.planTrip).toHaveBeenCalledTimes(2);
    expect(controls.planTrip.mock.calls[1][0]).toEqual(request);
    expect(screen.queryByRole("button", { name: "在地图查看活动甲" })).toBeNull();
    expect(screen.queryByRole("button", { name: "在地图查看活动乙" })).toBeNull();
    expect(screen.queryByText(/来源：高德步行路线/)).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(JSON.stringify(submittedPlan)).toBe(original);
  });
});
