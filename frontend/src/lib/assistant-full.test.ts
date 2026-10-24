import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { isAssistantState, isAssistantStateV2, readVersionedAssistantState, sendFullDialogueTurn, sendToolDialogueTurn } from "./assistant";
import { fullAssistantStateFixture } from "./assistant-full.test-fixtures";
import { CHAT_ID, OP_ID, conversationFixture } from "./chat-test-fixtures";

const wire = (value: unknown) => new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } });
const context = () => ({ workspace_version: crypto.randomUUID(), display: "none" as const, items: [] });
let fetcher: ReturnType<typeof vi.fn>;
beforeEach(() => { fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher); });
afterEach(() => vi.useRealTimers());

describe("explicit full dialogue wire and exact execution aggregates", () => {
  it("accepts >8 exact aggregate reservations without widening v1 or inventing successful sends", () => {
    const value = fullAssistantStateFixture();
    expect(isAssistantStateV2(value)).toBe(true); expect(isAssistantState(value)).toBe(false);
    value.task!.execution_receipt!.dispatch = { status: "unknown", sent: null, received: null, failed: null, cancelled: null, uncertain: null };
    expect(isAssistantStateV2(value)).toBe(true);
    value.task!.execution_receipt = null; expect(isAssistantStateV2(value)).toBe(true);
  });
  it.each<((v: ReturnType<typeof fullAssistantStateFixture>) => void)>([
    (v: ReturnType<typeof fullAssistantStateFixture>) => { v.task!.model_calls = 9; },
    v => { v.task!.execution_receipt!.reservations.output_tokens = 24577; },
    v => { v.task!.execution_receipt!.budget.model_limit = 99; },
    v => { v.task!.execution_receipt!.budget.real_call_authorization = true as false; },
    v => { v.task!.execution_receipt!.dispatch.received = 9; },
    v => { v.task!.execution_receipt!.dispatch.status = "unknown"; },
    v => { v.task!.execution_receipt!.reservations.models = 17; v.task!.model_calls = 17; },
    v => { Object.assign(v.task!.execution_receipt!, { input: "private" }); },
    v => { v.task!.steps = Array(101).fill(v.task!.steps[0]); },
    v => { Object.assign(v.task!.steps[0], { thinking: "private" }); },
  ])("rejects inconsistent/unknown/unbounded receipt data (%#)", mutate => {
    const value = fullAssistantStateFixture(); mutate(value); expect(isAssistantStateV2(value)).toBe(false);
  });
  it("uses v3 for full submit/read and keeps v2 callable unchanged", async () => {
    fetcher.mockImplementation(async () => wire(conversationFixture()));
    const body = { operation_id: OP_ID, expected_revision: 0, text: "核对安排与费用", dialogue_context: context() };
    await sendFullDialogueTurn(CHAT_ID, body, new AbortController().signal);
    expect(new Headers(fetcher.mock.calls[0][1].headers).get("X-TripPilot-Surface")).toBe("dialogue-v3");
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual(body);
    await sendToolDialogueTurn(CHAT_ID, body, new AbortController().signal);
    expect(new Headers(fetcher.mock.calls[1][1].headers).get("X-TripPilot-Surface")).toBe("dialogue-v2");
    fetcher.mockResolvedValue(wire(fullAssistantStateFixture()));
    const value = await readVersionedAssistantState(CHAT_ID, 2, new AbortController().signal);
    expect(value.task!.model_calls).toBe(10);
    expect(new Headers(fetcher.mock.calls[2][1].headers).get("X-TripPilot-Surface")).toBe("dialogue-v3");
  });
  it("rejects missing binding before sending, and does not guess a legacy fallback", async () => {
    await expect(sendFullDialogueTurn(CHAT_ID, { operation_id: OP_ID, expected_revision: 0, text: "查看安排" }, new AbortController().signal)).rejects.toThrow("没有发送");
    expect(fetcher).not.toHaveBeenCalled();
  });
  it("resource preflight failure is explicit and does not expose private paths or invoke another engine", async () => {
    fetcher.mockResolvedValue(new Response(JSON.stringify({ detail: { code: "assistant_full_runtime_not_ready", message: "/private/secret" } }), { status: 503 }));
    await expect(sendFullDialogueTurn(CHAT_ID, { operation_id: OP_ID, expected_revision: 0, text: "看看行程", dialogue_context: context() }, new AbortController().signal)).rejects.toThrow("没有登记任务或发送模型");
    expect(fetcher).toHaveBeenCalledOnce();
  });
  it("waits beyond the old 65s only for v3, and stops once at 105s without retry", async () => {
    vi.useFakeTimers();
    fetcher.mockImplementation((_url: string, init: RequestInit) => new Promise((_resolve, reject) => {
      init.signal!.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true });
    }));
    const pending = sendFullDialogueTurn(CHAT_ID, { operation_id: OP_ID, expected_revision: 0, text: "核对安排和费用", dialogue_context: context() }, new AbortController().signal);
    const rejected = expect(pending).rejects.toThrow("任务可能已经开始");
    await vi.advanceTimersByTimeAsync(65_000); expect(fetcher.mock.calls[0][1].signal.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(40_000); await rejected; expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
