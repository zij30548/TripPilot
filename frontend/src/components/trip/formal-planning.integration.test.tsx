import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import TripPlanner from "./trip-planner";
import EditablePlanningSession from "./editable-planning-session";
import { newWorkspaceInputs } from "@/lib/restore-trip";
import { adjustmentResponse } from "@/lib/schedule-adjustment.test-fixtures";
import { candidateResponse, deferred, forecast, now, required, tripRequest } from "@/lib/weather-schedule-check.test-fixtures";
import type { SaveOperation } from "@/lib/saved-trips";
import type { ScheduleResponse } from "@/types/schedule";

const api = vi.hoisted(() => ({ search: vi.fn(), plan: vi.fn(), schedule: vi.fn(), candidates: vi.fn(), weather: vi.fn(), save: vi.fn(), model: vi.fn(), intent: vi.fn() }));
vi.mock("@/lib/api", async original => ({ ...await original<typeof import("@/lib/api")>(), planTrip: api.plan }));
vi.mock("@/lib/places-api", () => ({ searchPlaces: api.search }));
vi.mock("@/lib/schedule-api", async original => ({ ...await original<typeof import("@/lib/schedule-api")>(), querySchedulePreview: api.schedule }));
vi.mock("@/lib/candidates-api", async original => ({ ...await original<typeof import("@/lib/candidates-api")>(), queryCandidates: api.candidates }));
vi.mock("@/lib/weather-api", async original => ({ ...await original<typeof import("@/lib/weather-api")>(), queryWeatherForecast: api.weather }));
vi.mock("@/lib/saved-trips", async original => ({ ...await original<typeof import("@/lib/saved-trips")>(), saveTrip: api.save }));
vi.mock("@/lib/explanation-api", async original => ({ ...await original<typeof import("@/lib/explanation-api")>(), queryExplanation: api.model }));
vi.mock("@/lib/adjustment-intent-api", async original => ({ ...await original<typeof import("@/lib/adjustment-intent-api")>(), queryAdjustmentIntent: api.intent }));
const click = async (name: string | RegExp) => act(async () => { fireEvent.click(screen.getByRole("button", { name })); });
const change = (label: string | RegExp, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
const counts = () => Object.fromEntries(Object.entries(api).map(([name, mock]) => [name, mock.mock.calls.length]));
const section = (step: string) => document.querySelector<HTMLDetailsElement>(`[data-step="${step}"]`)!;
const collapse = async (step: string) => act(async () => { section(step).open = false; fireEvent(section(step), new Event("toggle")); });
const official = () => within(screen.getByRole("region", { name: "正式草案费用与预算" }));
const timeline = () => screen.getAllByRole("list", { name: "草案时间线" })[0].textContent;
function open(request = tripRequest()) { return render(<EditablePlanningSession sessionId="new-controlled-session" initial={newWorkspaceInputs(request)} origin={{ kind: "new" }} />); }
beforeEach(() => {
  vi.resetAllMocks(); vi.useFakeTimers({ toFake: ["Date"] }); vi.setSystemTime(now);
  api.schedule.mockImplementation(async r => adjustmentResponse(r)); api.candidates.mockResolvedValue(candidateResponse()); api.weather.mockResolvedValue(forecast());
  api.search.mockResolvedValue([tripRequest().accommodation_place, required]);
  api.save.mockImplementation(async (o: SaveOperation) => ({ id: "c".repeat(32), name: o.name, saved_at: "2026-10-12T00:00:00Z", snapshot: o.snapshot }));
});
afterEach(() => vi.useRealTimers());

describe("6C-1 formal homepage and stable presentation (controlled services)", () => {
  it("starts with a truly blank form; explicit Place preview is not confirmation; local submit never loads Mock or automatic business data", async () => {
    render(<TripPlanner><p>新建说明</p></TripPlanner>);
    expect(screen.getByLabelText(/开始日期/)).toHaveProperty("value", "");
    expect(Object.values(counts()).every(n => n === 0)).toBe(true);
    change(/开始日期/, "2026-10-10"); change(/结束日期/, "2026-10-10"); change(/总预算/, "3000");
    const submit = () => fireEvent.submit(screen.getByRole("button", { name: "确认需求，开始规划" }).closest("form")!);
    submit(); expect(screen.queryByText("上海旅行 · 正式规划工作区")).toBeNull();
    await click("选择住宿参考点"); change("搜索上海地点", "测试"); await click("搜索");
    await click(new RegExp(tripRequest().accommodation_place!.name));
    submit(); expect(screen.queryByText("上海旅行 · 正式规划工作区")).toBeNull();
    await click("确认住宿参考点"); submit();
    expect(screen.getByText("上海旅行 · 正式规划工作区")).toBeTruthy();
    expect(screen.queryByLabelText("历史工作副本来源")).toBeNull(); expect(screen.queryByText(/Mock 晴/)).toBeNull();
    expect(screen.getByRole("button", { name: "生成行程草案" })).toHaveProperty("disabled", true);
    expect(screen.getByText(/尚无必去地点：请先主动/)).toBeTruthy();
    for (const [name, count] of Object.entries(counts())) expect(count).toBe(name === "search" ? 1 : 0);
  });
  it("required-only generation needs no optional/weather/AI request and new initialization is independent and strict", async () => {
    const r = tripRequest(), seed = newWorkspaceInputs(r); seed.request.must_visit_places![0].name = "local";
    expect(r.must_visit_places![0].name).toBe(required.name);
    expect(() => newWorkspaceInputs({ ...r, accommodation_place: null })).toThrow();
    open(); expect(screen.getByText(/已有必去地点，可直接/)).toBeTruthy();
    await click("生成行程草案"); expect(api.schedule.mock.calls[0][0].optional_places).toEqual([]);
    expect(api.plan).not.toHaveBeenCalled(); expect(api.candidates).not.toHaveBeenCalled(); expect(api.weather).not.toHaveBeenCalled(); expect(api.model).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("历史工作副本来源")).toBeNull();
    expect(screen.getByRole("button", { name: "保存当前正式草案" })).toHaveProperty("disabled", false);
  });
  it("cancel edits preserves formal draft and fees; confirmation uses the same date/people cleanup and compatible settings as history", async () => {
    open(); change("停留分钟：同名地点", "75"); await click("生成行程草案");
    fireEvent.change(official().getByRole("textbox", { name: /全程其他/ }), { target: { value: "bad" } }); const before = timeline(), requests = counts();
    await click("修改旅行需求"); change(/旅行人数/, "3"); await click("取消需求修改");
    expect(timeline()).toBe(before); expect(official().getByRole("textbox", { name: /全程其他/ })).toHaveProperty("value", "bad"); expect(counts()).toEqual(requests);
    await click("修改旅行需求"); change(/旅行人数/, "3"); await click("确认需求修改");
    expect(screen.queryByRole("list", { name: "草案时间线" })).toBeNull(); expect(screen.getByLabelText("停留分钟：同名地点")).toHaveProperty("value", "75");
    expect(within(screen.getByRole("region", { name: "工作副本费用输入" })).getAllByRole("textbox").every(e => (e as HTMLInputElement).value === "")).toBe(true);
    expect(counts()).toEqual(requests);
  });
  it.each(["success", "error"])("confirmed edit isolates late old %s/finally across a new workspace", async outcome => {
    open(); const pending = deferred<ScheduleResponse>(); api.schedule.mockReturnValueOnce(pending.promise); await click("生成行程草案");
    const [body, signal] = api.schedule.mock.calls[0]; await click("修改旅行需求"); change(/总预算/, "5000"); await click("确认需求修改");
    expect(signal.aborted).toBe(true);
    expect(screen.getByRole("status", { name: "需求修改后的草案状态" }).textContent).toContain("请主动重新生成");
    await act(async () => { if (outcome === "success") pending.resolve(adjustmentResponse(body)); else pending.reject(new Error("old")); });
    expect(screen.queryByRole("list", { name: "草案时间线" })).toBeNull();
    expect(screen.getByRole("status", { name: "需求修改后的草案状态" }).textContent).toContain("请主动重新生成");
    await click("生成行程草案");
    expect(screen.queryByRole("status", { name: "需求修改后的草案状态" })).toBeNull();
    await click("保存当前正式草案");
    expect(api.save.mock.calls[0][0].snapshot.requirements.budget).toBe(5000); expect(api.plan).not.toHaveBeenCalled();
  });
  it("edited-requirement guidance follows loading/failure/success while fee cleanup facts and cancelled work survive", async () => {
    open(); await click("生成行程草案");
    fireEvent.change(official().getByRole("textbox", { name: /全程其他/ }), { target: { value: "25" } });
    await click("修改旅行需求"); change(/旅行人数/, "3"); await click("确认需求修改");
    const guidance = () => screen.queryByRole("status", { name: "需求修改后的草案状态" });
    expect(guidance()?.textContent).toContain("请主动重新生成");
    expect(screen.getByText(/全部用户费用估算已清空/)).toBeTruthy();
    const pending = deferred<ScheduleResponse>(); api.schedule.mockReturnValueOnce(pending.promise);
    await click("生成行程草案"); expect(guidance()?.textContent).toContain("正在生成");
    expect(guidance()?.textContent).not.toContain("请主动重新生成");
    await act(async () => pending.reject(new Error("controlled failure")));
    expect(guidance()?.textContent).toContain("请主动重新生成");
    await click("生成行程草案"); expect(guidance()).toBeNull();
    expect(screen.getByText(/已有有效正式草案/)).toBeTruthy();
    expect(screen.getByText(/全部用户费用估算已清空/)).toBeTruthy();
    const before = timeline(), requests = counts();
    await click("修改旅行需求"); change(/旅行人数/, "4"); await click("取消需求修改");
    expect(guidance()).toBeNull(); expect(timeline()).toBe(before); expect(counts()).toEqual(requests);
    expect(screen.getByText(/全部用户费用估算已清空/)).toBeTruthy();
  });
  it.each(["success", "error"])("a late invalidated %s cannot clear regeneration guidance within the same edited workspace", async outcome => {
    open(); await click("修改旅行需求"); change(/总预算/, "4000"); await click("确认需求修改");
    const pending = deferred<ScheduleResponse>(); api.schedule.mockReturnValueOnce(pending.promise);
    await click("生成行程草案"); const [body, signal] = api.schedule.mock.calls[0];
    change("停留分钟：同名地点", "75"); expect(signal.aborted).toBe(true);
    await act(async () => { if (outcome === "success") pending.resolve(adjustmentResponse(body)); else pending.reject(new Error("old")); });
    expect(screen.getByRole("status", { name: "需求修改后的草案状态" }).textContent).toContain("请主动重新生成");
    expect(screen.queryByRole("list", { name: "草案时间线" })).toBeNull();
    await click("生成行程草案");
    expect(screen.queryByRole("status", { name: "需求修改后的草案状态" })).toBeNull();
  });
  it("collapse/navigation preserves draft, fee input, uncertain frozen save content and operation ID", async () => {
    open(); await click("生成行程草案"); const before = timeline();
    fireEvent.change(official().getByRole("textbox", { name: /全程其他/ }), { target: { value: "12.30" } });
    api.save.mockRejectedValueOnce(new Error("结果未确认")); await click("保存当前正式草案");
    const frozen = structuredClone(api.save.mock.calls[0][0]); const requests = counts();
    await collapse("review"); await collapse("draft"); await click("1 · 需求与地点准备");
    await click("3 · 核对、调整与保存"); expect(section("review").open).toBe(true); expect(counts()).toEqual(requests);
    expect(official().getByRole("textbox", { name: /全程其他/ })).toHaveProperty("value", "12.30");
    fireEvent.change(official().getByRole("textbox", { name: /全程其他/ }), { target: { value: "99" } });
    await click("重试保存冻结快照"); expect(api.save.mock.calls[1][0]).toEqual(frozen);
    await click("2 · 生成与查看草案"); expect(timeline()).toBe(before);
  });
  it("optional-only inputs require explicit candidates; navigation cannot trigger retrieval", async () => {
    open({ ...tripRequest(), must_visit: [], must_visit_places: [] });
    await collapse("prepare"); await click("1 · 需求与地点准备"); expect(Object.values(counts()).every(n => n === 0)).toBe(true);
    await click("获取候选地点"); expect(api.schedule).not.toHaveBeenCalled(); await click("生成行程草案");
    expect(api.schedule.mock.calls[0][0].must_visit_places).toEqual([]); expect(api.schedule.mock.calls[0][0].optional_places).toHaveLength(3);
  });
  it("presentation and fee edits keep a controlled AI result and active preview; no real model call is made", async () => {
    open(); await click("获取候选地点"); await click("生成行程草案"); await click("查询天气"); await click("检查天气对行程的影响");
    api.model.mockImplementation(async (r: ScheduleResponse, id: string) => ({ contract_version: "schedule-explanation-v1", request_id: id,
      model: "controlled-test-only", schedule_generated_at: r.generated_at, explained_at: new Date(now).toISOString(),
      highlights: [{ message_code: "arrangement", fact_ids: ["f0"], text: "受控解读，不是真实模型验收" }], limitations: ["参考估时"], usage: { input_tokens: null, output_tokens: null } }));
    await click("解释当前行程"); await click("预览排除该地点后的草案"); const before = counts();
    await collapse("review"); await click("2 · 生成与查看草案"); await click("3 · 核对、调整与保存");
    fireEvent.change(official().getByRole("textbox", { name: /全程其他/ }), { target: { value: "20" } });
    expect(screen.getByText("受控解读，不是真实模型验收")).toBeTruthy();
    expect(screen.getByRole("button", { name: "采用此方案" })).toHaveProperty("disabled", false);
    expect(counts()).toEqual(before); await click("取消预览"); expect(counts()).toEqual(before);
    expect(screen.getByText("受控解读，不是真实模型验收")).toBeTruthy();
  });
  it("review navigation reaches adjustment; cancel retains formal/budget, adoption changes formal and does not query again", async () => {
    open(); await click("获取候选地点"); await click("生成行程草案"); await click("查询天气"); await click("检查天气对行程的影响");
    const before = timeline(); await click("预览排除该地点后的草案"); expect(screen.getByRole("button", { name: "采用此方案" })).toHaveProperty("disabled", false);
    await collapse("review"); const requests = counts(); await click("3 · 核对、调整与保存");
    expect(section("review").open).toBe(true); await click("取消预览"); expect(timeline()).toBe(before); expect(counts()).toEqual(requests);
    await click("预览排除该地点后的草案"); const readyCounts = counts(); await click("采用此方案"); expect(counts()).toEqual(readyCounts);
    expect(timeline()).not.toBe(before); expect(official().getByRole("heading", { name: "正式草案费用与预算" })).toBeTruthy(); expect(api.model).not.toHaveBeenCalled();
  });
});
