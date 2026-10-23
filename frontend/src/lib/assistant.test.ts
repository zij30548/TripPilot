import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { isAssistantState, readAssistantState, sendAssistantTurn, sendDialogueTurn, type AssistantState } from "./assistant";
import { CHAT_ID, OP_ID, OTHER_CHAT_ID, conversationFixture } from "./chat-test-fixtures";

export const assistantStateFixture = (overrides: Partial<AssistantState> = {}): AssistantState => ({
  schema_version: "assistant-state-v1", conversation_id: CHAT_ID, revision: 2, migration_ready: true, preferences: [],
  skills: [{ id: "travel_requirements", name: "旅行需求整理", version: "1.0.0" }],
  task: { id: OP_ID, state: "awaiting_confirmation", model_calls: 1, tool_calls: 1, stop_reason: "confirmation_required", steps: [{ name: "整理旅行需求", state: "succeeded", elapsed_ms: 150 }] },
  ...overrides,
});
const reply = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
let fetcher: ReturnType<typeof vi.fn>;
beforeEach(() => { fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher); });
afterEach(() => { vi.useRealTimers(); });

describe("EchoMind travel assistant strict transport", () => {
  it("binds the minimal dialogue surface while preserving strict operation validation and legacy defaults", async () => {
    fetcher.mockImplementation(async () => reply(conversationFixture()));
    const body = { operation_id: OP_ID, expected_revision: 0, text: "想去杭州" };
    await sendDialogueTurn(CHAT_ID, body, new AbortController().signal);
    expect(new Headers(fetcher.mock.calls[0][1].headers).get("X-TripPilot-Surface")).toBe("dialogue-v1");
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual(body);
    await sendAssistantTurn(CHAT_ID, body, new AbortController().signal);
    expect(new Headers(fetcher.mock.calls[1][1].headers).has("X-TripPilot-Surface")).toBe(false);
    fetcher.mockResolvedValue(reply(conversationFixture({ id: OTHER_CHAT_ID })));
    await expect(sendDialogueTurn(CHAT_ID, body, new AbortController().signal)).rejects.toThrow("不属于本次");
  });
  it("uses the separate assistant endpoint once with unchanged revision/operation; never falls back to legacy intent", async () => {
    fetcher.mockResolvedValue(reply(conversationFixture()));
    const body = { operation_id: OP_ID, expected_revision: 0, text: "想去杭州" };
    expect((await sendAssistantTurn(CHAT_ID, body, new AbortController().signal)).revision).toBe(2);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toBe(`/api/chat/conversations/${CHAT_ID}/assistant-turns`);
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual(body);
  });
  it.each([
    conversationFixture({ id: OTHER_CHAT_ID }), conversationFixture({ revision: 0 }),
    conversationFixture({ last_turn: { operation_id: OTHER_CHAT_ID, state: "succeeded", error_code: null } }),
    { ...conversationFixture(), thinking: "not public" },
  ])("rejects conversation, operation, revision or unexpected payload mismatch", async result => {
    fetcher.mockResolvedValue(reply(result));
    await expect(sendAssistantTurn(CHAT_ID, { operation_id: OP_ID, expected_revision: 0, text: "杭州" }, new AbortController().signal)).rejects.toThrow("不属于本次");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it("reads only a matching assistant receipt without any model request", async () => {
    fetcher.mockResolvedValue(reply(assistantStateFixture()));
    await expect(readAssistantState(CHAT_ID, 2, new AbortController().signal)).resolves.toMatchObject({ task: { model_calls: 1 } });
    expect(fetcher.mock.calls[0][0]).toBe(`/api/chat/conversations/${CHAT_ID}/assistant-state`);
    expect(fetcher.mock.calls[0][1].method).toBe("GET");
    fetcher.mockResolvedValue(reply(assistantStateFixture({ revision: 3 })));
    await expect(readAssistantState(CHAT_ID, 2, new AbortController().signal)).rejects.toThrow("版本不一致");
  });
  it("strictly validates finite steps/counts, identities, no private trace, and known preferences representation", () => {
    expect(isAssistantState(assistantStateFixture())).toBe(true);
    expect(isAssistantState(assistantStateFixture({ migration_ready: false, task: null }))).toBe(true);
    const invalid = [
      { ...assistantStateFixture(), private_trace: "secret" },
      { ...assistantStateFixture(), preferences: [{ text: "unverified" }] },
      { ...assistantStateFixture(), task: { ...assistantStateFixture().task, model_calls: 9 } },
      { ...assistantStateFixture(), task: { ...assistantStateFixture().task, steps: Array(9).fill({ name: "step", state: "running", elapsed_ms: 1 }) } },
      { ...assistantStateFixture(), task: { ...assistantStateFixture().task, steps: [{ name: "step", state: "running", elapsed_ms: Infinity }] } },
      { ...assistantStateFixture(), skills: [...assistantStateFixture().skills, ...assistantStateFixture().skills] },
    ];
    for (const result of invalid) expect(isAssistantState(result)).toBe(false);
  });
  it("fails explicitly when migration is unavailable and never surfaces arbitrary backend messages", async () => {
    fetcher.mockResolvedValue(reply({ detail: { code: "assistant_migration_required", message: "private SQL" } }, 503));
    await expect(sendAssistantTurn(CHAT_ID, { operation_id: OP_ID, expected_revision: 0, text: "杭州" }, new AbortController().signal)).rejects.toThrow("完成独立迁移");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it("a missing new endpoint is explicit and cannot fall back to the old model route", async () => {
    fetcher.mockResolvedValue(reply({ detail: "Not Found" }, 404));
    await expect(sendAssistantTurn(CHAT_ID, { operation_id: OP_ID, expected_revision: 0, text: "杭州" }, new AbortController().signal)).rejects.toThrow("接口尚未就绪");
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toContain("/assistant-turns");
  });
  it.each([
    ["assistant_runtime_dependencies_unavailable", 503, "运行依赖尚未准备好"],
    ["assistant_runtime_surface_required", 422, "仅支持正式对话入口"],
  ])("%s fails clearly without exposing private detail or trying another runtime", async (code, status, expected) => {
    fetcher.mockResolvedValue(reply({ detail: { code, message: "private runtime import path" } }, Number(status)));
    await expect(sendAssistantTurn(CHAT_ID, { operation_id: OP_ID, expected_revision: 0, text: "杭州" }, new AbortController().signal)).rejects.toThrow(String(expected));
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toContain("/assistant-turns");
  });
  it("bounds responses before parsing, including streaming payloads", async () => {
    fetcher.mockResolvedValue(new Response("x", { headers: { "Content-Length": "65537" } }));
    await expect(readAssistantState(CHAT_ID, 2, new AbortController().signal)).rejects.toThrow("大小无效");
    fetcher.mockResolvedValue(new Response(new Uint8Array(65537)));
    await expect(readAssistantState(CHAT_ID, 2, new AbortController().signal)).rejects.toThrow("响应过大");
  });
  it("does not send a cancelled request and rejects late success from a transport ignoring AbortSignal", async () => {
    const cancelled = new AbortController(); cancelled.abort();
    await expect(readAssistantState(CHAT_ID, 2, cancelled.signal)).rejects.toMatchObject({ name: "AbortError" });
    expect(fetcher).not.toHaveBeenCalled();
    const controller = new AbortController();
    fetcher.mockImplementation(async () => { controller.abort(); return reply(assistantStateFixture()); });
    await expect(readAssistantState(CHAT_ID, 2, controller.signal)).rejects.toMatchObject({ name: "AbortError" });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it("has a bounded wait and leaves a sent timeout uncertain instead of retrying", async () => {
    vi.useFakeTimers();
    fetcher.mockImplementation((_url: string, init: RequestInit) => new Promise((_resolve, reject) => {
      init.signal!.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true });
    }));
    const pending = sendAssistantTurn(CHAT_ID, { operation_id: OP_ID, expected_revision: 0, text: "杭州" }, new AbortController().signal);
    const rejected = expect(pending).rejects.toThrow("任务可能已经开始");
    await vi.advanceTimersByTimeAsync(65_000);
    await rejected;
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
