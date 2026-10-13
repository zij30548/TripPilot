import { act, fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ScheduleResult } from "./schedule-preview";
import { deferred, home, optionals, required, schedule, scheduleRequest } from "@/lib/weather-schedule-check.test-fixtures";
import { adjustmentResponse } from "@/lib/schedule-adjustment.test-fixtures";
import type { AMapSDK } from "@/lib/amap-loader";
import { drawingFixture } from "@/lib/schedule-drawing.test-fixtures";
import { validateDrawing } from "@/lib/schedule-drawing";
import { useScheduleMap } from "@/lib/use-schedule-map";

const loader = vi.hoisted(() => ({ loadAMap: vi.fn(), getShanghaiCenter: vi.fn() }));
vi.mock("@/lib/amap-loader", () => loader);
// No SDK/network calls. These controls exercise the actual shared map component.
const maps: FakeMap[] = [], markers: FakeMarker[] = [];
const lines: FakeLine[] = [];
class FakeLine {
  setMap = vi.fn();
  constructor(readonly options: { path: number[][][]; strokeStyle: string; strokeColor: string }) { lines.push(this); }
}
const polyline = vi.fn(FakeLine);
class FakeMap {
  setFitView = vi.fn(); setZoomAndCenter = vi.fn(); destroy = vi.fn();
  constructor() { maps.push(this); }
}
class FakeMarker {
  handler = () => {};
  on = vi.fn((_event: string, handler: () => void) => { this.handler = handler; });
  off = vi.fn(); setMap = vi.fn(); setzIndex = vi.fn();
  constructor(readonly options: { content: HTMLElement; position: number[]; title: string }) { markers.push(this); }
}
const sdk = { Map: FakeMap, Marker: FakeMarker, Polyline: polyline } as unknown as AMapSDK;
const context = { isCurrent: () => true };
const click = async (name: string | RegExp) => act(async () => { fireEvent.click(screen.getByRole("button", { name })); });
const selected = () => document.querySelector('[data-map-selected="true"]');
beforeEach(() => {
  maps.length = 0; markers.length = 0; lines.length = 0; polyline.mockClear();
  loader.loadAMap.mockReset().mockResolvedValue(sdk); loader.getShanghaiCenter.mockReset().mockResolvedValue([121, 31]);
});

