import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useChatPlaces } from "./chat-places-controller";
import { CHAT_ID } from "./chat-test-fixtures";
import { batchFixture, placeFixture, placesConversation, placesFixture, succeeded } from "./chat-places-test-fixtures";
const reply = (value: unknown) => new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } });
function pending<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(r => { resolve = r; }); return { promise, resolve }; }

describe("version-bound place preparation", () => {
  it("does not query on mount/collapse; preview is local and only explicit confirmation writes IDs", async () => {
    let server = placesFixture({ revision: 2, batch: batchFixture() });
    const fetcher = vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        const command = JSON.parse(init.body as string);
        server = placesFixture({ revision: 3, required_places: [placeFixture()], batch: batchFixture(), last_operation: succeeded(command.operation_id) });
      }
      return reply(server);
    });
    vi.stubGlobal("fetch", fetcher);
    const conversation = placesConversation();
    const hook = renderHook(({ open }) => useChatPlaces(conversation, open, false), { initialProps: { open: false } });
    expect(fetcher).not.toHaveBeenCalled();
    hook.rerender({ open: true });
    await waitFor(() => expect(hook.result.current.state.workspace?.batch).toBeTruthy());
    act(() => hook.result.current.preview(placeFixture().id)); expect(fetcher).toHaveBeenCalledTimes(1);
    await act(async () => hook.result.current.act({ action: "confirm", batch_id: batchFixture().id, place_id: placeFixture().id, role: "required" }));
    expect(hook.result.current.state.workspace?.required_places).toHaveLength(1);
    const body = JSON.parse(fetcher.mock.calls[1][1]!.body as string);
    expect(body).not.toHaveProperty("place"); expect(body).not.toHaveProperty("owner_id");
    expect(body.place_id).toBe(placeFixture().id);
    hook.rerender({ open: false });
    expect(hook.result.current.state.workspace?.required_places).toHaveLength(1);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it("blocks old confirmation callbacks after input edit and rejects duplicate or full required lists", async () => {
    let server = placesFixture({ batch: batchFixture() });
    const fetcher = vi.fn(async () => reply(server)); vi.stubGlobal("fetch", fetcher);
    const hook = renderHook(() => useChatPlaces(placesConversation(), true, false));
    await waitFor(() => expect(hook.result.current.state.workspace).toBeTruthy());
    act(() => hook.result.current.preview(placeFixture().id));
    const old = hook.result.current.act;
    act(() => hook.result.current.edit());
    await act(async () => old({ action: "confirm", batch_id: batchFixture().id, place_id: placeFixture().id, role: "required" }));
    expect(fetcher).toHaveBeenCalledTimes(1);
    server = placesFixture({ batch: batchFixture(), required_places: [placeFixture()] });
    await act(async () => hook.result.current.reload());
    act(() => hook.result.current.preview(placeFixture().id));
    await act(async () => hook.result.current.act({ action: "confirm", batch_id: batchFixture().id, place_id: placeFixture().id, role: "required" }));
    expect(fetcher).toHaveBeenCalledTimes(2);
    server = placesFixture({ batch: batchFixture(), required_places: Array.from({ length: 6 }, (_, i) => placeFixture(`controlled-${i}`)) });
    await act(async () => hook.result.current.reload());
    act(() => hook.result.current.preview(placeFixture().id));
    await act(async () => hook.result.current.act({ action: "confirm", batch_id: batchFixture().id, place_id: placeFixture().id, role: "required" }));
    expect(fetcher).toHaveBeenCalledTimes(3);
  });
  it.each(["success", "error"])("isolates late %s and finally after A→B→A", async outcome => {
    const late = pending<Response>(); let posts = 0;
    const fetcher = vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        const body = JSON.parse(init.body as string);
        if (body.action === "cancel") return reply(placesFixture({ revision: 2, last_operation: { ...succeeded(body.operation_id), state: "cancelled" } }));
        posts += 1; return late.promise;
      }
      return reply(placesFixture());
    }); vi.stubGlobal("fetch", fetcher);
    const a = placesConversation(), b = { ...a, id: "20000000-0000-4000-8000-000000000001" };
    const hook = renderHook(({ conversation }) => useChatPlaces(conversation, true, false), { initialProps: { conversation: a } });
    await waitFor(() => expect(hook.result.current.state.workspace).toBeTruthy());
    let operation!: Promise<void>;
    act(() => { operation = hook.result.current.act({ action: "search", keyword: "受控", role: "required" }); });
    hook.rerender({ conversation: b }); hook.rerender({ conversation: { ...a } });
    await waitFor(() => expect(hook.result.current.state.pending).toBe(false));
    await act(async () => { late.resolve(outcome === "success" ? reply(placesFixture({ batch: batchFixture(), last_operation: succeeded() })) : new Response("{}", { status: 502 })); await operation; });
    expect(hook.result.current.state.workspace?.batch).toBeNull(); expect(hook.result.current.state.error).toBeNull();
    expect(hook.result.current.state.pending).toBe(false); expect(posts).toBe(1);
    expect(fetcher.mock.calls.filter(([, init]) => init?.method === "POST" && JSON.parse(init.body as string).action === "cancel")).toHaveLength(1);
  });
  it("blocks repeat submits, cancellation does not retry, and latest intent makes old batch unusable", async () => {
    const late = pending<Response>(); let operationId = "";
    const conversation = placesConversation();
    const fetcher = vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        const request = JSON.parse(init.body as string); operationId = request.operation_id;
        if (request.action === "cancel") return reply(placesFixture());
        return late.promise;
      }
      return reply(placesFixture());
    }); vi.stubGlobal("fetch", fetcher);
    const hook = renderHook(({ c }) => useChatPlaces(c, true, false), { initialProps: { c: conversation } });
    await waitFor(() => expect(hook.result.current.state.workspace).toBeTruthy());
    let work!: Promise<void>;
    act(() => { const captured = hook.result.current.act; work = captured({ action: "ask", text: "在苏州查受控地点" }); void captured({ action: "ask", text: "在苏州查受控地点" }); });
    await act(async () => hook.result.current.cancel());
    expect(hook.result.current.state.needsRead).toBe(true);
    await act(async () => { late.resolve(reply(placesFixture({ revision: 1, batch: batchFixture(), last_operation: succeeded(operationId) }))); await work; });
    expect(hook.result.current.state.workspace?.batch).toBeNull();
    expect(fetcher.mock.calls.filter(([, init]) => init?.method === "POST" && JSON.parse(init.body as string).action === "ask")).toHaveLength(1);
    hook.rerender({ c: { ...conversation, revision: 3 } });
    await waitFor(() => expect(hook.result.current.state.pending).toBe(false));
    expect(hook.result.current.usable).toBe(false);
  });
  it("requires re-read after failed or uncertain writes instead of silently sending a new key", async () => {
    const fetcher = vi.fn(async (_url: string, init?: RequestInit) => init?.method === "POST" ? new Response("{}", { status: 502 }) : reply(placesFixture()));
    vi.stubGlobal("fetch", fetcher); const conversation = placesConversation();
    const hook = renderHook(() => useChatPlaces(conversation, true, false));
    await waitFor(() => expect(hook.result.current.state.workspace).toBeTruthy());
    await act(async () => hook.result.current.act({ action: "search", keyword: "受控", role: "required" }));
    await act(async () => hook.result.current.act({ action: "search", keyword: "受控", role: "required" }));
    expect(hook.result.current.state.needsRead).toBe(true); expect(fetcher).toHaveBeenCalledTimes(2);
    expect(hook.result.current.state.workspace?.conversation_id).toBe(CHAT_ID);
  });
  it("shows a failed model receipt as failure even when its transport is HTTP 200", async () => {
    const fetcher = vi.fn(async (_url: string, init?: RequestInit) => reply(init?.method === "POST" ? placesFixture({ revision: 1,
      last_operation: { ...succeeded(JSON.parse(init.body as string).operation_id), state: "failed", error_code: "model_structure" } }) : placesFixture()));
    vi.stubGlobal("fetch", fetcher); const conversation = placesConversation();
    const hook = renderHook(() => useChatPlaces(conversation, true, false));
    await waitFor(() => expect(hook.result.current.state.workspace).toBeTruthy());
    await act(async () => hook.result.current.act({ action: "ask", text: "帮我找一个地点" }));
    expect(hook.result.current.state.error).toContain("未通过校验");
    expect(hook.result.current.state.workspace?.batch).toBeNull();
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it("reconciles an uncertain receipt by explicit read, without replaying it or permanently locking local actions", async () => {
    const unknownId = "50000000-0000-4000-8000-000000000001";
    const old = placesFixture({ revision: 2, last_operation: { ...succeeded(unknownId), state: "uncertain", error_code: "request_uncertain" } });
    const fetcher = vi.fn(async (_url: string, init?: RequestInit) => reply(init?.method === "POST" ? placesFixture({ revision: 3, city_id: "310000",
      last_operation: succeeded(JSON.parse(init.body as string).operation_id) }) : old));
    vi.stubGlobal("fetch", fetcher); const conversation = placesConversation();
    const hook = renderHook(() => useChatPlaces(conversation, true, false));
    await waitFor(() => expect(hook.result.current.state.workspace).toBeTruthy());
    expect(hook.result.current.state.notice).toContain("旧操作结果仍未知");
    expect(fetcher).toHaveBeenCalledTimes(1);
    await act(async () => hook.result.current.act({ action: "set_city", city_id: "310000" }));
    const command = JSON.parse(fetcher.mock.calls[1][1]!.body as string);
    expect(command.action).toBe("set_city"); expect(command.operation_id).not.toBe(unknownId);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});
