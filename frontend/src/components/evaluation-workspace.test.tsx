import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import EvaluationWorkspace, { technicalReceiptChecks } from "./evaluation-workspace";
import type { AssistantState } from "@/lib/assistant";
import { CHAT_ID, OTHER_CHAT_ID, conversationFixture, deferred } from "@/lib/chat-test-fixtures";

const api = vi.hoisted(() => ({
  list: vi.fn(), conversation: vi.fn(), state: vi.fn(), listeners: new Set<() => void>(),
  auth: { phase: "authenticated", epoch: 1, session: { session_id: "test-session-1" } },
}));
vi.mock("@/lib/auth-session", () => ({
  authSnapshot: () => api.auth, authServerSnapshot: () => api.auth,
  subscribeAuth: (listener: () => void) => { api.listeners.add(listener); return () => api.listeners.delete(listener); },
}));
vi.mock("@/lib/chat", () => ({ CHAT_VERSION: "chat-session-v1", listChatConversations: api.list, readChatConversation: api.conversation }));
vi.mock("@/lib/assistant", () => ({ readAssistantState: api.state }));
const summary = (id = CHAT_ID, title = "杭州旅行", revision = 1) => ({ id, title, revision, updated_at: "2026-10-22T01:00:00Z" });
function receipt(overrides: Partial<AssistantState> = {}): AssistantState {
  return { schema_version: "assistant-state-v1", conversation_id: CHAT_ID, revision: 2, migration_ready: true, preferences: [], skills: [],
    task: { id: "10000000-0000-4000-8000-000000000009", state: "waiting", model_calls: 2, tool_calls: 1, stop_reason: "awaiting_user",
      steps: [{ name: "search_places", state: "succeeded", elapsed_ms: 1450 }, { name: "coordinator", state: "waiting", elapsed_ms: 280 }] }, ...overrides };
}
const click = async (name: string | RegExp) => act(async () => { fireEvent.click(screen.getByRole("button", { name })); });
async function readList() { await click("读取会话"); }
beforeEach(() => {
  vi.resetAllMocks(); api.listeners.clear(); api.auth = { phase: "authenticated", epoch: 1, session: { session_id: "test-session-1" } };
  api.list.mockResolvedValue({ schema_version: "chat-list-v1", items: [summary(), summary(OTHER_CHAT_ID, "北京旅行")], next_offset: null });
  api.conversation.mockImplementation(async (id: string) => conversationFixture({ id, title: id === CHAT_ID ? "杭州旅行" : "北京旅行", revision: 2 }));
  api.state.mockImplementation(async (id: string) => receipt({ conversation_id: id }));
});

describe("deterministic technical receipt checks, not semantic scoring", () => {
  it("accepts the exact execution limits and flags either exceeded limit independently", () => {
    const atLimit = receipt({ task: { ...receipt().task!, state: "succeeded", model_calls: 3, tool_calls: 4 } });
    expect(technicalReceiptChecks(atLimit).map(check => check.state)).toEqual(["passed", "passed", "passed"]);
    const exceeded = receipt({ task: { ...atLimit.task!, model_calls: 4, tool_calls: 5 } });
    expect(technicalReceiptChecks(exceeded).map(check => check.state)).toEqual(["failed", "failed", "passed"]);
    expect(technicalReceiptChecks(exceeded)[2].detail).toContain("非答案质量判定");
  });
  it.each(["failed", "cancelled", "uncertain", "running", "waiting", "paused", "awaiting_confirmation", "queued"] as const)("%s is never reported as a successful completion", state => {
    const checks = technicalReceiptChecks(receipt({ task: { ...receipt().task!, state } }));
    expect(checks[2].state).toBe(["failed", "cancelled"].includes(state) ? "failed" : "pending");
    expect(checks[2].detail).toContain("不记为成功");
  });
  it("missing or unready receipts leave every check pending instead of manufacturing zero-call passes", () => {
    expect(technicalReceiptChecks(receipt({ task: null })).map(check => check.state)).toEqual(["pending", "pending", "pending"]);
    expect(technicalReceiptChecks(receipt({ migration_ready: false })).map(check => check.state)).toEqual(["pending", "pending", "pending"]);
  });
  it("renders bounded checks separately from the explicit unassessed semantic dimensions", async () => {
    api.state.mockResolvedValueOnce(receipt({ task: { ...receipt().task!, state: "uncertain", model_calls: 4 } }));
    render(<EvaluationWorkspace />); await readList(); await click(/杭州旅行/);
    const checks = within(screen.getByRole("region", { name: "本轮技术核查" }));
    expect(checks.getByText("未通过")).toBeTruthy(); expect(checks.getByText("待核")).toBeTruthy(); expect(checks.getByText("通过")).toBeTruthy();
    expect(checks.getByText(/语义正确性、事实支持和需求覆盖未评/)).toBeTruthy();
  });
});

