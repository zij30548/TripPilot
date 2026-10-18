import { act, fireEvent, render, renderHook, screen, within } from "@testing-library/react";
import { useCallback, useRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { executeReadOnlyTool, isQueryResponse, QUERY_VERSION, type QueryRequest, type QueryResponse } from "./itinerary-query";
import { captureFixture, adjustmentResponse } from "./schedule-adjustment.test-fixtures";
import { calculateScheduleCosts, emptyCostInputs } from "./schedule-costs";
import { candidateResponse, deferred, forecast, now, tripRequest } from "./weather-schedule-check.test-fixtures";
import { useSchedulePreview } from "./use-schedule-preview";
import { useItineraryQuery } from "./use-itinerary-query";
import PlanningWorkspace from "@/components/trip/planning-workspace";

const api = vi.hoisted(() => ({ query: vi.fn(), schedule: vi.fn(), weather: vi.fn(), candidates: vi.fn() }));
vi.mock("./itinerary-query", async original => ({ ...await original<typeof import("./itinerary-query")>(), queryItinerary: api.query }));
vi.mock("./schedule-api", async original => ({ ...await original<typeof import("./schedule-api")>(), querySchedulePreview: api.schedule }));
vi.mock("./weather-api", async original => ({ ...await original<typeof import("./weather-api")>(), queryWeatherForecast: api.weather }));
vi.mock("./candidates-api", async original => ({ ...await original<typeof import("./candidates-api")>(), queryCandidates: api.candidates }));
function request(): QueryRequest { const base = captureFixture(); return { contract_version: QUERY_VERSION, request_id: "controlled", draft_version: base.version, cost_version: 0, dates: base.original.days.map(d => d.date), question: "当前费用是多少" }; }
function response(req = request(), call: QueryResponse["tool_call"] = { name: "get_budget_summary", arguments: {} }): QueryResponse {
  const { question: _question, ...bound } = req; void _question;
  return { ...bound, model: "qwen3.7-flash-2026-07-15", tool_call_id: "call_test", tool_call: call, usage: { input_tokens: null, output_tokens: null } };
}
function useHarness() {
  const schedule = useSchedulePreview(tripRequest()), costs = useRef(emptyCostInputs());
  const getCosts = useCallback(() => costs.current, []);
  const assistant = useItineraryQuery(schedule, getCosts);
  return { schedule, assistant, costs };
}
async function ready() {
  const view = renderHook(useHarness);
  await act(async () => { await view.result.current.schedule.query(); });
  act(() => view.result.current.assistant.editQuestion("当前费用是多少")); return view;
}
beforeEach(() => { vi.resetAllMocks(); api.schedule.mockImplementation(async r => adjustmentResponse(r)); api.query.mockImplementation(async r => response(r)); api.weather.mockResolvedValue(forecast()); api.candidates.mockResolvedValue(candidateResponse()); });
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("local tool execution and strict binding", () => {
  it("uses actual day and same cost calculator, never model factual text or mutation", () => {
    const base = captureFixture(), costs = emptyCostInputs(), before = JSON.stringify(base);
    costs.other = "1.001"; costs.accommodation = "0";
    expect(executeReadOnlyTool(response(), request(), base, costs)).toEqual({ kind: "budget", costs: calculateScheduleCosts(base.original, base.request, costs) });
    const day = executeReadOnlyTool(response(request(), { name: "get_day_schedule", arguments: { date: request().dates[0] } }), request(), base, costs);
    expect(day.kind === "day" && day.day).toEqual(base.original.days[0]); expect(JSON.stringify(base)).toBe(before);
    expect(executeReadOnlyTool(response(request(), { name: "request_clarification", arguments: { reason: "unsupported" } }), request(), base, costs).kind).toBe("clarification");
  });
  it("rejects versions, extra facts, unknown tools, dates, parameters and usage", () => {
    const good = response();
    for (const change of [{ request_id: "old" }, { draft_version: 8 }, { cost_version: 1 }, { dates: ["2026-12-01"] }, { model: "other" }, { text: "free answer" },
      { tool_call: { name: "delete_trip", arguments: {} } }, { tool_call: { name: "get_budget_summary", arguments: { price: 0 } } },
      { tool_call: { name: "get_day_schedule", arguments: { date: "2026-12-01" } } }, { usage: { input_tokens: 1, output_tokens: 1025 } }]) expect(isQueryResponse({ ...good, ...change }, request())).toBe(false);
  });
});
describe("synchronous request isolation", () => {
  it("no valid draft means no call; no query on edit; one explicit request", async () => {
    const { result } = renderHook(useHarness);
    act(() => result.current.assistant.editQuestion("当前费用")); await act(async () => result.current.assistant.query()); expect(api.query).not.toHaveBeenCalled();
    await act(async () => result.current.schedule.query()); expect(api.query).not.toHaveBeenCalled();
    const wait = deferred<QueryResponse>(); api.query.mockReturnValueOnce(wait.promise);
    act(() => { void result.current.assistant.query(); void result.current.assistant.query(); }); expect(api.query).toHaveBeenCalledTimes(1);
    await act(async () => wait.resolve(response(api.query.mock.calls[0][0]))); expect(result.current.assistant.state.status).toBe("success");
  });
  it.each(["success", "error"])("cost ABA rejects old %s/finally and reads latest costs with old callback", async outcome => {
    const { result } = await ready(), oldQuery = result.current.assistant.query, stale = deferred<QueryResponse>(); api.query.mockReturnValueOnce(stale.promise);
    act(() => { void oldQuery(); });
    act(() => { result.current.assistant.costsChanged(); result.current.costs.current.other = "5"; result.current.assistant.costsChanged(); result.current.costs.current.other = ""; });
    expect(api.query.mock.calls[0][1].aborted).toBe(true);
    const latest = deferred<QueryResponse>(); api.query.mockReturnValueOnce(latest.promise); act(() => { void oldQuery(); });
    await act(async () => { if (outcome === "error") stale.reject(new Error("old")); else stale.resolve(response(api.query.mock.calls[0][0])); });
    expect(result.current.assistant.state.status).toBe("loading"); act(() => { void oldQuery(); }); expect(api.query).toHaveBeenCalledTimes(2);
    await act(async () => latest.resolve(response(api.query.mock.calls[1][0]))); expect(result.current.assistant.state.status).toBe("success");
  });
  it.each(["success", "error"])("mode ABA + regenerated draft isolates %s, capture uses current refs", async outcome => {
    const { result } = await ready(), oldQuery = result.current.assistant.query, stale = deferred<QueryResponse>(); api.query.mockReturnValueOnce(stale.promise);
    act(() => { void oldQuery(); result.current.schedule.setTransportMode("transit"); result.current.schedule.setTransportMode("walking"); });
    await act(async () => oldQuery()); expect(api.query).toHaveBeenCalledTimes(1);
    await act(async () => result.current.schedule.query()); await act(async () => oldQuery());
    const current = result.current.assistant.state;
    await act(async () => { if (outcome === "error") stale.reject(new Error("stale")); else stale.resolve(response(api.query.mock.calls[0][0])); });
    expect(result.current.assistant.state).toBe(current); expect(api.query.mock.calls[1][0].draft_version).toBe(result.current.schedule.readVersion());
  });
  it("old cancel cannot cancel newer query; unmount clears pending", async () => {
    const { result, unmount } = await ready(), first = deferred<QueryResponse>(), second = deferred<QueryResponse>();
    api.query.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    act(() => { void result.current.assistant.query(); }); const oldCancel = result.current.assistant.cancel;
    act(() => oldCancel()); act(() => { void result.current.assistant.query(); }); act(() => oldCancel());
    expect(api.query.mock.calls[1][1].aborted).toBe(false); unmount(); expect(api.query.mock.calls[1][1].aborted).toBe(true);
    await act(async () => { first.resolve(response(api.query.mock.calls[0][0])); second.reject(new Error("unmounted")); });
  });
  it("same event rule changes invalidate, stale query cannot bind old render to new version", async () => {
    const { result } = await ready(), oldQuery = result.current.assistant.query;
    act(() => { result.current.schedule.setLunchEnabled(false); void oldQuery(); }); expect(api.query).not.toHaveBeenCalled();
  });
});
describe("actual workspace composition", () => {
  it("weather and unadopted preview/cancel preserve query; adopting synchronously invalidates without model request", async () => {
    vi.useFakeTimers({ toFake: ["Date"] }); vi.setSystemTime(now);
    render(<PlanningWorkspace request={tripRequest()} onEdit={vi.fn()} />);
    const click = async (name: string) => { await act(async () => fireEvent.click(screen.getByRole("button", { name }))); };
    await click("获取候选地点"); await click("生成行程草案"); await click("查询天气"); await click("检查天气对行程的影响");
    const panel = within(screen.getByRole("region", { name: "只读行程查询" }));
    fireEvent.change(panel.getByRole("textbox"), { target: { value: "当前费用" } }); await click("查询当前行程");
    const text = panel.getByLabelText("只读查询结果").textContent;
    await click("预览排除该地点后的草案"); expect(panel.getByLabelText("只读查询结果").textContent).toBe(text);
    await click("取消预览"); await click("刷新天气"); expect(panel.getByLabelText("只读查询结果").textContent).toBe(text);
    await click("检查天气对行程的影响"); await click("预览排除该地点后的草案"); await click("采用此方案");
    expect(panel.queryByLabelText("只读查询结果")).toBeNull(); expect(api.query).toHaveBeenCalledTimes(1);
  });
  it("fixed result shows unknown/zero/invalid costs; edits clear query without touching draft or other controllers", async () => {
    render(<PlanningWorkspace request={tripRequest()} onEdit={vi.fn()} />);
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "生成行程草案" })));
    const panel = within(screen.getByRole("region", { name: "只读行程查询" }));
    fireEvent.change(panel.getByRole("textbox"), { target: { value: "当前费用是多少，还有哪些没填？" } });
    await act(async () => fireEvent.click(panel.getByRole("button", { name: "查询当前行程" })));
    expect(panel.getByText(/不是可自由支配余额/)).toBeTruthy(); expect(panel.getByText("尚未核算的费用")).toBeTruthy();
    const budget = within(screen.getByRole("region", { name: "正式草案费用与预算" }));
    fireEvent.change(budget.getByRole("textbox", { name: /全程其他费用/ }), { target: { value: "-1" } });
    expect(panel.queryByLabelText("只读查询结果")).toBeNull(); expect(api.query).toHaveBeenCalledTimes(1); expect(api.schedule).toHaveBeenCalledTimes(1);
    await act(async () => fireEvent.click(panel.getByRole("button", { name: "查询当前行程" })));
    expect(panel.getByText(/存在非法金额/)).toBeTruthy(); expect(panel.queryByText(/当前可计算小计/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "1 · 需求与地点准备" })); expect(panel.getByText(/存在非法金额/)).toBeTruthy(); expect(api.query).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("button", { name: "保存当前正式草案" }).hasAttribute("disabled")).toBe(false);
  });
});

describe("HTTP client boundary", () => {
  it("one fetch, strict echo refusal, safe server error and no retry", async () => {
    const { queryItinerary } = await vi.importActual<typeof import("./itinerary-query")>("./itinerary-query");
    const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(response()))).mockResolvedValueOnce(new Response(JSON.stringify(response() as object), { status: 200 })).mockResolvedValueOnce(new Response(JSON.stringify({ detail: { code: "structure", message: "UNSAFE RAW" } }), { status: 502 }));
    vi.stubGlobal("fetch", fetcher);
    expect(await queryItinerary(request(), new AbortController().signal)).toEqual(response());
    expect(fetcher.mock.calls[0][0]).toBe("/api/trips/itinerary-query");
    await expect(queryItinerary({ ...request(), request_id: "new" }, new AbortController().signal)).rejects.toThrow("不一致");
    await expect(queryItinerary(request(), new AbortController().signal)).rejects.toThrow("唯一有效");
    expect(fetcher).toHaveBeenCalledTimes(3);
    const controller = new AbortController(); controller.abort(); await expect(queryItinerary(request(), controller.signal)).rejects.toThrow(); expect(fetcher).toHaveBeenCalledTimes(3);
  });
});