describe("explicit formal map, timeline and shared SDK", () => {
  it("retained route callbacks cannot survive date ABA, official invalidation or unmount", () => {
    const response = schedule(); response.days.push({ date: "2026-10-11", items: [], return_time: null });
    let current = true; const ctx = { isCurrent: () => current };
    const drawing = validateDrawing(drawingFixture(response), response);
    const hook = renderHook(() => useScheduleMap(response, ctx, drawing));
    const oldSelect = hook.result.current.selectRoute;
    act(() => oldSelect(0)); expect(hook.result.current.route?.itemIndex).toBe(0);
    act(() => hook.result.current.selectDate("2026-10-11"));
    act(() => hook.result.current.selectDate("2026-10-10"));
    act(() => oldSelect(0)); expect(hook.result.current.route).toBeNull();
    const latest = hook.result.current.selectRoute; current = false;
    act(() => latest(0)); expect(hook.result.current.route).toBeNull();
    hook.unmount(); current = true; act(() => latest(0)); expect(fetch).not.toHaveBeenCalled();
  });
  it("actual movement selection replaces only its line; places, fit-all, collapse and unrelated renders preserve geometry without requests", async () => {
    const response = schedule(), attachment = drawingFixture(response), drawing = validateDrawing(attachment, response);
    const view = render(<ScheduleResult response={response} drawing={drawing} mapContext={context} />);
    const buttons = screen.getAllByRole("button", { name: /^查看这段路线：/ });
    await act(async () => fireEvent.click(buttons[0])); expect(lines).toHaveLength(1);
    expect(lines[0].options.path).toEqual(attachment.entries[0].route!.segments);
    expect(document.querySelector('[data-route-selected="true"]')?.textContent).toContain("09:00");
    await click("在地图查看地点 1：同名地点"); act(() => markers[2].handler());
    await click("查看当天全部地点"); await click("查看这段路线全貌");
    expect(lines).toHaveLength(1); expect(maps[0].setFitView).toHaveBeenLastCalledWith([lines[0]], false, [48, 48, 48, 48], 17);
    await click("收起当天地图"); await click("查看当天地图"); view.rerender(<ScheduleResult response={response} drawing={drawing} mapContext={{ isCurrent: () => true }} />);
    expect(lines).toHaveLength(1); expect(lines[0].setMap).not.toHaveBeenCalledWith(null);
    await act(async () => fireEvent.click(buttons[1])); expect(lines).toHaveLength(2); expect(lines[0].setMap).toHaveBeenCalledWith(null);
    expect(lines[1].options.path).toEqual(attachment.entries[1].route!.segments);
    await click("清除路线"); expect(lines[1].setMap).toHaveBeenCalledWith(null); expect(document.querySelector('[data-route-selected="true"]')).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
  });
  it("a delayed map init uses the newly selected route; date change clears it before initialization", async () => {
    const delayed = deferred<AMapSDK>(); loader.loadAMap.mockReturnValue(delayed.promise);
    const response = schedule(), drawing = validateDrawing(drawingFixture(response), response);
    response.days.push({ date: "2026-10-11", items: [], return_time: null });
    render(<ScheduleResult response={response} drawing={drawing} mapContext={context} />);
    const buttons = screen.getAllByRole("button", { name: /^查看这段路线：/ });
    await act(async () => { fireEvent.click(buttons[0]); fireEvent.click(buttons[1]); });
    await click(/草案第 2 天/); await act(async () => delayed.resolve(sdk));
    expect(lines).toHaveLength(0); expect(screen.queryByLabelText("当前交通路线")).toBeNull();
    await click(/草案第 1 天/); expect(lines).toHaveLength(0);
  });
  it("invalid/missing geometry preserves timeline and markers, and readonly never enables route controls", async () => {
    const response = schedule(); const view = render(<ScheduleResult response={response} drawing={{ status: "invalid", attachment: null }} mapContext={context} />);
    await act(async () => fireEvent.click(screen.getAllByRole("button", { name: /^查看这段路线：/ })[0]));
    expect(screen.getByText(/步行绘图附件未通过/)).toBeTruthy(); expect(lines).toHaveLength(0); expect(markers).toHaveLength(3);
    view.rerender(<ScheduleResult response={response} />);
    expect(screen.queryByRole("button", { name: /^查看这段路线：/ })).toBeNull();
  });
  it("transit draws only provided independent walking and ride pieces, with partial/no-geometry messages", async () => {
    const response = adjustmentResponse({ ...scheduleRequest(), transport_mode: "transit" });
    const route = response.edges[0].transit_route!;
    route.legs[0].geometry = [[[121, 31], [121.001, 31]]]; route.legs[0].geometry_complete = false;
    route.geometry_complete = false;
    render(<ScheduleResult response={response} mapContext={context} />);
    await act(async () => fireEvent.click(screen.getAllByRole("button", { name: /^查看这段路线：/ })[0]));
    expect(lines).toHaveLength(1); expect(lines[0].options.path).toEqual(route.legs[0].geometry);
    expect(screen.getByText(/路线地图不完整/)).toBeTruthy(); expect(screen.getByLabelText("公交方案地图图例")).toBeTruthy();
    await act(async () => fireEvent.click(screen.getAllByRole("button", { name: /^查看这段路线：/ })[1]));
    expect(lines[0].setMap).toHaveBeenCalledWith(null); expect(screen.getByText(/本方案没有可绘制几何/)).toBeTruthy(); expect(fetch).not.toHaveBeenCalled();
  });
  it("readonly history/comparison defaults never initialize maps; formal mode is also lazy", async () => {
    const response = schedule(); const view = render(<ScheduleResult response={response} />);
    expect(screen.queryByRole("button", { name: "查看当天地图" })).toBeNull(); expect(loader.loadAMap).not.toHaveBeenCalled();
    view.rerender(<ScheduleResult response={response} mapContext={context} />);
    expect(loader.loadAMap).not.toHaveBeenCalled(); await click("查看当天地图");
    await waitFor(() => expect(markers).toHaveLength(3)); expect(polyline).not.toHaveBeenCalled(); expect(fetch).not.toHaveBeenCalled();
    expect(markers.map(m => m.options.content.textContent)).toEqual(["住", "1", "2"]);
  });
  it("a valid transit draft uses exactly the same visit markers, without initializing any transit polyline", async () => {
    const response = adjustmentResponse({ ...scheduleRequest(), transport_mode: "transit" });
    render(<ScheduleResult response={response} mapContext={context} />);
    await click("在地图查看地点 2：同名地点"); await waitFor(() => expect(markers).toHaveLength(3));
    expect(markers.map(m => m.options.content.textContent)).toEqual(["住", "1", "2"]);
    expect(polyline).not.toHaveBeenCalled(); expect(fetch).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "查看公交方案全貌" })).toBeNull();
  });
  it("card → marker repeatedly recenters; marker → correct ID card focuses/highlights and home → home description", async () => {
    render(<ScheduleResult response={schedule()} mapContext={context} />);
    await click("在地图查看地点 1：同名地点"); await waitFor(() => expect(markers).toHaveLength(3));
    expect(maps[0].setZoomAndCenter).toHaveBeenLastCalledWith(16, [required.longitude, required.latitude]);
    const n = maps[0].setZoomAndCenter.mock.calls.length; await click("在地图查看地点 1：同名地点");
    expect(maps[0].setZoomAndCenter).toHaveBeenCalledTimes(n + 1);
    act(() => markers[2].handler()); expect(selected()?.textContent).toContain("地点 2"); expect(document.activeElement).toBe(selected());
    expect(markers[1].options.content.style.background).toBe("rgb(49, 95, 81)"); expect(markers[2].setzIndex).toHaveBeenLastCalledWith(200);
    act(() => markers[0].handler()); expect(selected()?.textContent).toContain("住宿参考点"); expect(selected()?.tagName).toBe("DIV");
    await click("查看当天全部地点"); expect(maps[0].setFitView).toHaveBeenLastCalledWith(markers, false, [40, 40, 40, 40], 16);
    expect(fetch).not.toHaveBeenCalled();
  });
  it("home also visited keeps one marker and separate home/visit entrances", async () => {
    const r = schedule(); r.request.must_visit_places[0] = home; r.days[0].items.find(i => i.kind === "visit")!.place_id = home.id;
    render(<ScheduleResult response={r} mapContext={context} />); await click("在地图查看地点 1：测试住宿");
    await waitFor(() => expect(markers).toHaveLength(2)); expect(markers[0].options.content.textContent).toBe("住·1");
    expect(selected()?.tagName).toBe("LI"); act(() => markers[0].handler()); expect(selected()?.tagName).toBe("DIV");
    expect(selected()?.textContent).toContain("也安排为第 1 个游览地点");
  });
  it("dates sync, numbering restarts, empty days remove all markers; retained old events and ABA are inert", async () => {
    const r = schedule(); const visit = r.days[0].items.find(i => i.kind === "visit")!;
    r.days.push({ date: "2026-10-11", items: [{ ...visit, place_id: optionals[0].id }], return_time: null }, { date: "2026-10-12", items: [], return_time: null });
    render(<ScheduleResult response={r} mapContext={context} />); await click("查看当天地图"); await waitFor(() => expect(markers).toHaveLength(3));
    const old = markers[1].handler; act(old); await click(/草案第 2 天/);
    expect(selected()).toBeNull(); expect(markers.slice(3).map(m => m.options.content.textContent)).toEqual(["住", "1"]);
    expect(within(screen.getByRole("list", { name: "草案时间线" })).getByText(/可选地址1/)).toBeTruthy();
    act(old); expect(selected()).toBeNull(); await click(/草案第 1 天/); act(old); expect(selected()).toBeNull();
    const previous = markers.slice(-3); await click(/草案第 3 天/);
    expect(screen.getByText("当天尚未安排景点，地图没有地点标记。")).toBeTruthy(); previous.forEach(m => expect(m.setMap).toHaveBeenCalledWith(null));
    expect(screen.getByRole("button", { name: "查看当天全部地点" })).toHaveProperty("disabled", true); expect(maps).toHaveLength(1);
  });
  it("collapse and unrelated renders preserve the instance/markers/view, cleanup detaches all listeners", async () => {
    const response = schedule(); const view = render(<ScheduleResult response={response} mapContext={context} />);
    await click("查看当天地图"); await waitFor(() => expect(markers).toHaveLength(3)); await click("在地图查看地点 2：同名地点");
    const fits = maps[0].setFitView.mock.calls.length, centers = maps[0].setZoomAndCenter.mock.calls.length;
    await click("收起当天地图"); await click("查看当天地图"); view.rerender(<ScheduleResult response={response} mapContext={{ isCurrent: () => true }} />);
    expect(maps).toHaveLength(1); expect(markers).toHaveLength(3); expect(maps[0].setFitView).toHaveBeenCalledTimes(fits); expect(maps[0].setZoomAndCenter).toHaveBeenCalledTimes(centers);
    view.unmount(); markers.forEach(m => expect(m.off).toHaveBeenCalledWith("click", m.handler)); expect(maps[0].destroy).toHaveBeenCalledOnce(); act(() => markers[1].handler());
  });
  it("initialization uses the newest date, and unmount prevents late creation", async () => {
    const delayed = deferred<AMapSDK>(); loader.loadAMap.mockReturnValue(delayed.promise);
    const r = schedule(); r.days.push({ date: "2026-10-11", items: [], return_time: null });
    const view = render(<ScheduleResult response={r} mapContext={context} />); await click("查看当天地图"); await click(/草案第 2 天/);
    await act(async () => delayed.resolve(sdk)); expect(maps).toHaveLength(1); expect(markers).toHaveLength(0); view.unmount();
    const late = deferred<AMapSDK>(); loader.loadAMap.mockReturnValue(late.promise);
    const next = render(<ScheduleResult response={r} mapContext={context} />); await click("查看当天地图"); next.unmount();
    await act(async () => late.resolve(sdk)); expect(maps).toHaveLength(1);
  });
  it("synchronous official invalidation rejects callbacks/init before React unmount", async () => {
    let current = true; const delayed = deferred<AMapSDK>(); loader.loadAMap.mockReturnValue(delayed.promise);
    render(<ScheduleResult response={schedule()} mapContext={{ isCurrent: () => current }} />); await click("查看当天地图");
    current = false; await act(async () => delayed.resolve(sdk)); expect(maps).toHaveLength(0);
    await click("在地图查看地点 1：同名地点"); expect(selected()).toBeNull();
  });
  it("map failure is isolated, explicit retry succeeds without changing timeline", async () => {
    loader.loadAMap.mockRejectedValueOnce(new Error("受控地图加载失败"));
    render(<ScheduleResult response={schedule()} mapContext={context} />);
    const timeline = screen.getByRole("list", { name: "草案时间线" }).textContent;
    await click("查看当天地图"); expect(screen.getByRole("alert").textContent).toContain("受控地图加载失败");
    await click("重试加载地图"); await waitFor(() => expect(markers).toHaveLength(3));
    expect(screen.getByRole("list", { name: "草案时间线" }).textContent).toBe(timeline); expect(fetch).not.toHaveBeenCalled();
  });
  it("invalid coordinates never become zero while timeline remains readable", async () => {
    const r = structuredClone(schedule()); r.request.must_visit_places[0].latitude = Infinity;
    render(<ScheduleResult response={r} mapContext={context} />); await click("查看当天地图");
    await waitFor(() => expect(markers).toHaveLength(2)); expect(screen.getByRole("list", { name: "草案时间线" }).textContent).toContain("必去地点");
    expect(markers.every(m => m.options.position.every(Number.isFinite))).toBe(true); expect(screen.getByText(/部分地点缺少有效坐标/)).toBeTruthy();
  });
});
