import { createRef } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import DialogueTools, { type DialogueProposal, type DialogueToolsHandle } from "./dialogue-tools";
import { assistantPlanningFixture } from "@/lib/assistant-planning.test-fixtures";
import { adjustmentResponse } from "@/lib/schedule-adjustment.test-fixtures";
import { savedCityFixture } from "@/lib/restore-trip.test-fixtures";
import { deferred } from "@/lib/chat-test-fixtures";
import type { TripRequest } from "@/types/trip";
import type { ScheduleRequest } from "@/types/schedule";
import type { SaveOperation, SavedDetail } from "@/lib/saved-trips";

const api = vi.hoisted(() => ({ schedule: vi.fn(), save: vi.fn(), list: vi.fn(), read: vi.fn(), epoch: 0 }));
vi.mock("@/lib/schedule-api", async original => ({ ...await original<typeof import("@/lib/schedule-api")>(), querySchedulePreview: api.schedule }));
vi.mock("@/lib/saved-trips", async original => ({ ...await original<typeof import("@/lib/saved-trips")>(), saveTrip: api.save, listTrips: api.list, readTrip: api.read }));
vi.mock("@/lib/auth-session", async original => ({ ...await original<typeof import("@/lib/auth-session")>(), authSnapshot: () => ({ epoch: api.epoch }) }));
vi.mock("@/components/trip-request-form", () => ({ default: ({ initialRequest, onConfirm }: { initialRequest: TripRequest; onConfirm: (request: TripRequest) => void }) =>
  <button type="button" onClick={() => onConfirm({ ...initialRequest, budget: initialRequest.budget + 1 })}>确认受控需求编辑</button> }));

