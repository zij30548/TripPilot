import type { ComponentProps } from "react";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import TripPlanResult from "@/components/trip-plan-result";
import type PlaceExplorer from "@/components/places/place-explorer";
import { candidateResponse, deferred, forecast, now, optionals, plan, required, schedule, stamp } from "@/lib/weather-schedule-check.test-fixtures";
import type { ScheduleRequest } from "@/types/schedule";
import type { WeatherForecastResponse } from "@/types/weather";
import { adjustmentResponse } from "@/lib/schedule-adjustment.test-fixtures";

const api = vi.hoisted(() => ({ weather: vi.fn(), candidates: vi.fn(), schedule: vi.fn(), walking: vi.fn() }));
vi.mock("@/lib/weather-api", async (original) => ({ ...await original<typeof import("@/lib/weather-api")>(), queryWeatherForecast: api.weather }));
vi.mock("@/lib/candidates-api", async (original) => ({ ...await original<typeof import("@/lib/candidates-api")>(), queryCandidates: api.candidates }));
vi.mock("@/lib/schedule-api", async (original) => ({ ...await original<typeof import("@/lib/schedule-api")>(), querySchedulePreview: api.schedule }));
vi.mock("@/lib/routes-api", async (original) => ({ ...await original<typeof import("@/lib/routes-api")>(), queryWalkingRoute: api.walking }));
vi.mock("@/components/places/place-explorer", () => ({ default: (props: ComponentProps<typeof PlaceExplorer>) => <section aria-label="测试地图">
  {props.bindingTarget && <button onClick={() => props.bindingTarget!.onConfirm(props.bindingTarget!.keyword === "测试活动甲" ? required : optionals[0])}>测试确认地点</button>}
  {props.itinerary?.places.map((place) => <button key={place.id} onClick={() => props.itinerary!.onSelect(place.id)}>测试 Marker {place.id}</button>)}
  <output data-testid="map-state">{JSON.stringify({ ids: props.itinerary?.places.map((place) => place.id), selected: props.itinerary?.selectedPlaceId, walking: props.walkingRoute, transit: props.transitRoute })}</output>
</section> }));
const panel = () => within(screen.getByRole("region", { name: "天气与行程检查" }));
const click = async (name: string) => { await act(async () => fireEvent.click(screen.getByRole("button", { name }))); };
async function ready() {
  const input = plan(), view = render(<TripPlanResult plan={input} onEdit={vi.fn()} />);
  await click("获取候选地点"); await click("生成行程草案"); await click("查询天气");
  return { input, view };
}
beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] }); vi.setSystemTime(now); vi.clearAllMocks();
  api.weather.mockImplementation(async () => forecast()); api.candidates.mockImplementation(async () => candidateResponse());
  api.schedule.mockImplementation(async (request: ScheduleRequest) => schedule(request));
  api.walking.mockResolvedValue({ status: "ok", source: "amap", queried_at: stamp, route: { distance_meters: 80, duration_seconds: 60, segments: [[[121.01, 31], [121.1, 31]]] } });
});
afterEach(() => vi.useRealTimers());

