import type { ComponentProps } from "react";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import TripPlanResult from "@/components/trip-plan-result";
import type PlaceExplorer from "@/components/places/place-explorer";
import { adjustmentResponse } from "@/lib/schedule-adjustment.test-fixtures";
import { candidateResponse, deferred, forecast, now, optionals, plan, required, stamp } from "@/lib/weather-schedule-check.test-fixtures";
import type { ExplanationResponse } from "@/lib/explanation-api";
import type { ScheduleRequest, ScheduleResponse } from "@/types/schedule";

const api = vi.hoisted(() => ({ weather: vi.fn(), candidates: vi.fn(), schedule: vi.fn(), explanation: vi.fn(), walking: vi.fn() }));
vi.mock("@/lib/weather-api", async (original) => ({ ...await original<typeof import("@/lib/weather-api")>(), queryWeatherForecast: api.weather }));
vi.mock("@/lib/candidates-api", async (original) => ({ ...await original<typeof import("@/lib/candidates-api")>(), queryCandidates: api.candidates }));
vi.mock("@/lib/schedule-api", async (original) => ({ ...await original<typeof import("@/lib/schedule-api")>(), querySchedulePreview: api.schedule }));
vi.mock("@/lib/explanation-api", async (original) => ({ ...await original<typeof import("@/lib/explanation-api")>(), queryExplanation: api.explanation }));
vi.mock("@/lib/routes-api", async (original) => ({ ...await original<typeof import("@/lib/routes-api")>(), queryWalkingRoute: api.walking }));
vi.mock("@/components/places/place-explorer", () => ({ default: (props: ComponentProps<typeof PlaceExplorer>) => <section aria-label="受控地图">
  {props.bindingTarget && <button onClick={() => props.bindingTarget!.onConfirm(props.bindingTarget!.keyword === "测试活动甲" ? required : optionals[0])}>受控确认绑定</button>}
  {props.itinerary?.places.map((p) => <button key={p.id} onClick={() => props.itinerary!.onSelect(p.id)}>受控 Marker {p.id}</button>)}
  <output data-testid="explanation-map">{JSON.stringify({ selected: props.itinerary?.selectedPlaceId, route: props.walkingRoute })}</output>
</section> }));
const click = async (name: string) => { await act(async () => fireEvent.click(screen.getByRole("button", { name }))); };
const ai = () => within(screen.getByRole("region", { name: "AI 行程解读" }));
const mockText = '<script>不执行</script> 受控依据：本次必去与可选，非真实千问验收';
const counts = () => Object.values(api).map((method) => method.mock.calls.length);
function explanation(response: ScheduleResponse, id: string): ExplanationResponse {
  return { contract_version: "schedule-explanation-v1", request_id: id, model: "qwen3.7-flash-2026-07-15", schedule_generated_at: response.generated_at, explained_at: stamp,
    highlights: [{ message_code: "arrangement", fact_ids: ["f0"], text: mockText }], limitations: ["基于提交快照", "未核验营业预约", "参考估时"], usage: { input_tokens: null, output_tokens: null } };
}
async function ready() {
  const view = render(<TripPlanResult plan={plan()} onEdit={vi.fn()} />);
  await click("获取候选地点"); await click("生成行程草案"); await click("查询天气"); await click("检查天气对行程的影响");
  expect(api.explanation).not.toHaveBeenCalled(); await click("解释当前行程"); return view;
}
beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] }); vi.setSystemTime(now); vi.resetAllMocks();
  api.schedule.mockImplementation(async (r: ScheduleRequest) => adjustmentResponse(r));
  api.explanation.mockImplementation(async (r: ScheduleResponse, id: string) => explanation(r, id));
  api.weather.mockImplementation(async () => forecast()); api.candidates.mockImplementation(async () => candidateResponse());
  api.walking.mockResolvedValue({ status: "ok", source: "amap", queried_at: stamp, route: { distance_meters: 80, duration_seconds: 60, segments: [[[121.01, 31], [121.1, 31]]] } });
});
afterEach(() => vi.useRealTimers());

describe("AI explanation in actual result composition (controlled services)", () => {
  it("plain text only; fees, weather, labels, old map, dates and preview/cancel do not clear or requery", async () => {
    await ready(); expect(ai().getByText(mockText)).toBeTruthy(); expect(screen.getByRole("region", { name: "AI 行程解读" }).querySelector("script")).toBeNull();
    const n = counts();
    fireEvent.change(within(screen.getByRole("region", { name: "正式草案费用与预算" })).getByRole("textbox", { name: /全程其他费用/ }), { target: { value: "12.50" } });
    expect(counts()).toEqual(n); expect(ai().getByText(mockText)).toBeTruthy();
    await click("预览排除该地点后的草案"); expect(ai().getByText(mockText)).toBeTruthy(); await click("取消预览");
    await click("刷新天气"); expect(ai().getByText(mockText)).toBeTruthy();
    fireEvent.change(within(screen.getByRole("region", { name: "天气与行程检查" })).getAllByRole("combobox")[0], { target: { value: "indoor" } });
    for (const label of ["甲", "乙"]) { await click(`为测试活动${label}绑定地点`); await click("受控确认绑定"); }
    await click("查询步行路线"); await click(`受控 Marker ${required.id}`); expect(ai().getByText(mockText)).toBeTruthy();
    const before = counts(), map = screen.getByTestId("explanation-map").textContent;
    fireEvent.change(within(screen.getByRole("region", { name: "正式草案费用与预算" })).getByRole("textbox", { name: /全程其他费用/ }), { target: { value: "0" } });
    expect(screen.getByTestId("explanation-map").textContent).toBe(map); expect(counts()).toEqual(before);
    fireEvent.click(within(screen.getByLabelText("选择行程日期")).getAllByRole("button")[1]);
    expect(ai().getByText(mockText)).toBeTruthy(); expect(api.explanation).toHaveBeenCalledTimes(1);
  });
  it("adopt and normal regeneration clear explanation; no implicit AI request", async () => {
    await ready(); await click("预览排除该地点后的草案"); const n = counts(); await click("采用此方案");
    expect(counts()).toEqual(n); expect(ai().queryByText(mockText)).toBeNull();
    await click("解释当前行程"); expect(api.explanation.mock.calls[1][0].request.optional_places.map((p: { id: string }) => p.id)).not.toContain(optionals[0].id);
    await click("生成行程草案"); expect(ai().queryByText(mockText)).toBeNull(); expect(api.explanation).toHaveBeenCalledTimes(2);
  });
  it("pending cancellation and fee rerenders leave formal budget/preview intact", async () => {
    await ready(); await click("预览排除该地点后的草案");
    const pending = deferred<ExplanationResponse>(); api.explanation.mockReturnValueOnce(pending.promise);
    await click("解释当前行程");
    fireEvent.change(within(screen.getByRole("region", { name: "正式草案费用与预算" })).getByRole("textbox", { name: /全程其他费用/ }), { target: { value: "20" } });
    expect(api.explanation.mock.calls[1][2].aborted).toBe(false);
    expect(screen.getByRole("button", { name: "采用此方案" }).hasAttribute("disabled")).toBe(false);
    const n = counts(); await click("取消解读"); expect(counts()).toEqual(n); expect(ai().getByText(/上游是否停止处理或计费无法保证/)).toBeTruthy();
    await act(async () => pending.resolve(explanation(api.explanation.mock.calls[1][0], "late")));
    expect(ai().queryByText(mockText)).toBeNull(); expect(screen.getByRole("button", { name: "采用此方案" }).hasAttribute("disabled")).toBe(false);
  });
});
