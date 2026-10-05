import { beforeEach, describe, expect, it, vi } from "vitest";
import { planTrip } from "./api";
import { isConfirmedPlace, type Place } from "@/types/place";
import type { TripPlan, TripRequest } from "@/types/trip";

const place: Place = { id: "fixture-a", name: "测试地标", address: "测试地址",
  longitude: 120, latitude: 30, category: "测试类别", source: "amap" };
const second: Place = { ...place, id: "fixture-b", address: "另一个地址" };

function request(): TripRequest {
  return { start_date: "2026-10-10", end_date: "2026-10-11", budget: 3000,
    travelers: 2, accommodation_location: place.name, accommodation_place: { ...place },
    pace: "balanced", interests: [], must_visit: [place.name, second.name],
    must_visit_places: [{ ...place }, { ...second }], avoid_places: [],
    daily_start_time: "09:00", daily_end_time: "21:00" };
}
function plan(input = request()): TripPlan {
  return { destination: "上海", request: input, currency: "CNY", is_mock: true,
    notice: "Mock 示例", estimated_cost: 50,
    budget_breakdown: { transport: 0, food: 0, tickets: 50, other: 0 },
    days: [{ day: 1, date: "2026-10-10", title: "Mock 测试", transports: [],
      weather: { date: "2026-10-10", condition: "Mock 晴", min_temperature: 18, max_temperature: 25, rain_risk: 10 },
      activities: [{ id: "mock-one", name: "Mock 活动", category: "sightseeing", estimated_cost: 50,
        description: "未根据需求排程", start_time: "09:00", end_time: "10:00" }] }] };
}
let fetchMock: ReturnType<typeof vi.fn<typeof fetch>>;
beforeEach(() => { fetchMock = vi.fn<typeof fetch>(); vi.stubGlobal("fetch", fetchMock); });

describe("structured confirmed trip requirements", () => {
  it("posts the complete Place contract and validates its unchanged echo without modifying Mock activities", async () => {
    const input = request();
    const response = plan(input);
    fetchMock.mockResolvedValue(new Response(JSON.stringify(response)));
    expect(await planTrip(input)).toEqual(response);
    expect(JSON.parse(fetchMock.mock.calls[0][1]?.body as string)).toEqual(input);
    expect(input.must_visit_places?.map(p => p.id)).toEqual([place.id, second.id]);
  });

  it("accepts an empty confirmed must-visit list and nullable address/category", async () => {
    const input = request();
    input.must_visit = []; input.must_visit_places = [];
    input.accommodation_place = { ...place, address: null, category: null };
    fetchMock.mockResolvedValue(new Response(JSON.stringify(plan(input))));
    expect((await planTrip(input)).request).toEqual(input);
  });

  it("does not apply legacy echo tolerance to an explicitly empty new must-visit request", async () => {
    const input = { ...request(), must_visit: [], must_visit_places: [] };
    const output = plan({ ...input, must_visit: ["错误夹带的旧版文本"] });
    fetchMock.mockResolvedValue(new Response(JSON.stringify(output)));
    await expect(planTrip(input)).rejects.toThrow("返回的行程格式不正确");
  });

  it.each([false, true])("accepts old text-only requests and legacy echoes (new defaults=%s)", async (defaults) => {
    const input = request();
    delete input.accommodation_place; delete input.must_visit_places;
    input.accommodation_location = "旧版区域"; input.must_visit = ["旧版景点"];
    const echoed = defaults ? { ...input, accommodation_place: null, must_visit_places: [] } : input;
    fetchMock.mockResolvedValue(new Response(JSON.stringify(plan(echoed))));
    expect((await planTrip(input)).request.must_visit).toEqual(["旧版景点"]);
  });

  it.each([
    { id: " " }, { name: " " }, { source: "other" }, { source: undefined },
    { latitude: "30" }, { longitude: true }, { latitude: NaN }, { longitude: Infinity },
    { latitude: 91 }, { longitude: -181 }, { address: {} }, { category: 42 },
    { address: undefined }, { category: undefined }, { price: 100 },
  ])("rejects malformed structured Places rather than silently dropping fields: %j", async (patch) => {
    const malformed = { ...place, ...patch };
    expect(isConfirmedPlace(malformed)).toBe(false);
    const input = { ...request(), accommodation_place: malformed } as unknown as TripRequest;
    await expect(planTrip(input)).rejects.toThrow("已确认地点数据无效");
    expect(fetchMock).not.toHaveBeenCalled();
    const output = plan();
    output.request.must_visit_places = [malformed] as unknown as Place[];
    output.request.must_visit = [place.name];
    fetchMock.mockResolvedValue(new Response(JSON.stringify(output)));
    await expect(planTrip(request())).rejects.toThrow("返回的行程格式不正确");
  });

  it.each(["duplicate", "accommodation-name", "must-names", "explicit-empty", "null-list"])("rejects conflicting outbound fields: %s", async (kind) => {
    const input = request();
    if (kind === "duplicate") input.must_visit_places = [place, { ...place, id: ` ${place.id} ` }];
    if (kind === "accommodation-name") input.accommodation_location = "不同名称";
    if (kind === "must-names") input.must_visit = ["不同名称", second.name];
    if (kind === "explicit-empty") input.must_visit_places = [];
    if (kind === "null-list") (input as unknown as Record<string, unknown>).must_visit_places = null;
    await expect(planTrip(input)).rejects.toThrow("已确认地点数据无效");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(["missing-accommodation", "missing-must", "changed-id", "changed-coordinates", "duplicate", "inconsistent-text"])("rejects a lost or changed structured echo: %s", async (kind) => {
    const output = plan();
    if (kind === "missing-accommodation") delete output.request.accommodation_place;
    if (kind === "missing-must") delete output.request.must_visit_places;
    if (kind === "changed-id") output.request.accommodation_place!.id = "changed-id";
    if (kind === "changed-coordinates") output.request.accommodation_place!.longitude = 121;
    if (kind === "duplicate") output.request.must_visit_places = [place, place];
    if (kind === "inconsistent-text") output.request.must_visit = ["不同", second.name];
    fetchMock.mockResolvedValue(new Response(JSON.stringify(output)));
    await expect(planTrip(request())).rejects.toThrow("返回的行程格式不正确");
  });

  it.each([422, 500])("does not treat error bodies as confirmed places or show raw details (%s)", async (status) => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ ...plan(), detail: "private-upstream-details" }), { status }));
    await expect(planTrip(request())).rejects.not.toThrow("private-upstream-details");
  });
});
