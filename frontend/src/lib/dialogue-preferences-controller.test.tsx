import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useDialoguePreferences } from "./dialogue-preferences-controller";
import { CHAT_ID, OTHER_CHAT_ID, deferred } from "./chat-test-fixtures";
const reply = (items: unknown[] = [], versions = {}) => new Response(JSON.stringify({ schema_version: "assistant-preferences-v1", items, versions }), { headers: { "Content-Type": "application/json" } });
const save = { name: "save_preference", arguments: { key: "travel_preferences", value: "希望少步行" } };
describe("dialogue preference consent and CAS", () => {
  it("does not read or save on mount, requires explicit consent and sends server version", async () => {
    const fetcher = vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") return reply([{ key: "travel_preferences", value: "希望少步行", version: 3, source: "user", consent: true, created_at: "2026-10-22T00:00:00Z", updated_at: "2026-10-22T00:00:00Z" }], { travel_preferences: 3 });
      return reply([], { travel_preferences: 2 });
    }); vi.stubGlobal("fetch", fetcher);
    const hook = renderHook(() => useDialoguePreferences(CHAT_ID)); expect(fetcher).not.toHaveBeenCalled();
    await act(async () => { expect((await hook.result.current.execute(save)).text).toContain("明确同意"); }); expect(fetcher).not.toHaveBeenCalled();
    await act(async () => { expect((await hook.result.current.execute(save, true)).text).toContain("希望少步行"); });
    expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toEqual({ action: "set", key: "travel_preferences", value: "希望少步行", consent: true, expected_version: 2 });
  });
  it("cancelled write is not resent and requires readback before any new write", async () => {
    const late = deferred<Response>();
    const fetcher = vi.fn(async (_url: string, init?: RequestInit) => init?.method === "POST" ? late.promise : reply());
    vi.stubGlobal("fetch", fetcher); const hook = renderHook(() => useDialoguePreferences(CHAT_ID));
    await act(async () => { await hook.result.current.execute({ name: "read_preferences", arguments: {} }); });
    let operation!: ReturnType<typeof hook.result.current.execute>; act(() => { operation = hook.result.current.execute(save, true); });
    act(() => { hook.result.current.cancel(); });
    await act(async () => { late.resolve(reply()); await operation; expect((await hook.result.current.execute(save, true)).text).toContain("先读取"); });
    expect(fetcher.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
  });
  it("an old read cannot populate preferences after session A→B→A", async () => {
    const late = deferred<Response>(); vi.stubGlobal("fetch", vi.fn(async () => late.promise));
    const hook = renderHook(({ id }) => useDialoguePreferences(id), { initialProps: { id: CHAT_ID } });
    let pending!: ReturnType<typeof hook.result.current.execute>; act(() => { pending = hook.result.current.execute({ name: "read_preferences", arguments: {} }); });
    const old = hook.result.current.execute; hook.rerender({ id: OTHER_CHAT_ID }); hook.rerender({ id: CHAT_ID });
    await act(async () => { late.resolve(reply()); expect((await pending).text).toContain("旧偏好"); expect((await old(save, true)).text).toContain("会话"); });
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