beforeEach(() => {
  vi.resetAllMocks(); api.epoch = 0;
  api.schedule.mockImplementation(async (request: ScheduleRequest) => adjustmentResponse(request));
  api.save.mockRejectedValue(new Error("保存响应丢失，提交状态未知"));
  const saved = savedCityFixture(); api.list.mockResolvedValue({ items: [saved], offset: 0, limit: 10, has_more: false }); api.read.mockResolvedValue(saved);
});
function setup() {
  const fixture = assistantPlanningFixture(), handle = createRef<DialogueToolsHandle>(), message = vi.fn(), busy = vi.fn();
  const fetcher = vi.fn(async () => new Response(JSON.stringify(fixture.places), { headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetcher);
  const props = { conversation: fixture.conversation, blocked: false, proposal: null as DialogueProposal | null,
    handle, onMessage: message, onBusy: busy, onConfirmRequirements: vi.fn(async () => undefined) };
  return { ...render(<DialogueTools {...props} />), props, handle, message, busy, fetcher };
}
async function submit(v: ReturnType<typeof setup>, text: string) {
  await act(async () => { expect(v.handle.current?.submit(text)).toBe(true); });
}
async function prepareUncertain(v: ReturnType<typeof setup>) {
  await submit(v, "准备规划"); await submit(v, "确认");
  await submit(v, "生成行程"); await submit(v, "确认");
  await submit(v, "保存行程 原快照"); await submit(v, "确认");
  expect(api.save).toHaveBeenCalledOnce();
  expect(v.message.mock.calls.at(-1)?.[1]).toContain("提交状态未知");
}
function offer(v: ReturnType<typeof setup>, action: DialogueProposal["action"]) {
  v.rerender(<DialogueTools {...v.props} proposal={{ conversation: v.props.conversation.id, revision: v.props.conversation.revision,
    operation: v.props.conversation.last_turn!.operation_id, action }} />);
}

describe("dialogue composition retains uncertain save authority across input replacement", () => {
  it("v3 local read pair publishes two actual results once and cannot replay the same response", async () => {
    const v = setup(); await submit(v, "准备规划"); await submit(v, "确认"); await submit(v, "生成行程"); await submit(v, "确认");
    act(() => v.handle.current!.beforeModel());
    const context = v.handle.current!.selectionContext();
    const conversation = { ...v.props.conversation, revision: v.props.conversation.revision + 2,
      last_turn: { operation_id: crypto.randomUUID(), state: "succeeded" as const, error_code: null } };
    const proposal: DialogueProposal = { conversation: conversation.id, revision: conversation.revision, operation: conversation.last_turn.operation_id,
      action: null, actions: [{ name: "read_schedule", arguments: { date: null, day: 1 } }, { name: "read_budget", arguments: {} }],
      context, workspaceVersion: context.workspace_version, programMessages: [] };
    v.rerender(<DialogueTools {...v.props} conversation={conversation} proposal={proposal} />);
    await waitFor(() => expect(v.message.mock.calls.at(-1)?.[1]).toContain("本地只读结果"));
    const answer = v.message.mock.calls.at(-1)?.[1]; expect(answer).toContain("必去已安排"); expect(answer).toContain("尚未核算");
    expect(api.schedule).toHaveBeenCalledOnce(); expect(api.save).not.toHaveBeenCalled();
    const count = v.message.mock.calls.length;
    v.rerender(<DialogueTools {...v.props} conversation={conversation} proposal={{ ...proposal }} />);
    expect(v.message).toHaveBeenCalledTimes(count);
  });

  it("fee ABA and cancel make old v3 read proposals inert even when requirements are unchanged", async () => {
    const v = setup(); await submit(v, "准备规划"); await submit(v, "确认"); await submit(v, "生成行程"); await submit(v, "确认");
    act(() => v.handle.current!.beforeModel()); const context = v.handle.current!.selectionContext();
    for (const value of ["5", ""]) {
      v.props.conversation = { ...v.props.conversation, revision: v.props.conversation.revision + 2,
        last_turn: { operation_id: crypto.randomUUID(), state: "succeeded", error_code: null } };
      offer(v, { name: "set_cost", arguments: { kind: "other", selector: null, value } });
      await waitFor(() => expect(v.message.mock.calls.at(-1)?.[1]).toContain("未确认前不执行")); await submit(v, "确认");
    }
    const conversation = { ...v.props.conversation, revision: v.props.conversation.revision + 2,
      last_turn: { operation_id: crypto.randomUUID(), state: "succeeded" as const, error_code: null } };
    const late: DialogueProposal = { conversation: conversation.id, revision: conversation.revision, operation: conversation.last_turn.operation_id,
      action: null, actions: [{ name: "read_schedule", arguments: { date: null, day: 1 } }, { name: "read_budget", arguments: {} }],
      context, workspaceVersion: context.workspace_version, programMessages: [] };
    v.rerender(<DialogueTools {...v.props} conversation={conversation} proposal={late} />);
    await waitFor(() => expect(v.message.mock.calls.at(-1)?.[1]).toContain("未执行旧提议"));
    expect(v.message.mock.calls.some(([, text]) => text.includes("本地只读结果"))).toBe(false);
    expect(api.schedule).toHaveBeenCalledOnce();
    act(() => v.handle.current!.beforeModel()); const next = v.handle.current!.selectionContext(); act(() => v.handle.current!.cancel());
    const cancelled = { ...conversation, revision: conversation.revision + 2, last_turn: { ...conversation.last_turn, operation_id: crypto.randomUUID() } };
    v.rerender(<DialogueTools {...v.props} conversation={cancelled} proposal={{ ...late, conversation: cancelled.id, revision: cancelled.revision,
      operation: cancelled.last_turn.operation_id, context: next, workspaceVersion: next.workspace_version }} />);
    await waitFor(() => expect(v.message.mock.calls.at(-1)?.[1]).toContain("未执行旧提议"));
    expect(api.save).not.toHaveBeenCalled();
  });

  it("prepare cannot discard a frozen operation; explicit retry resolves it and only then permits replacement", async () => {
    const v = setup(); await prepareUncertain(v); const original = api.save.mock.calls[0][0] as SaveOperation;
    const calls = v.fetcher.mock.calls.length;
    await submit(v, "准备规划"); await submit(v, "确认");
    expect(v.message.mock.calls.at(-1)?.[1]).toContain("保存结果尚未确认"); expect(v.fetcher).toHaveBeenCalledTimes(calls);
    api.save.mockResolvedValueOnce({ name: original.name } as SavedDetail);
    await submit(v, "保存行程 新名字不能替换旧操作"); await submit(v, "确认");
    expect(api.save.mock.calls[1][0]).toBe(original);
    expect(v.message.mock.calls.at(-1)?.[1]).toContain("已保存“原快照”");
    await submit(v, "准备规划"); await submit(v, "确认");
    expect(v.message.mock.calls.at(-1)?.[1]).toContain("已准备规划输入");
    expect(api.schedule).toHaveBeenCalledOnce(); expect(api.save).toHaveBeenCalledTimes(2);
  });
  it.each(["set_daily_window", "restore_saved"])("%s cannot replace an uncertain save or announce success", async name => {
    const v = setup(); await prepareUncertain(v);
    if (name === "restore_saved") await submit(v, "查看历史");
    offer(v, name === "set_daily_window" ? { name, arguments: { start: "10:00", end: "17:00" } } : { name, arguments: { selector: savedCityFixture().id } });
    await waitFor(() => expect(v.message.mock.calls.at(-1)?.[1]).toContain("请回复“确认”"));
    await submit(v, "确认");
    expect(v.message.mock.calls.at(-1)?.[1]).toContain("保存结果尚未确认");
    expect(api.read).not.toHaveBeenCalled(); expect(api.schedule).toHaveBeenCalledOnce(); expect(api.save).toHaveBeenCalledOnce();
    await submit(v, "查看行程"); expect(v.message.mock.calls.at(-1)?.[1]).toContain("必去已安排");
  });
  it("editor confirmation keeps its inputs and dialog when save is uncertain; cancel leaves the formal draft", async () => {
    const v = setup(); await prepareUncertain(v); await submit(v, "编辑需求");
    fireEvent.click(screen.getByRole("button", { name: "确认受控需求编辑" }));
    expect(v.message.mock.calls.at(-1)?.[1]).toContain("保存结果尚未确认");
    expect(screen.getByRole("dialog", { name: "编辑规划需求" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "取消整次编辑" }));
    await submit(v, "查看行程"); expect(v.message.mock.calls.at(-1)?.[1]).toContain("必去已安排"); expect(api.save).toHaveBeenCalledOnce();
  });
  it("changed requirements reject normal planning but can resolve exactly the old save without rebuilding it", async () => {
    const v = setup(); await prepareUncertain(v); const original = api.save.mock.calls[0][0] as SaveOperation;
    const next = { ...v.props.conversation, revision: v.props.conversation.revision + 1,
      requirements: { ...v.props.conversation.requirements, budget_cny: String(Number(v.props.conversation.requirements.budget_cny) + 100) } };
    v.rerender(<DialogueTools {...v.props} conversation={next} />);
    await submit(v, "查看行程"); expect(v.message.mock.calls.at(-1)?.[1]).toContain("旧正式草案已经失效");
    api.save.mockResolvedValueOnce({ name: original.name } as SavedDetail);
    await submit(v, "保存行程 不得生成新快照"); await submit(v, "确认");
    expect(api.save.mock.calls[1][0]).toBe(original);
    expect(v.message.mock.calls.at(-1)?.[1]).toContain("已保存“原快照”"); expect(api.schedule).toHaveBeenCalledOnce();
  });
  it("a changed account cannot use the frozen retry exception", async () => {
    const v = setup(); await prepareUncertain(v); api.epoch++;
    await submit(v, "保存行程 不允许跨账号"); await submit(v, "确认"); expect(api.save).toHaveBeenCalledOnce();
  });
  it("a sent cancelled save stays uncertain; late completion cannot discard the frozen retry", async () => {
    const v = setup(); await submit(v, "准备规划"); await submit(v, "确认"); await submit(v, "生成行程"); await submit(v, "确认");
    const late = deferred<SavedDetail>(); api.save.mockReturnValueOnce(late.promise);
    await submit(v, "保存行程 原快照"); await submit(v, "确认"); const original = api.save.mock.calls[0][0];
    act(() => v.handle.current?.cancel()); await submit(v, "准备规划"); await submit(v, "确认");
    expect(v.message.mock.calls.at(-1)?.[1]).toContain("保存结果尚未确认");
    await act(async () => { late.resolve({ name: "不得覆盖" } as SavedDetail); });
    api.save.mockResolvedValueOnce({ name: "原快照" } as SavedDetail);
    await submit(v, "保存行程 仍为同一次"); await submit(v, "确认"); expect(api.save.mock.calls[1][0]).toBe(original);
  });
});
