import type { ComponentProps } from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import TripPlanResult from "@/components/trip-plan-result";
import type PlaceExplorer from "@/components/places/place-explorer";
import type { Place } from "@/types/place";
import type { TripPlan, TripRequest } from "@/types/trip";
import type { ScheduleEdge, ScheduleItem, ScheduleRequest, ScheduleResponse } from "@/types/schedule";
import { isScheduleResponse } from "@/types/schedule";
import { getWeatherDates, type WeatherForecastRequest, type WeatherForecastResponse } from "@/types/weather";

const controls = vi.hoisted(() => ({ weather: vi.fn(), candidates: vi.fn(), schedule: vi.fn(), walking: vi.fn() }));
vi.mock("@/lib/weather-api", async (original) => ({ ...await original<typeof import("@/lib/weather-api")>(), queryWeatherForecast: controls.weather }));
vi.mock("@/lib/candidates-api", async (original) => ({ ...await original<typeof import("@/lib/candidates-api")>(), queryCandidates: controls.candidates }));
vi.mock("@/lib/schedule-api", async (original) => ({ ...await original<typeof import("@/lib/schedule-api")>(), querySchedulePreview: controls.schedule }));
vi.mock("@/lib/routes-api", async (original) => ({ ...await original<typeof import("@/lib/routes-api")>(), queryWalkingRoute: controls.walking }));
// No live SDK: retain the parent result's real binding/route state and callbacks.
vi.mock("@/components/places/place-explorer", () => ({ default: (props: ComponentProps<typeof PlaceExplorer>) => <section aria-label="测试地图">
  {props.bindingTarget && <button onClick={() => props.bindingTarget!.onConfirm(props.bindingTarget!.keyword === "测试活动甲" ? required : optional)}>测试确认地点</button>}
  {props.itinerary?.places.map((place) => <button key={place.id} onClick={() => props.itinerary!.onSelect(place.id)}>测试 Marker {place.name}</button>)}
  <output data-testid="map-state">{JSON.stringify({ ids: props.itinerary?.places.map((place) => place.id), selected: props.itinerary?.selectedPlaceId, walking: props.walkingRoute, transit: props.transitRoute })}</output>
</section> }));

