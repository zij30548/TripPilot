import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import TripPlanResult from "../trip-plan-result";
import { adjustmentResponse } from "@/lib/schedule-adjustment.test-fixtures";
import { candidateResponse, deferred, forecast, now, optionals, plan, stamp } from "@/lib/weather-schedule-check.test-fixtures";
import type { ScheduleCapture } from "@/lib/schedule-adjustment";
import type { ScheduleRequest, ScheduleResponse } from "@/types/schedule";
import type { IntentResponse } from "@/lib/adjustment-intent-api";

const api = vi.hoisted(() => ({ schedule: vi.fn(), candidates: vi.fn(), weather: vi.fn(), explanation: vi.fn(), intent: vi.fn() }));
vi.mock("@/lib/schedule-api", async o => ({ ...await o<typeof import("@/lib/schedule-api")>(), querySchedulePreview: api.schedule }));
vi.mock("@/lib/candidates-api", async o => ({ ...await o<typeof import("@/lib/candidates-api")>(), queryCandidates: api.candidates }));
vi.mock("@/lib/weather-api", async o => ({ ...await o<typeof import("@/lib/weather-api")>(), queryWeatherForecast: api.weather }));
vi.mock("@/lib/explanation-api", async o => ({ ...await o<typeof import("@/lib/explanation-api")>(), queryExplanation: api.explanation }));
vi.mock("@/lib/adjustment-intent-api", async o => ({ ...await o<typeof import("@/lib/adjustment-intent-api")>(), queryAdjustmentIntent: api.intent }));
vi.mock("@/components/places/place-explorer", () => ({ default: () => <div>受控地图保持独立</div> }));
const click = async (name: string) => act(async () => { fireEvent.click(screen.getByRole("button", { name })); });
const field = () => screen.getByRole("textbox", { name: "调整要求" });
const counts = () => Object.values(api).map(f => f.mock.calls.length);
function intent(base: ScheduleCapture, id: string): IntentResponse {
  return { contract_version: "adjustment-intent-v1", request_id: id, draft_version: base.version, schedule_generated_at: base.original.generated_at, interpreted_at: stamp,
    model: "qwen3.7-flash-2026-07-15", usage: { input_tokens: null, output_tokens: null }, selection: { decision: "execute", action: "exclude_optional", target_id: optionals[0].id, reason_code: "single_optional" } };
}
beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] }); vi.setSystemTime(now); vi.resetAllMocks();
  api.schedule.mockImplementation(async (r: ScheduleRequest) => adjustmentResponse(r));
  api.candidates.mockImplementation(async () => candidateResponse()); api.weather.mockImplementation(async () => forecast());
  api.intent.mockImplementation(async (base: ScheduleCapture, _text: string, id: string) => intent(base,id));
  api.explanation.mockImplementation(async (r: ScheduleResponse, id: string) => ({ contract_version: "schedule-explanation-v1", request_id: id, schedule_generated_at: r.generated_at,
    explained_at: stamp, model: "qwen3.7-flash-2026-07-15", highlights: [{ message_code: "arrangement", fact_ids: ["f0"], text: "当前正式草案受控解读" }], limitations: ["一", "二", "三"], usage: { input_tokens: null, output_tokens: null } }));
});
afterEach(() => vi.useRealTimers());
async function ready() {
  const view=render(<TripPlanResult plan={plan()} onEdit={vi.fn()} />);
  await click("获取候选地点"); await click("生成行程草案"); await click("解释当前行程");
  return view;
}
describe("actual result composition with controlled intent transport", () => {
  it("input/enter are inert; preview/cancel keep AI and budget; adopt updates shared candidate and clears AI", async () => {
    await ready(); const before=counts();
    fireEvent.change(field(),{target:{value:`排除${optionals[0].id}`}}); fireEvent.keyDown(field(),{key:"Enter"}); expect(counts()).toEqual(before);
    await click("生成调整预览"); expect(screen.getByText("当前正式草案受控解读")).toBeTruthy();
    expect(screen.getByRole("region",{name:"新旧地点对比"})).toBeTruthy();
    const fees=within(screen.getByRole("region",{name:"正式草案费用与预算"})); const n=counts();
    fireEvent.change(fees.getByRole("textbox",{name:/全程其他费用/}),{target:{value:"15"}});
    expect(counts()).toEqual(n); expect(screen.getByRole("button",{name:"采用此方案"})).not.toHaveProperty("disabled",true);
    await click("查询天气"); expect(screen.getByRole("button",{name:"采用此方案"})).not.toHaveProperty("disabled",true);
    await click("取消预览"); expect(screen.getByText("当前正式草案受控解读")).toBeTruthy(); expect(fees.getByRole("textbox",{name:/全程其他费用/})).toHaveProperty("value","15");
    await click("生成调整预览"); const prior=counts(); await click("采用此方案"); expect(counts()).toEqual(prior);
    expect(screen.queryByText("当前正式草案受控解读")).toBeNull();
    expect(within(screen.getByRole("region",{name:"候选地点准备"})).getByRole("button",{name:/恢复/})).toBeTruthy();
    await click("生成行程草案"); expect(api.schedule.mock.lastCall![0].optional_places.map((p:{id:string})=>p.id)).not.toContain(optionals[0].id);
  });
  it("clarification renders only program messages, no executable preview or arbitrary output", async () => {
    await ready(); api.intent.mockImplementationOnce(async (base,_text,id)=>({...intent(base,id),selection:{decision:"clarify",action:null,target_id:null,reason_code:"unclear_request"}}));
    fireEvent.change(field(),{target:{value:"轻松一点"}}); await click("生成调整预览");
    expect(within(screen.getByRole("region",{name:"自然语言调整"})).getByText(/请明确写出/)).toBeTruthy();
    expect(screen.queryByRole("button",{name:"采用此方案"})).toBeNull(); expect(api.schedule).toHaveBeenCalledTimes(1);
    expect(screen.getByText("当前正式草案受控解读")).toBeTruthy();
  });
  it("pending cancel and editing input discard stale success without model/schedule retries", async () => {
    await ready(); const pending=deferred<IntentResponse>(); api.intent.mockReturnValueOnce(pending.promise);
    fireEvent.change(field(),{target:{value:"排除一个地点"}}); await click("生成调整预览");
    expect(screen.getByRole("button",{name:"正在理解要求…"})).toHaveProperty("disabled",true);
    await click("取消自然语言调整"); expect(api.intent.mock.lastCall![3].aborted).toBe(true);
    await act(async()=>pending.resolve(intent(api.intent.mock.lastCall![0],api.intent.mock.lastCall![2])));
    expect(api.schedule).toHaveBeenCalledTimes(1); expect(screen.getByText("当前正式草案受控解读")).toBeTruthy();
  });
});