describe("minimal read-only Agent evaluation workspace (controlled receipts)", () => {
  it("mounting, navigation and expanding explanation never start a task or fetch a list automatically", async () => {
    const page = render(<EvaluationWorkspace active={false} />);
    expect(api.list).not.toHaveBeenCalled(); expect(api.state).not.toHaveBeenCalled();
    page.rerender(<EvaluationWorkspace active />);
    expect(api.list).not.toHaveBeenCalled();
    expect(screen.getByText(/执行成功不等于语义评测通过/)).toBeTruthy();
    expect(screen.queryByText(/通过率|准确率|评分\d/)).toBeNull();
    await readList(); await click(/杭州旅行/);
    expect(api.list).toHaveBeenCalledTimes(1); expect(api.conversation).toHaveBeenCalledTimes(1); expect(api.state).toHaveBeenCalledTimes(1);
    expect(api.state.mock.calls[0][1]).toBe(2); // Fresh conversation, not list revision 1.
    expect(screen.getByText("2", { selector: "dd" })).toBeTruthy();
    expect(screen.getByText("search_places")).toBeTruthy(); expect(screen.getByText(/1.45 秒/)).toBeTruthy();
    expect(screen.getByText(/等待用户补充或确认/)).toBeTruthy();
    page.rerender(<EvaluationWorkspace active={false} />); page.rerender(<EvaluationWorkspace active />);
    expect(screen.getByText("search_places")).toBeTruthy(); expect(api.state).toHaveBeenCalledTimes(1);
    await click("重新读取"); expect(api.state).toHaveBeenCalledTimes(2);
  });

  it.each(["success", "error"])("A→B→A rejects old %s and old finally cannot release a newer read", async outcome => {
    const old = deferred<AssistantState>(), fresh = deferred<AssistantState>();
    api.state.mockReturnValueOnce(old.promise).mockResolvedValueOnce(receipt({ conversation_id: OTHER_CHAT_ID })).mockReturnValueOnce(fresh.promise);
    render(<EvaluationWorkspace />); await readList(); await click(/杭州旅行/);
    const oldSignal = api.state.mock.calls[0][2] as AbortSignal;
    await click(/北京旅行/); await click(/杭州旅行/);
    expect(oldSignal.aborted).toBe(true);
    await act(async () => { if (outcome === "success") old.resolve(receipt({ task: { ...receipt().task!, stop_reason: "old_task" } })); else old.reject(new Error("old failure")); });
    expect(screen.queryByText(/old_task|old failure/)).toBeNull();
    expect(screen.getByText("正在读取最新执行记录…")).toBeTruthy();
    await act(async () => fresh.resolve(receipt({ task: { ...receipt().task!, stop_reason: "current_task" } })));
    expect(screen.getByText(/current_task/)).toBeTruthy(); expect(api.state).toHaveBeenCalledTimes(3);
  });

  it.each(["success", "error"])("cancelled reads ignore late %s and require an explicit new read", async outcome => {
    const pending = deferred<AssistantState>(); api.state.mockReturnValueOnce(pending.promise);
    render(<EvaluationWorkspace />); await readList(); await click(/杭州旅行/); await click("取消读取");
    expect(api.state.mock.calls[0][2].aborted).toBe(true);
    await act(async () => { if (outcome === "success") pending.resolve(receipt()); else pending.reject(new Error("cancelled error")); });
    expect(screen.queryByText("search_places")).toBeNull(); expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText("已停止读取。没有重新执行任务。")).toBeTruthy(); expect(api.state).toHaveBeenCalledTimes(1);
    await click(/杭州旅行/); expect(screen.getByText("search_places")).toBeTruthy();
  });

  it("account changes synchronously hide old receipts, abort pending reads and never automatically reread", async () => {
    const pending = deferred<AssistantState>(); api.state.mockReturnValueOnce(pending.promise);
    render(<EvaluationWorkspace />); await readList(); await click(/杭州旅行/);
    act(() => { api.auth = { phase: "authenticated", epoch: 2, session: { session_id: "test-session-2" } }; api.listeners.forEach(listener => listener()); });
    expect(api.state.mock.calls[0][2].aborted).toBe(true); expect(screen.queryByRole("button", { name: /杭州旅行/ })).toBeNull();
    await act(async () => pending.resolve(receipt())); expect(screen.queryByText("search_places")).toBeNull();
    expect(api.list).toHaveBeenCalledTimes(1); expect(api.state).toHaveBeenCalledTimes(1);
    await readList(); expect(api.list).toHaveBeenCalledTimes(2);
  });

  it("unmount aborts before a late conversation read could issue a second receipt request", async () => {
    const pending = deferred<ReturnType<typeof conversationFixture>>(); api.conversation.mockReturnValueOnce(pending.promise);
    const page = render(<EvaluationWorkspace />); await readList(); await click(/杭州旅行/); page.unmount();
    expect(api.conversation.mock.calls[0][1].aborted).toBe(true);
    await act(async () => pending.resolve(conversationFixture())); expect(api.state).not.toHaveBeenCalled();
  });

  it("a hidden page retains an in-flight read without issuing another request when shown again", async () => {
    const pending = deferred<AssistantState>(); api.state.mockReturnValueOnce(pending.promise);
    const page = render(<EvaluationWorkspace />); await readList(); await click(/杭州旅行/);
    page.rerender(<EvaluationWorkspace active={false} />);
    await act(async () => pending.resolve(receipt()));
    page.rerender(<EvaluationWorkspace active />);
    expect(screen.getByText("search_places")).toBeTruthy(); expect(api.state).toHaveBeenCalledTimes(1);
  });

  it("list failures and version conflicts are explicit, without falling back to a fabricated task", async () => {
    api.list.mockRejectedValueOnce(new Error("会话读取失败"));
    render(<EvaluationWorkspace />); await readList(); expect(screen.getByRole("alert").textContent).toBe("会话读取失败");
    await readList(); api.state.mockRejectedValueOnce(new Error("执行记录与当前会话版本不一致")); await click(/杭州旅行/);
    expect(screen.getByRole("alert").textContent).toBe("执行记录与当前会话版本不一致");
    expect(screen.queryByText("search_places")).toBeNull(); expect(api.state).toHaveBeenCalledTimes(1);
  });

  it.each([true, false])("missing task is an honest empty state; migration readiness %s never becomes a passed score", async ready => {
    api.state.mockResolvedValueOnce(receipt({ migration_ready: ready, task: null }));
    render(<EvaluationWorkspace />); await readList(); await click(/杭州旅行/);
    expect(screen.getByText(ready ? /还没有任务回执/ : /任务存储尚未准备好/)).toBeTruthy();
    expect(screen.queryByText("执行完成")).toBeNull(); expect(screen.queryByText(/100%/)).toBeNull();
  });

  it("explicit paging uses its validated offset; duplicate records are not merged silently", async () => {
    api.list.mockResolvedValueOnce({ schema_version: "chat-list-v1", items: [summary()], next_offset: 20 })
      .mockResolvedValueOnce({ schema_version: "chat-list-v1", items: [summary()], next_offset: null });
    render(<EvaluationWorkspace />); await readList(); await click("加载更多");
    expect(api.list.mock.calls[1][0]).toBe(20); expect(screen.getByRole("alert").textContent).toMatch(/没有合并重复记录/);
    expect(within(screen.getByRole("complementary", { name: "评测会话" })).getAllByRole("button", { name: /杭州旅行/ })).toHaveLength(1);
  });

  it("double-clicking read cannot create concurrent requests", async () => {
    const pending = deferred<{ schema_version: string; items: ReturnType<typeof summary>[]; next_offset: null }>(); api.list.mockReturnValueOnce(pending.promise);
    render(<EvaluationWorkspace />);
    act(() => { const read = screen.getByRole("button", { name: "读取会话" }); fireEvent.click(read); fireEvent.click(read); });
    expect(api.list).toHaveBeenCalledTimes(1);
    await act(async () => pending.resolve({ schema_version: "chat-list-v1", items: [], next_offset: null }));
    await waitFor(() => expect(screen.getByText(/还没有会话/)).toBeTruthy());
  });
});