// Fictional offline fixtures. The weather tests do not call AMap or alter its data.
const home: Place = { id: "weather-fixture-home", name: "测试住宿", address: "测试住宿地址", longitude: 121, latitude: 31, category: "测试地标", source: "amap" };
const required: Place = { ...home, id: "weather-fixture-required", name: "测试必去", longitude: 121.01 };
const optional: Place = { ...home, id: "weather-fixture-optional", name: "测试可选", latitude: 31.01 };
const stamp = "2026-10-10T01:00:00Z";
function makeRequest(): TripRequest {
  return { start_date: "2026-10-10", end_date: "2026-10-12", daily_start_time: "09:00", daily_end_time: "18:00", accommodation_location: home.name, accommodation_place: home, must_visit: [required.name], must_visit_places: [required], budget: 3000, travelers: 2, pace: "balanced", interests: ["摄影"], avoid_places: [] };
}
function makePlan(request = makeRequest()): TripPlan {
  return { request, destination: "上海", estimated_cost: 20, currency: "CNY", is_mock: true, notice: "测试 Mock 说明", budget_breakdown: { transport: 0, food: 0, tickets: 20, other: 0 },
    // Deliberately the fixed old two-day example, unrelated to three requested dates.
    days: [1, 2].map((day) => ({ day, date: `2026-10-${9 + day}`, title: `旧第${day}天`, transports: [], activities: ["甲", "乙"].map((label, index) => ({ id: `mock-${index}`, name: `测试活动${label}`, category: "sightseeing" as const, estimated_cost: 10, description: "Mock 描述", start_time: "09:00", end_time: "10:00" })), weather: { date: `2026-10-${9 + day}`, condition: "Mock 晴", min_temperature: 18, max_temperature: 25, rain_risk: 10 } })) };
}
function forecast(request: WeatherForecastRequest): WeatherForecastResponse {
  const period = { weather: "小雨", temperature_celsius: 0, wind_direction: "东", wind_power: "≤3", precipitation: "rain" as const, precipitation_basis: "小雨" };
  return { request, source: "amap", city: "上海市", adcode: "310000", timezone: "Asia/Shanghai", queried_at: stamp, reported_at: "2026-10-10T08:00:00+08:00", report_time_status: "valid", freshness_at_query: "fresh", guidance_rule: "amap_text_precipitation_v1", coverage: "complete", days: getWeatherDates(request).map((date) => ({ date, status: "available", day: period, night: period })) };
}
function schedule(request: ScheduleRequest): ScheduleResponse {
  const edges: ScheduleEdge[] = [], items: ScheduleItem[] = [];
  let minutes = 9 * 60, from = request.accommodation_place;
  const clock = (value: number) => `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
  function append(duration: number, input: Partial<ScheduleItem> & Pick<ScheduleItem, "kind">) {
    items.push({ start_time: clock(minutes), end_time: clock(minutes + duration), duration_minutes: duration, place_id: null, from_place_id: null, to_place_id: null, edge_id: null, duration_source: null, ...input }); minutes += duration;
  }
  function move(to: Place) {
    const id = `edge-${edges.length}`;
    edges.push({ id, origin: { place_id: from.id, longitude: from.longitude, latitude: from.latitude }, destination: { place_id: to.id, longitude: to.longitude, latitude: to.latitude }, transport_mode: "walking", status: "ok", duration_seconds: 60, duration_minutes: 1, distance_meters: 80, source: "amap", queried_at: stamp, message: null, used: true, transit_route: null, selection_rule: null });
    append(1, { kind: "walk", from_place_id: from.id, to_place_id: to.id, edge_id: id }); from = to;
  }
  for (const place of [...request.must_visit_places, ...(request.optional_places ?? [])]) {
    move(place);
    const setting = request.duration_settings.find((item) => item.place_id === place.id)!;
    append(setting.minutes, { kind: "visit", place_id: place.id, duration_source: setting.source });
  }
  move(request.accommodation_place);
  const dates = getWeatherDates({ start_date: request.start_date, end_date: request.end_date });
  const result: ScheduleResponse = { status: "complete", generated_at: stamp, request: { ...request, transport_mode: "walking" }, edges, days: dates.map((date, index) => ({ date, items: index ? [] : items, return_time: index ? null : clock(minutes) })), unscheduled: [], rules: ["离线步行测试"], unknowns: ["预算未验证"], optional_results: (request.optional_places ?? []).map((place) => ({ place_id: place.id, scheduled_date: request.start_date, not_attempted_reason: null, attempts: [{ date: request.start_date, outcome: "scheduled", message: "安排在尾部" }] })) };
  expect(isScheduleResponse(result, request)).toBe(true);
  return result;
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}
const panel = () => within(screen.getByRole("region", { name: "上海逐日天气预报" }));

beforeEach(() => {
  vi.clearAllMocks();
  controls.weather.mockImplementation(async (request: WeatherForecastRequest) => forecast(request));
  controls.schedule.mockImplementation(async (request: ScheduleRequest) => schedule(request));
  controls.walking.mockResolvedValue({ status: "ok", source: "amap", queried_at: stamp, route: { distance_meters: 80, duration_seconds: 60, segments: [[[121.01, 31], [121, 31.01]]] } });
  controls.candidates.mockResolvedValue({ status: "success", queried_at: stamp, keywords: ["公园"], queries: [{ interest: "摄影", keyword: "公园", status: "success", result_count: 1, message: null }], candidates: [{ place: required, role: "must_visit", retrieval_sources: [] }, { place: optional, role: "optional", retrieval_sources: [{ interest: "摄影", keyword: "公园" }] }] });
});

describe("weather isolation in existing trip results", () => {
  it("uses confirmed request dates, is ordered before candidates and never follows the old date selector", async () => {
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    const region = screen.getByRole("region", { name: "上海逐日天气预报" });
    expect(region.previousElementSibling).toBe(screen.getByRole("region", { name: "已确认需求地点" }));
    expect(region.nextElementSibling).toBe(screen.getByRole("region", { name: "候选地点准备" }));
    expect(panel().getAllByRole("article")).toHaveLength(3);
    fireEvent.click(screen.getByRole("button", { name: /Day 2/ }));
    fireEvent.click(within(screen.getByRole("group", { name: "草案交通方式" })).getByRole("button", { name: "公交／地铁" }));
    expect(controls.weather).not.toHaveBeenCalled();
    fireEvent.click(panel().getByRole("button", { name: "查询天气" }));
    await waitFor(() => expect(panel().getByRole("button", { name: "刷新天气" })).toBeTruthy());
    expect(controls.weather).toHaveBeenCalledWith({ start_date: "2026-10-10", end_date: "2026-10-12" }, expect.any(AbortSignal));
    fireEvent.click(screen.getByRole("button", { name: /Day 1/ }));
    expect(panel().getAllByText("小雨")).toHaveLength(6);
    expect(controls.weather).toHaveBeenCalledTimes(1);
    expect(within(screen.getByRole("region", { name: "旧 Mock 行程示例" })).getByText("Mock 晴")).toBeTruthy();
  });

  it("weather refresh preserves candidates, draft, bindings and selected route; other modules never query weather", async () => {
    const plan = makePlan(), original = JSON.stringify(plan);
    render(<TripPlanResult plan={plan} onEdit={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "获取候选地点" }));
    await screen.findByRole("button", { name: "排除可选地点：测试可选" });
    fireEvent.click(screen.getByRole("button", { name: "生成行程草案" }));
    await screen.findByRole("list", { name: "草案时间线" });
    for (const label of ["甲", "乙"]) {
      fireEvent.click(screen.getByRole("button", { name: `为测试活动${label}绑定地点` }));
      fireEvent.click(screen.getByRole("button", { name: "测试确认地点" }));
    }
    fireEvent.click(screen.getByRole("button", { name: "查询步行路线" }));
    await screen.findByRole("button", { name: "在地图查看路线" });
    const draft = screen.getByRole("region", { name: "行程草案" }).textContent;
    const candidateState = screen.getByRole("region", { name: "候选地点准备" }).textContent;
    const mapState = screen.getByTestId("map-state").textContent;
    expect(controls.weather).not.toHaveBeenCalled();

    fireEvent.click(panel().getByRole("button", { name: "查询天气" }));
    await waitFor(() => expect(panel().getByRole("button", { name: "刷新天气" })).toBeTruthy());
    fireEvent.click(panel().getByRole("button", { name: "刷新天气" }));
    await waitFor(() => expect(panel().getByRole("button", { name: "刷新天气" })).toBeTruthy());
    expect(screen.getByRole("region", { name: "行程草案" }).textContent).toBe(draft);
    expect(screen.getByRole("region", { name: "候选地点准备" }).textContent).toBe(candidateState);
    expect(screen.getByTestId("map-state").textContent).toBe(mapState);
    expect(controls.candidates).toHaveBeenCalledTimes(1);
    expect(controls.schedule).toHaveBeenCalledTimes(1);
    expect(controls.walking).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "测试 Marker 测试必去" }));
    fireEvent.click(screen.getByRole("button", { name: "排除可选地点：测试可选" }));
    fireEvent.click(screen.getByRole("button", { name: "恢复可选地点：测试可选" }));
    fireEvent.click(within(screen.getByRole("group", { name: "草案交通方式" })).getByRole("button", { name: "公交／地铁" }));
    fireEvent.click(screen.getByRole("button", { name: /Day 2/ }));
    expect(controls.weather).toHaveBeenCalledTimes(2);
    expect(panel().getAllByText("小雨")).toHaveLength(6);
    expect(JSON.stringify(plan)).toBe(original);
  });

  it("invalidates a pending weather request before onEdit and keeps a remounted result idle", async () => {
    const pending = deferred<WeatherForecastResponse>();
    controls.weather.mockReturnValueOnce(pending.promise);
    const onEdit = vi.fn(() => expect(signal.aborted).toBe(true));
    const view = render(<TripPlanResult plan={makePlan()} onEdit={onEdit} />);
    fireEvent.click(panel().getByRole("button", { name: "查询天气" }));
    const signal = controls.weather.mock.calls[0][1] as AbortSignal;
    fireEvent.click(screen.getByRole("button", { name: "修改旅行需求" }));
    expect(onEdit).toHaveBeenCalledTimes(1);
    expect(panel().getByRole("button", { name: "查询天气" })).toBeTruthy();
    view.unmount();
    render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    await act(async () => pending.resolve(forecast({ start_date: "2026-10-10", end_date: "2026-10-12" })));
    expect(panel().getByRole("button", { name: "查询天气" })).toBeTruthy();
    expect(panel().queryByText(/来源：高德天气/)).toBeNull();
    expect(controls.weather).toHaveBeenCalledTimes(1);
  });

  it("changing confirmed dates cancels pending weather before the old success can populate the new dates", async () => {
    const pending = deferred<WeatherForecastResponse>();
    controls.weather.mockReturnValueOnce(pending.promise);
    const view = render(<TripPlanResult plan={makePlan()} onEdit={vi.fn()} />);
    fireEvent.click(panel().getByRole("button", { name: "查询天气" }));
    const signal = controls.weather.mock.calls[0][1] as AbortSignal;
    view.rerender(<TripPlanResult plan={makePlan({ ...makeRequest(), start_date: "2026-12-31", end_date: "2027-01-02" })} onEdit={vi.fn()} />);
    expect(signal.aborted).toBe(true);
    await act(async () => pending.resolve(forecast({ start_date: "2026-10-10", end_date: "2026-10-12" })));
    expect(panel().getByRole("article", { name: "2027-01-02 天气预报" })).toBeTruthy();
    expect(panel().queryByText(/来源：高德天气/)).toBeNull();
    expect(controls.weather).toHaveBeenCalledTimes(1);
  });
});