describe("weather check UI in the actual result composition", () => {
  it("labels only real scheduled POIs, previews privately and explicitly adopts an optional exclusion", async () => {
    const { input } = await ready(); const original = JSON.stringify(input);
    const draft = screen.getByRole("region", { name: "行程草案" }).textContent;
    const selectors = panel().getAllByRole("combobox");
    expect(selectors).toHaveLength(2); expect(panel().queryByText(/测试活动甲/)).toBeNull();
    fireEvent.change(selectors[1], { target: { value: "outdoor" } });
    await click("检查天气对行程的影响");
    const results = panel().getAllByRole("article");
    expect(within(results[0]).queryByRole("button", { name: "预览排除该地点后的草案" })).toBeNull();
    expect(within(results[1]).getByText("白天：未触发雨雪规则")).toBeTruthy();
    expect(within(results[1]).getByText("夜间：有雨雪预报")).toBeTruthy();
    expect(within(results[1]).getByText(/同日部分时段预报有雨雪/)).toBeTruthy();
    expect(screen.getByRole("region", { name: "行程草案" }).textContent).toBe(draft);
    expect(api.weather).toHaveBeenCalledTimes(1); expect(api.schedule).toHaveBeenCalledTimes(1);
    await click("预览排除该地点后的草案");
    expect(screen.getByRole("region", { name: "行程草案" }).textContent).toBe(draft);
    expect(screen.queryByRole("button", { name: "恢复可选地点：同名地点" })).toBeNull();
    expect(screen.getByRole("region", { name: "调整预览" }).textContent).toContain("尚未采用");
    await click("采用此方案");
    expect(screen.getByRole("button", { name: "恢复可选地点：同名地点" })).toBeTruthy();
    expect(panel().getByRole("button", { name: "预览排除该地点后的草案" }).hasAttribute("disabled")).toBe(true);
    expect(api.schedule).toHaveBeenCalledTimes(2);
    const sent = api.schedule.mock.calls[1][0] as ScheduleRequest;
    expect(sent.optional_places!.map((p) => p.id)).toEqual(optionals.slice(1).map((p) => p.id));
    expect(sent.must_visit_places).toEqual([required]);
    expect((panel().getAllByRole("combobox")[1] as HTMLSelectElement).value).toBe("unknown");
    await click("检查天气对行程的影响");
    expect(panel().getAllByRole("article")[1].textContent).toContain("测试可选2");
    expect(JSON.stringify(input)).toBe(original);
  });
  it("refresh start, annotation ABA and generation clear authorization, but refresh preserves draft; failed refresh is explicit", async () => {
    await ready(); await click("检查天气对行程的影响");
    const draft = screen.getByRole("region", { name: "行程草案" }).textContent;
    const pending = deferred<WeatherForecastResponse>(); api.weather.mockReturnValueOnce(pending.promise);
    await click("刷新天气");
    expect(panel().getByRole("button", { name: "预览排除该地点后的草案" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("region", { name: "行程草案" }).textContent).toBe(draft);
    await act(async () => pending.reject(new Error("controlled failure")));
    expect(panel().getByRole("button", { name: "预览排除该地点后的草案" }).hasAttribute("disabled")).toBe(true);
    await click("检查天气对行程的影响");
    expect(panel().getByText(/基于上次成功查询的天气/)).toBeTruthy();
    const select = panel().getAllByRole("combobox")[0];
    fireEvent.change(select, { target: { value: "indoor" } }); fireEvent.change(select, { target: { value: "unknown" } });
    expect(panel().getByRole("button", { name: "预览排除该地点后的草案" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("region", { name: "行程草案" }).textContent).toBe(draft);
    await click("检查天气对行程的影响"); await click("生成行程草案");
    expect(panel().getByRole("button", { name: "预览排除该地点后的草案" }).hasAttribute("disabled")).toBe(true);
  });
  it("stale and unknown basis show unassessed without a weather adjustment button; ordinary candidate choice remains", async () => {
    api.weather.mockImplementation(async () => ({ ...forecast(), reported_at: null, report_time_status: "missing", freshness_at_query: "unknown" }));
    await ready(); await click("检查天气对行程的影响");
    expect(panel().getAllByText("白天：未评估")).toHaveLength(2);
    expect(panel().queryByRole("button", { name: "预览排除该地点后的草案" })).toBeNull();
    expect(screen.getByRole("button", { name: "排除可选地点：同名地点" }).hasAttribute("disabled")).toBe(false);
  });
  it("Mock dates, old map bindings/route/Marker do not invalidate the check, and weather check does not alter them", async () => {
    await ready(); await click("检查天气对行程的影响");
    await click("预览排除该地点后的草案");
    const preview = screen.getByRole("region", { name: "调整预览" }).textContent;
    for (const label of ["甲", "乙"]) { await click(`为测试活动${label}绑定地点`); await click("测试确认地点"); }
    await click("查询步行路线"); await click(`测试 Marker ${required.id}`);
    expect(panel().getByRole("button", { name: "预览排除该地点后的草案" }).hasAttribute("disabled")).toBe(false);
    const map = screen.getByTestId("map-state").textContent;
    fireEvent.click(screen.getByRole("button", { name: /Day 2/ }));
    expect(panel().getByRole("button", { name: "预览排除该地点后的草案" }).hasAttribute("disabled")).toBe(false);
    expect(screen.getByRole("region", { name: "调整预览" }).textContent).toBe(preview);
    expect(screen.getByRole("button", { name: "采用此方案" }).hasAttribute("disabled")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: /Day 1/ }));
    expect(screen.getByTestId("map-state").textContent).not.toBeNull();
    await click("检查天气对行程的影响");
    expect(screen.getByRole("button", { name: "采用此方案" }).hasAttribute("disabled")).toBe(true);
    expect(map).toContain(required.id);
    expect(api.weather).toHaveBeenCalledTimes(1); expect(api.schedule).toHaveBeenCalledTimes(2); expect(api.walking).toHaveBeenCalledTimes(1);
  });
  it("a failed regeneration leaves no old draft/report permission; restoring a candidate permits an explicit retry", async () => {
    await ready(); await click("检查天气对行程的影响"); await click("排除可选地点：同名地点");
    api.schedule.mockRejectedValueOnce(new Error("controlled failure")); await click("生成行程草案");
    expect(screen.queryByRole("list", { name: "草案时间线" })).toBeNull();
    expect(panel().getByRole("button", { name: "预览排除该地点后的草案" }).hasAttribute("disabled")).toBe(true);
    await click("恢复可选地点：同名地点"); expect(api.schedule).toHaveBeenCalledTimes(2);
    await click("生成行程草案"); expect(panel().getAllByRole("combobox")).toHaveLength(2);
    expect(panel().getByRole("button", { name: "预览排除该地点后的草案" }).hasAttribute("disabled")).toBe(true);
  });
  it("both full timelines and ID-based changes remain viewable; cancel does not publish fourth-candidate settings", async () => {
    await ready(); await click("检查天气对行程的影响");
    const original = screen.getByRole("region", { name: "行程草案" }).textContent;
    await click("预览排除该地点后的草案");
    const preview = within(screen.getByRole("region", { name: "调整预览" }));
    expect(preview.getByRole("region", { name: "新旧地点对比" }).textContent).toContain("新增 1 个 · 移除 1 个 · 保留 1 个");
    expect(preview.getByRole("region", { name: "新旧地点对比" }).textContent).toContain("同名地点 · 必去");
    expect(within(preview.getByRole("region", { name: "新旧地点对比" })).getByText(/测试可选2.*2026-10-10/).textContent).toContain("不确定");
    fireEvent.click(preview.getByText("查看原方案完整时间轴与安排情况"));
    fireEvent.click(preview.getByText("查看调整预览完整时间轴与未安排原因"));
    expect(preview.getAllByRole("list", { name: "草案时间线" })).toHaveLength(2);
    expect(preview.getByRole("region", { name: "住宿返程对比" }).textContent).toContain("2026-10-10");
    await click("取消预览");
    expect(screen.queryByRole("region", { name: "调整预览" })).toBeNull();
    expect(screen.getByRole("region", { name: "行程草案" }).textContent).toBe(original);
    expect(screen.queryByRole("button", { name: "恢复可选地点：同名地点" })).toBeNull();
    expect(api.schedule).toHaveBeenCalledTimes(2);
  });
  it("an unadoptable result stays visible with reasons, then weather refresh expires it without clearing original", async () => {
    await ready(); await click("检查天气对行程的影响");
    const original = screen.getByRole("region", { name: "行程草案" }).textContent;
    api.schedule.mockImplementationOnce(async (r) => adjustmentResponse(r, "empty"));
    await click("预览排除该地点后的草案");
    expect(screen.getByRole("button", { name: "采用此方案" }).hasAttribute("disabled")).toBe(true);
    const preview = within(screen.getByRole("region", { name: "调整预览" }));
    expect(preview.getByRole("alert").textContent).toContain("必去");
    fireEvent.click(preview.getByText("查看调整预览完整时间轴与未安排原因"));
    expect(preview.getByText("受控测试：未安排")).toBeTruthy();
    await click("刷新天气");
    expect(preview.getByText(/条件已变化/)).toBeTruthy();
    expect(screen.getByRole("region", { name: "行程草案" }).textContent).toBe(original);
  });
});
