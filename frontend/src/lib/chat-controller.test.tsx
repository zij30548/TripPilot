import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { canConfirmRequirements, missingRequirements, useChatWorkspace } from "./chat-controller";
import { CHAT_ID, OP_ID, OTHER_CHAT_ID, conversationFixture, deferred, emptyConversation } from "./chat-test-fixtures";
import type { ChatConversation } from "./chat";

const api = vi.hoisted(() => ({ list: vi.fn(), create: vi.fn(), read: vi.fn(), send: vi.fn(), cancel: vi.fn(), confirm: vi.fn(), remove: vi.fn(), epoch: 0 }));
vi.mock("./chat", async original => ({ ...await original<typeof import("./chat")>(), listChatConversations: api.list, createChatConversation: api.create, readChatConversation: api.read, sendChatTurn: api.send, cancelChatTurn: api.cancel, confirmChatRequirements: api.confirm, deleteChatConversation: api.remove }));
vi.mock("./auth-session", () => ({ authSnapshot: () => ({ epoch: api.epoch }) }));
beforeEach(() => {
  vi.resetAllMocks(); api.epoch = 0;
  api.list.mockResolvedValue({ schema_version: "chat-list-v1", items: [], next_offset: null });
  api.create.mockResolvedValue(emptyConversation());
  api.read.mockImplementation(async id => conversationFixture({ id }));
  api.send.mockImplementation(async (_id, request) => conversationFixture({ last_turn: { operation_id: request.operation_id, state: "succeeded", error_code: null } }));
  api.cancel.mockImplementation(async (id, operationId) => conversationFixture({ id, revision: 3, last_turn: { operation_id: operationId, state: "cancelled", error_code: null } }));
  api.confirm.mockResolvedValue(conversationFixture({ revision: 3, confirmed_revision: 3 }));
  api.remove.mockResolvedValue(undefined);
});
async function loaded() {
  const view = renderHook(useChatWorkspace);
  await waitFor(() => expect(view.result.current.state.listing).toBe(false));
  return view;
}
async function selected() {
  const view = await loaded();
  await act(async () => { await view.result.current.selectConversation(CHAT_ID); });
  return view;
}

