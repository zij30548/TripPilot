import { createRef } from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import DialogueTools, { type DialogueToolsHandle, type DialogueProposal } from "./dialogue-tools";
import { assistantPlanningFixture } from "@/lib/assistant-planning.test-fixtures";
import { batchFixture, succeeded } from "@/lib/chat-places-test-fixtures";
import { deferred } from "@/lib/chat-test-fixtures";
import type { WorkspaceInputs } from "@/lib/restore-trip";
import type { PlacesRequest } from "@/lib/chat-places";
import type { ChatConversation } from "@/lib/chat";
import { savedCityFixture } from "@/lib/restore-trip.test-fixtures";

const mocks = vi.hoisted(() => ({ planning: vi.fn(), execute: vi.fn(), invalidate: vi.fn(), cancel: vi.fn(), photo: vi.fn(), video: vi.fn() }));
vi.mock("@/lib/dialogue-planning-controller", async original => ({ ...await original<typeof import("@/lib/dialogue-planning-controller")>(), useDialoguePlanning: mocks.planning }));
vi.mock("@/lib/photo-api", async original => ({ ...await original<typeof import("@/lib/photo-api")>(), recognizePhoto: mocks.photo }));
vi.mock("@/lib/video-api", async original => ({ ...await original<typeof import("@/lib/video-api")>(), extractVideo: mocks.video }));
const wire = (value: unknown) => new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } });

function setup({ conversation: requested, proposal = null }: { conversation?: ChatConversation; proposal?: DialogueProposal | null } = {}) {
  const fixture = assistantPlanningFixture();
  const conversation = requested ?? fixture.conversation;
  let server = structuredClone(fixture.places);
  const candidate = { ...server.required_places[0], id: "new-control-place", name: "受控新点" };
  const fetcher = vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      const command = JSON.parse(String(init.body)) as PlacesRequest;
      server = { ...server, revision: server.revision + 1, intent_revision: command.expected_intent_revision, stale: false,
        last_operation: succeeded(command.operation_id),
        ...(command.action === "search" ? { batch: { ...batchFixture([candidate]), city_id: server.city_id!, role: command.role } } : {}),
        ...(command.action === "confirm" ? { required_places: [...server.required_places, candidate] } : {}) };
    }
    return wire(server);
  }); vi.stubGlobal("fetch", fetcher);
  const handle = createRef<DialogueToolsHandle>(), message = vi.fn(), busy = vi.fn(), confirm = vi.fn(async () => undefined);
  const props = { conversation, blocked: false, proposal, handle, onMessage: message, onBusy: busy, onConfirmRequirements: confirm };
  return { ...render(<DialogueTools {...props} />), props, handle, message, busy, fetcher, candidate, initial: fixture };
}
async function submit(v: ReturnType<typeof setup>, text: string) {
  await act(async () => { expect(v.handle.current?.submit(text)).toBe(true); });
}
beforeEach(() => {
  vi.clearAllMocks(); mocks.execute.mockResolvedValue({ text: "受控规划结果" });
  mocks.planning.mockImplementation((inputs: WorkspaceInputs) => ({ readInputs: () => structuredClone(inputs), readVersion: () => 0,
    execute: mocks.execute, invalidate: mocks.invalidate, cancelOperation: mocks.cancel, schedule: { capture: () => null } }));
  URL.createObjectURL = vi.fn(() => "blob:controlled"); URL.revokeObjectURL = vi.fn();
});