describe("chat UI and persisted conversation versions", () => {
  it("mount, typing, and local new-chat do not send a model, route, search or create request", async () => {
    const view = await loaded();
    act(() => view.result.current.editDraft("去杭州"));
    act(() => view.result.current.newConversation());
    expect(api.list).toHaveBeenCalledTimes(1);
    for (const endpoint of [api.create, api.read, api.send, api.confirm, api.cancel]) expect(endpoint).not.toHaveBeenCalled();
    expect(view.result.current.state.draft).toBe("");
  });
  it("first explicit send creates once, freezes revision/op, blocks double click; later turn uses current revision", async () => {
    const view = await loaded(), first = deferred<ChatConversation>(); api.send.mockReturnValueOnce(first.promise);
    act(() => view.result.current.editDraft("想去杭州"));
    act(() => { void view.result.current.send(); void view.result.current.send(); });
    await waitFor(() => expect(api.send).toHaveBeenCalledTimes(1));
    expect(api.create).toHaveBeenCalledTimes(1);
    const [, request] = api.send.mock.calls[0];
    expect(request).toMatchObject({ expected_revision: 0, text: "想去杭州" });
    expect(request.operation_id).toMatch(/^[a-f0-9-]{36}$/);
    await act(async () => first.resolve(conversationFixture({ last_turn: { operation_id: request.operation_id, state: "succeeded", error_code: null } })));
    act(() => view.result.current.editDraft("改成北京"));
    await act(async () => { await view.result.current.send(); });
    expect(api.create).toHaveBeenCalledTimes(1);
    expect(api.send.mock.calls[1][1]).toMatchObject({ expected_revision: 2, text: "改成北京" });
    expect(api.send.mock.calls[1][1].operation_id).not.toBe(request.operation_id);
  });
  it("an explicit send in the same input event batch captures the latest draft once", async () => {
    const view = await loaded();
    const send = view.result.current.send;
    await act(async () => { view.result.current.editDraft("同一批输入的杭州需求"); await send(); });
    expect(api.send).toHaveBeenCalledTimes(1);
    expect(api.send.mock.calls[0][1].text).toBe("同一批输入的杭州需求");
  });
  it("reading the latest draft never lets an old send target A→B→A, a reset blank chat or a changed identity", async () => {
    const view = await selected(), oldA = view.result.current.send;
    await act(async () => { await view.result.current.selectConversation(OTHER_CHAT_ID); });
    await act(async () => { await view.result.current.selectConversation(CHAT_ID); });
    act(() => view.result.current.editDraft("最新A输入"));
    await act(async () => oldA()); expect(api.send).not.toHaveBeenCalled();
    act(() => view.result.current.newConversation());
    const oldBlank = view.result.current.send;
    act(() => view.result.current.newConversation());
    act(() => view.result.current.editDraft("新空白会话输入"));
    await act(async () => oldBlank()); expect(api.send).not.toHaveBeenCalled();
    const anotherAccount = view.result.current.send;
    api.epoch += 1;
    await act(async () => anotherAccount()); expect(api.send).not.toHaveBeenCalled();
  });
  it("uncertain create can be reconciled only with the same frozen operation and without sending a turn", async () => {
    const view = await loaded(); api.create.mockRejectedValueOnce(new Error("ACK lost"));
    act(() => view.result.current.editDraft("想去杭州"));
    await act(async () => { await view.result.current.send(); });
    const operationId = api.create.mock.calls[0][0];
    expect(view.result.current.state.needsRefresh).toBe(true);
    await act(async () => { await view.result.current.send(); });
    expect(api.create).toHaveBeenCalledTimes(1); expect(api.send).not.toHaveBeenCalled();
    await act(async () => { await view.result.current.recoverCreation(); });
    expect(api.create.mock.calls[1][0]).toBe(operationId);
    expect(api.send).not.toHaveBeenCalled();
    expect(view.result.current.state.draft).toBe("想去杭州");
    expect(view.result.current.state.needsRefresh).toBe(false);
  });
  it.each(["success", "error"])("cancel isolates late %s and finally; a stale cancel cannot stop the newer turn", async outcome => {
    const view = await selected(), first = deferred<ChatConversation>(), second = deferred<ChatConversation>();
    api.send.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    act(() => view.result.current.editDraft("改为两个人"));
    act(() => { void view.result.current.send(); });
    const oldCancel = view.result.current.cancel;
    await act(async () => { await oldCancel(); });
    expect(api.send.mock.calls[0][2].aborted).toBe(true);
    expect(api.cancel.mock.calls[0][0]).toBe(CHAT_ID);
    expect(api.cancel.mock.calls[0][1]).toBe(api.send.mock.calls[0][1].operation_id);
    act(() => view.result.current.editDraft("预算4000"));
    act(() => { void view.result.current.send(); });
    await act(async () => { if (outcome === "success") first.resolve(conversationFixture()); else first.reject(new Error("old error")); });
    await act(async () => { await oldCancel(); });
    expect(view.result.current.state.pending).toBe("sending");
    expect(api.send.mock.calls[1][2].aborted).toBe(false);
    expect(api.cancel).toHaveBeenCalledTimes(1);
    await act(async () => second.resolve(conversationFixture({ revision: 5 })));
    expect(view.result.current.state.conversation?.revision).toBe(5);
    expect(view.result.current.state.error).toBeNull();
  });
  it.each(["success", "error"])("A→B→A read generation rejects the first A %s", async outcome => {
    const view = await loaded(), oldA = deferred<ChatConversation>(); api.read.mockReturnValueOnce(oldA.promise);
    act(() => { void view.result.current.selectConversation(CHAT_ID); });
    await act(async () => { await view.result.current.selectConversation(OTHER_CHAT_ID); });
    await act(async () => { await view.result.current.selectConversation(CHAT_ID); });
    const latest = view.result.current.state.conversation;
    await act(async () => { if (outcome === "success") oldA.resolve(conversationFixture({ title: "old A" })); else oldA.reject(new Error("old A error")); });
    expect(view.result.current.state.conversation).toBe(latest);
    expect(view.result.current.state.error).toBeNull();
    expect(api.read.mock.calls[0][1].aborted).toBe(true);
  });
  it("switching cancels the immutable old op, does not apply its cancel response, and unmount aborts new read", async () => {
    const view = await selected(), turn = deferred<ChatConversation>(), cancelled = deferred<ChatConversation>();
    api.send.mockReturnValueOnce(turn.promise); api.cancel.mockReturnValueOnce(cancelled.promise);
    act(() => view.result.current.editDraft("两个人")); act(() => { void view.result.current.send(); });
    await act(async () => { await view.result.current.selectConversation(OTHER_CHAT_ID); });
    expect(api.cancel).toHaveBeenCalledTimes(1);
    await act(async () => { cancelled.resolve(conversationFixture({ title: "old cancellation" })); turn.resolve(conversationFixture({ title: "old turn" })); });
    expect(view.result.current.state.conversation?.id).toBe(OTHER_CHAT_ID);
    const late = deferred<ChatConversation>(); api.read.mockReturnValueOnce(late.promise);
    act(() => { void view.result.current.selectConversation(CHAT_ID); });
    view.unmount(); expect(api.read.mock.calls.at(-1)![1].aborted).toBe(true);
    await act(async () => late.reject(new Error("unmounted")));
  });
  it("uncertain send blocks repeated sends until explicit read and does not conceal old confirmed card", async () => {
    const view = await selected(); api.send.mockRejectedValueOnce(new Error("timeout"));
    act(() => view.result.current.editDraft("预算改300"));
    await act(async () => { await view.result.current.send(); });
    act(() => view.result.current.editDraft("预算改400"));
    await act(async () => { await view.result.current.send(); });
    expect(api.send).toHaveBeenCalledTimes(1);
    expect(view.result.current.state.needsRefresh).toBe(true);
    await act(async () => { await view.result.current.refreshConversation(); });
    expect(api.read).toHaveBeenCalledTimes(2);
    expect(view.result.current.state.needsRefresh).toBe(false);
    expect(api.send).toHaveBeenCalledTimes(1);
  });
  it("explicit confirmation binds id/revision, stale confirmation cannot target new conversation", async () => {
    const view = await selected();
    const oldConfirm = view.result.current.confirm;
    await act(async () => { await view.result.current.selectConversation(OTHER_CHAT_ID); });
    await act(async () => { await oldConfirm(); });
    expect(api.confirm).not.toHaveBeenCalled();
    await act(async () => { await view.result.current.confirm(); });
    expect(api.confirm.mock.calls[0].slice(0, 2)).toEqual([OTHER_CHAT_ID, 2]);
    expect(view.result.current.state.conversation?.confirmed_revision).toBe(3);
    await act(async () => { await view.result.current.confirm(); });
    expect(api.confirm).toHaveBeenCalledTimes(1);
  });
  it("account epoch change rejects response even when underlying transport ignores abort", async () => {
    const view = await selected(), late = deferred<ChatConversation>(); api.send.mockReturnValueOnce(late.promise);
    act(() => view.result.current.editDraft("预算5000")); act(() => { void view.result.current.send(); });
    api.epoch++;
    await act(async () => late.resolve(conversationFixture({ title: "wrong account" })));
    expect(view.result.current.state.conversation?.title).not.toBe("wrong account");
    view.unmount(); expect(api.cancel).not.toHaveBeenCalled();
  });
  it("read-only delete is explicit and doesn't call other tools", async () => {
    const view = await selected();
    expect(api.remove).not.toHaveBeenCalled();
    await act(async () => { await view.result.current.remove(); });
    expect(api.remove.mock.calls[0].slice(0, 2)).toEqual([CHAT_ID, 2]);
    expect(view.result.current.state.conversation).toBeNull();
    expect(api.send).not.toHaveBeenCalled();
  });
  it("late pre-create empty list cannot remove a newly created conversation", async () => {
    const oldList = deferred<{ schema_version: string; items: []; next_offset: null }>();
    api.list.mockReturnValueOnce(oldList.promise);
    const view = renderHook(useChatWorkspace);
    act(() => view.result.current.editDraft("想去杭州"));
    await act(async () => { await view.result.current.send(); });
    expect(view.result.current.state.items.map(item => item.id)).toEqual([CHAT_ID]);
    expect(api.list.mock.calls[0][1].aborted).toBe(true);
    await act(async () => oldList.resolve({ schema_version: "chat-list-v1", items: [], next_offset: null }));
    expect(view.result.current.state.items.map(item => item.id)).toEqual([CHAT_ID]);
    expect(view.result.current.state.listing).toBe(false);
  });
  it.each(["success", "error"])("late list %s cannot resurrect a deleted summary or publish a stale error", async outcome => {
    const view = await selected();
    const full = conversationFixture();
    const oldList = deferred<{ schema_version: string; items: { id: string; title: string; revision: number; updated_at: string }[]; next_offset: null }>();
    api.list.mockReturnValueOnce(oldList.promise);
    act(() => { void view.result.current.reloadList(); });
    await act(async () => { await view.result.current.remove(); });
    expect(api.list.mock.calls[1][1].aborted).toBe(true);
    await act(async () => {
      if (outcome === "error") oldList.reject(new Error("stale list error"));
      else oldList.resolve({ schema_version: "chat-list-v1", items: [{ id: full.id, title: full.title, revision: full.revision, updated_at: full.updated_at }], next_offset: null });
    });
    expect(view.result.current.state.items).toEqual([]);
    expect(view.result.current.state.error).toBeNull();
    expect(view.result.current.state.listing).toBe(false);
  });
});

describe("intent confirmation is not execution permission", () => {
  it("requires essential intent fields, successful turn and no blocking issue", () => {
    const good = conversationFixture();
    expect(missingRequirements(good)).toEqual([]); expect(canConfirmRequirements(good)).toBe(true);
    for (const issue of ["ambiguous", "unsupported_scope", "unsupported_action"] as const) expect(canConfirmRequirements({ ...good, issues: [issue] })).toBe(false);
    for (const status of ["running", "failed", "uncertain", "cancelled"] as const) expect(canConfirmRequirements({ ...good, last_turn: { operation_id: OP_ID, state: status, error_code: null } })).toBe(false);
    const incomplete = { ...good, requirements: { ...good.requirements, accommodation_hint: null, budget_cny: null } };
    expect(missingRequirements(incomplete)).toEqual(["全程总预算", "住宿参考名称"]);
    expect(canConfirmRequirements(incomplete)).toBe(false);
    expect(canConfirmRequirements({ ...good, confirmed_revision: good.revision })).toBe(false);
  });
});