describe("dialogue tools composed with real place authority and pure planning preparation", () => {
  it("a local read batch may bind the model response revision but cannot publish into a later identical-input revision", async () => {
    const late = deferred<{ text: string }>(), readBatch = vi.fn(() => late.promise);
    mocks.planning.mockImplementation((inputs: WorkspaceInputs) => ({ readInputs: () => structuredClone(inputs), readVersion: () => 0,
      captureReadSnapshot: () => Object.freeze({ version: 0 }), executeReadBatch: readBatch,
      execute: mocks.execute, invalidate: mocks.invalidate, cancelOperation: mocks.cancel, schedule: { capture: () => null } }));
    const v = setup(); await submit(v, "准备规划"); await submit(v, "确认");
    act(() => v.handle.current!.beforeModel()); const context = v.handle.current!.selectionContext();
    const responseConversation = { ...v.props.conversation, revision: v.props.conversation.revision + 2,
      last_turn: { operation_id: crypto.randomUUID(), state: "succeeded" as const, error_code: null } };
    const proposal: DialogueProposal = { conversation: responseConversation.id, revision: responseConversation.revision,
      operation: responseConversation.last_turn.operation_id, action: null, context, workspaceVersion: context.workspace_version,
      actions: [{ name: "read_schedule", arguments: { date: null, day: 1 } }, { name: "read_budget", arguments: {} }], programMessages: [] };
    v.rerender(<DialogueTools {...v.props} conversation={responseConversation} proposal={proposal} />);
    await waitFor(() => expect(readBatch).toHaveBeenCalledOnce());
    // Request→response advanced legitimately. Only the later local publication
    // crosses another revision, with the same requirements, work copy and fees.
    v.rerender(<DialogueTools {...v.props} conversation={{ ...responseConversation, revision: responseConversation.revision + 1 }} proposal={null} />);
    await act(async () => late.resolve({ text: "obsolete-local-read" }));
    expect(v.message.mock.calls.some(([, text]) => text.includes("obsolete-local-read"))).toBe(false);
    expect(readBatch).toHaveBeenCalledOnce(); expect(mocks.execute).not.toHaveBeenCalled();
    expect(v.busy.mock.calls.at(-1)?.[0]).toBe(false);
  });

  it("mount has no requests; search asks explicit consent; local preview and confirmation keep separate authority", async () => {
    const v = setup(); expect(v.fetcher).not.toHaveBeenCalled(); expect(mocks.planning).not.toHaveBeenCalled();
    await submit(v, "搜索必去 受控新点"); expect(v.fetcher).not.toHaveBeenCalled();
    expect(v.message.mock.calls.at(-1)?.[1]).toContain("未确认前不执行");
    await submit(v, "确认"); await waitFor(() => expect(v.fetcher).toHaveBeenCalledTimes(2));
    expect(v.message.mock.calls.at(-1)?.[1]).toContain("1. 受控新点");
    await submit(v, "确认地点 1"); expect(v.fetcher).toHaveBeenCalledTimes(2); expect(v.message.mock.calls.at(-1)?.[1]).toContain("请先预览");
    await submit(v, "预览地点 1"); expect(v.fetcher).toHaveBeenCalledTimes(2);
    await act(async () => { v.handle.current?.submit("确认地点 1"); v.handle.current?.submit("确认地点 1"); });
    await waitFor(() => expect(v.fetcher).toHaveBeenCalledTimes(3));
    const commands = v.fetcher.mock.calls.flatMap(([, init]) => init?.body ? [JSON.parse(String(init.body))] : []);
    expect(commands.filter(c => c.action === "confirm")).toHaveLength(1);
    expect(commands[1]).toMatchObject({ place_id: v.candidate.id, batch_id: batchFixture().id }); expect(commands[1]).not.toHaveProperty("place");
    expect(mocks.planning).not.toHaveBeenCalled();
  });
  it("preparing uses validated confirmed POIs; cancelling full requirement editor preserves the exact prepared inputs", async () => {
    const v = setup(); await submit(v, "准备规划"); expect(v.fetcher).not.toHaveBeenCalled(); await submit(v, "确认");
    await waitFor(() => expect(mocks.planning).toHaveBeenCalled());
    const original = structuredClone(mocks.planning.mock.calls[0][0]) as WorkspaceInputs;
    expect(original.request.accommodation_place?.id).toBe(v.initial.places.accommodation?.id);
    expect(original.request.must_visit_places?.map(p => p.id)).toEqual(v.initial.places.required_places.map(p => p.id));
    expect(mocks.execute).not.toHaveBeenCalled();
    await submit(v, "编辑需求"); expect(screen.getByRole("dialog", { name: "编辑规划需求" })).toBeTruthy();
    const travelers = screen.queryByLabelText(/旅行人数|出行人数/); if (travelers) fireEvent.change(travelers, { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "取消整次编辑" }));
    expect(screen.queryByRole("dialog", { name: "编辑规划需求" })).toBeNull();
    expect(mocks.invalidate).not.toHaveBeenCalled();
    expect(mocks.planning.mock.calls.every(([inputs]) => JSON.stringify(inputs) === JSON.stringify(original))).toBe(true);
    expect(v.fetcher).toHaveBeenCalledTimes(1);
  });
  it("media opening may read current local place state but never calls model, POI, extraction or planner", async () => {
    const v = setup(); await submit(v, "添加照片"); expect(v.fetcher).not.toHaveBeenCalled(); await submit(v, "确认");
    await screen.findByRole("dialog", { name: "照片地点线索" });
    expect(v.fetcher).toHaveBeenCalledTimes(1); expect(v.fetcher.mock.calls[0][1]?.method).toBe("GET");
    expect(mocks.photo).not.toHaveBeenCalled(); expect(mocks.video).not.toHaveBeenCalled(); expect(mocks.execute).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "关闭照片入口" }));
    await submit(v, "添加视频"); await screen.findByRole("dialog", { name: "视频画面地点线索" });
    expect(v.fetcher).toHaveBeenCalledTimes(1); expect(mocks.photo).not.toHaveBeenCalled(); expect(mocks.video).not.toHaveBeenCalled();
  });
  it("explicit history pagination reads page two, binds its local selection and does not generate or call models", async () => {
    const v = setup();
    const first = { ...savedCityFixture(), id: "a".repeat(32), name: "第一页快照" };
    const second = { ...savedCityFixture(), id: "b".repeat(32), name: "第二页快照" };
    const summary = (record: typeof first) => ({ id: record.id, name: record.name, saved_at: record.saved_at,
      schema_version: record.schema_version, start_date: record.start_date, end_date: record.end_date,
      transport_mode: record.transport_mode, status: record.status });
    v.fetcher.mockImplementation(async (url: string) => {
      if (url === "/api/saved-trips?offset=0&limit=10") return wire({ items: [summary(first)], offset: 0, limit: 10, has_more: true });
      if (url === "/api/saved-trips?offset=10&limit=10") return wire({ items: [summary(second)], offset: 10, limit: 10, has_more: false });
      if (url === `/api/saved-trips/${second.id}`) return wire(second);
      throw new Error(`Unexpected controlled request: ${url}`);
    });
    expect(v.fetcher).not.toHaveBeenCalled();
    await submit(v, "查看历史"); expect(v.message.mock.calls.at(-1)?.[1]).toContain("查看第2页历史");
    await submit(v, "查看第2页历史");
    expect(v.message.mock.calls.at(-1)?.[1]).toContain("1. 第二页快照");
    expect(v.message.mock.calls.at(-1)?.[1]).not.toContain("第一页快照");
    const conversation = v.props.conversation;
    const context = v.handle.current!.selectionContext();
    expect(context.items).toEqual([{ ordinal: 1, id: second.id, name: second.name, type: "saved" }]);
    v.rerender(<DialogueTools {...v.props} proposal={{ conversation: conversation.id, revision: conversation.revision,
      operation: conversation.last_turn!.operation_id, context, action: { name: "read_saved", arguments: { selector: "1" } } }} />);
    await waitFor(() => expect(v.message.mock.calls.at(-1)?.[1]).toContain("历史快照“第二页快照”"));
    expect(v.fetcher.mock.calls.map(([url]) => url)).toEqual([
      "/api/saved-trips?offset=0&limit=10", "/api/saved-trips?offset=10&limit=10", `/api/saved-trips/${second.id}`,
    ]);
    expect(mocks.planning).not.toHaveBeenCalled(); expect(mocks.execute).not.toHaveBeenCalled();
    expect(mocks.photo).not.toHaveBeenCalled(); expect(mocks.video).not.toHaveBeenCalled();
  });
  it("model proposal identity mismatch is ignored, valid proposal still requires user confirmation", async () => {
    const { conversation } = assistantPlanningFixture();
    const proposal: DialogueProposal = { conversation: conversation.id, revision: conversation.revision, operation: "wrong-operation", action: { name: "search_places", arguments: { keyword: "受控", role: "required" } } };
    const v = setup({ conversation, proposal }); expect(v.message).not.toHaveBeenCalled(); expect(v.fetcher).not.toHaveBeenCalled();
    const valid = { ...proposal, operation: conversation.last_turn!.operation_id };
    v.rerender(<DialogueTools {...v.props} proposal={valid} />);
    await waitFor(() => expect(v.message).toHaveBeenCalledOnce()); expect(v.fetcher).not.toHaveBeenCalled();
    v.rerender(<DialogueTools {...v.props} proposal={{ ...valid }} />); expect(v.message).toHaveBeenCalledOnce();
    await submit(v, "取消"); await submit(v, "确认"); expect(v.fetcher).not.toHaveBeenCalled();
  });
  it("natural-language confirm after a read-only turn reloads current batch and asks a fresh explicit confirmation", async () => {
    const v = setup(); await submit(v, "搜索必去 受控新点"); await submit(v, "确认");
    const next = { ...v.props.conversation, revision: v.props.conversation.revision + 1,
      last_turn: { ...v.props.conversation.last_turn!, operation_id: "10000000-0000-4000-8000-000000000020" } };
    let server = { ...v.initial.places, intent_revision: next.revision, revision: 3,
      batch: { ...batchFixture([v.candidate]), city_id: v.initial.places.city_id! } };
    v.fetcher.mockImplementation(async (_url: string, init?: RequestInit) => {
      if (init?.body) { const command = JSON.parse(String(init.body)); server = { ...server, revision: server.revision + 1,
        required_places: [...v.initial.places.required_places, v.candidate], last_operation: succeeded(command.operation_id) }; }
      return wire(server);
    });
    const proposal = { conversation: next.id, revision: next.revision, operation: next.last_turn.operation_id,
      action: { name: "confirm_place", arguments: { number: 1 } } };
    v.rerender(<DialogueTools {...v.props} conversation={next} proposal={proposal} />);
    await waitFor(() => expect(v.message.mock.calls.at(-1)?.[1]).toContain("请回复“确认”或“取消”本次操作"));
    const before = v.fetcher.mock.calls.filter(([, init]) => init?.body && JSON.parse(String(init.body)).action === "confirm").length;
    expect(before).toBe(0);
    await submit(v, "确认");
    await waitFor(() => expect(v.fetcher.mock.calls.filter(([, init]) => init?.body && JSON.parse(String(init.body)).action === "confirm")).toHaveLength(1));
  });
  it("changing requirement revision and back invalidates a pending confirmation", async () => {
    const v = setup(); await submit(v, "搜索必去 受控");
    v.rerender(<DialogueTools {...v.props} conversation={{ ...v.props.conversation, revision: v.props.conversation.revision + 1 }} />);
    v.rerender(<DialogueTools {...v.props} conversation={{ ...v.props.conversation }} />);
    await submit(v, "确认"); expect(v.fetcher).not.toHaveBeenCalled(); expect(v.message.mock.calls.at(-1)?.[1]).toContain("没有与当前上下文匹配");
  });
  it("changed city cannot read or regenerate a previously prepared city's formal draft", async () => {
    const v = setup(); await submit(v, "准备规划"); await submit(v, "确认"); await waitFor(() => expect(mocks.planning).toHaveBeenCalled());
    const changed = { ...v.props.conversation, revision: v.props.conversation.revision + 1,
      requirements: { ...v.props.conversation.requirements, destination_id: "110000" } };
    v.rerender(<DialogueTools {...v.props} conversation={changed} />);
    await submit(v, "查看行程"); await submit(v, "生成行程"); await submit(v, "确认");
    expect(mocks.execute).not.toHaveBeenCalled(); expect(mocks.invalidate).toHaveBeenCalled();
  });
  it("an ordinary chat turn with identical requirements does not discard prepared planning inputs", async () => {
    const v = setup(); await submit(v, "准备规划"); await submit(v, "确认"); await waitFor(() => expect(mocks.planning).toHaveBeenCalled());
    const next = { ...v.props.conversation, revision: v.props.conversation.revision + 1,
      requirements: structuredClone(v.props.conversation.requirements) };
    v.rerender(<DialogueTools {...v.props} conversation={next} />);
    await submit(v, "查看行程"); expect(mocks.execute).toHaveBeenCalledWith({ name: "read_schedule", arguments: { date: null, day: null } }, expect.any(AbortSignal));
    expect(mocks.invalidate).not.toHaveBeenCalled();
  });
  it("search and preview preserve prepared work; confirming a new real POI invalidates it", async () => {
    const v = setup(); await submit(v, "准备规划"); await submit(v, "确认"); await waitFor(() => expect(mocks.planning).toHaveBeenCalled());
    await submit(v, "搜索必去 受控新点"); await submit(v, "确认"); await submit(v, "预览地点 1");
    expect(mocks.invalidate).not.toHaveBeenCalled();
    await submit(v, "确认地点 1");
    await waitFor(() => expect(mocks.invalidate).toHaveBeenCalled());
    await submit(v, "查看行程"); expect(mocks.execute).not.toHaveBeenCalled();
  });
  it("stopping a sent search rejects its late completion and does not auto-resend", async () => {
    const v = setup(), late = deferred<Response>(); let captured: PlacesRequest | null = null;
    v.fetcher.mockImplementation(async (_url: string, init?: RequestInit) => {
      const command = init?.body ? JSON.parse(String(init.body)) as PlacesRequest : null;
      if (command?.action === "search") { captured = command; return late.promise; }
      return wire(v.initial.places);
    });
    await submit(v, "搜索必去 受控"); await submit(v, "确认");
    await waitFor(() => expect(captured).not.toBeNull()); act(() => v.handle.current?.cancel());
    const count = v.message.mock.calls.length;
    await act(async () => { late.resolve(wire({ ...v.initial.places, revision: 1, batch: { ...batchFixture([v.candidate]), city_id: v.initial.places.city_id }, last_operation: succeeded(captured!.operation_id) })); });
    expect(v.message.mock.calls.length).toBe(count);
    expect(v.fetcher.mock.calls.filter(([, init]) => init?.body && JSON.parse(String(init.body)).action === "search")).toHaveLength(1);
  });
});
