import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useDialoguePlaces } from "./dialogue-places-controller";
import { batchFixture, placeFixture, placesConversation, placesFixture, succeeded } from "./chat-places-test-fixtures";
import { deferred, OTHER_CHAT_ID } from "./chat-test-fixtures";
import type { PlacesRequest } from "./chat-places";
const response = (value: unknown) => new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } });

describe("dialogue POI commands retain server batch authority", () => {
  it("explicit ensureReady can read→sync→set city→search in one operation without a stale render", async () => {
    let server = placesFixture({ intent_revision: 1, stale: true });
    const fetcher = vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.body) {
        const command = JSON.parse(String(init.body));
        server = { ...server, revision: server.revision + 1, intent_revision: 2, stale: false, last_operation: succeeded(command.operation_id),
          ...(command.action === "sync" ? { city_id: null } : {}), ...(command.action === "set_city" ? { city_id: command.city_id } : {}) };
      }
      return response(server);
    }); vi.stubGlobal("fetch", fetcher);
    const conversation = placesConversation(); conversation.requirements.destination_id = "shanghai";
    const hook = renderHook(() => useDialoguePlaces(conversation));
    await act(async () => {
      const bound = hook.result.current;
      expect((await bound.ensureReady(true)).workspace?.city_id).toBe("310000");
      await bound.search("受控地点", "required");
    });
    expect(fetcher.mock.calls.map(([, init]) => init?.body ? JSON.parse(String(init.body)).action : "read")).toEqual(["read", "sync", "set_city", "search"]);
    expect(hook.result.current.current()?.city_id).toBe("310000");
  });
  it("unconfirmed readiness is read-only and a changed revision prevents queued sync/city writes", async () => {
    const late = deferred<Response>(); vi.stubGlobal("fetch", vi.fn(async () => late.promise));
    const c = placesConversation();
    const hook = renderHook(({ conversation }) => useDialoguePlaces(conversation), { initialProps: { conversation: c } });
    let pending!: ReturnType<typeof hook.result.current.ensureReady>;
    act(() => { pending = hook.result.current.ensureReady(true); });
    hook.rerender({ conversation: { ...c, revision: 3 } });
    await act(async () => { late.resolve(response(placesFixture({ intent_revision: 1, stale: true }))); await pending; });
    expect(fetch).toHaveBeenCalledTimes(1); expect(hook.result.current.current()).toBeNull();
    vi.stubGlobal("fetch", vi.fn(async () => response(placesFixture({ intent_revision: 2, stale: true }))));
    await act(async () => { expect((await hook.result.current.ensureReady()).text).toContain("明确同意同步"); });
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("an exact fresh read can be previewed before render, but an old snapshot cannot bind a new ordinal", async () => {
    let server = placesFixture({ batch: batchFixture() }); vi.stubGlobal("fetch", vi.fn(async () => response(server)));
    const hook = renderHook(() => useDialoguePlaces(placesConversation())); let old!: ReturnType<typeof placesFixture>;
    await act(async () => {
      const controller = hook.result.current, read = await controller.read(); old = read.workspace!;
      expect(controller.preview(1, read.workspace).previewToken).toBeTruthy();
    });
    server = placesFixture({ revision: 2, batch: { ...batchFixture(), id: "40000000-0000-4000-8000-000000000002" } });
    await act(async () => { await hook.result.current.read(); expect(hook.result.current.preview(1, old).previewToken).toBeUndefined(); });
  });
  it("mount and preview do not query, explicit confirmation sends only identity fields", async () => {
    let server = placesFixture();
    const fetcher = vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        const command: PlacesRequest = JSON.parse(String(init.body));
        server = { ...server, revision: server.revision + 1, last_operation: succeeded(command.operation_id),
          ...(command.action === "search" ? { batch: batchFixture() } : {}),
          ...(command.action === "confirm" ? { required_places: [placeFixture()] } : {}) };
      }
      return response(server);
    }); vi.stubGlobal("fetch", fetcher);
    const hook = renderHook(() => useDialoguePlaces(placesConversation()));
    expect(fetcher).not.toHaveBeenCalled();
    await act(async () => { await hook.result.current.read(); });
    await act(async () => { expect((await hook.result.current.search("受控景点", "required")).text).toContain("1. 受控景点"); });
    await act(async () => { expect((await hook.result.current.confirm(1)).text).toContain("请先预览"); });
    expect(fetcher).toHaveBeenCalledTimes(2);
    let token = "";
    act(() => { token = hook.result.current.preview(1).previewToken!; });
    expect(fetcher).toHaveBeenCalledTimes(2);
    await act(async () => { await hook.result.current.confirm(1, token); });
    expect(hook.result.current.current()?.required_places).toHaveLength(1);
    const body = JSON.parse(String(fetcher.mock.calls[2][1]?.body));
    expect(body).toMatchObject({ action: "confirm", place_id: "controlled-a", batch_id: batchFixture().id, role: "required" });
    expect(body).not.toHaveProperty("place"); expect(body).not.toHaveProperty("owner_id");
  });
  it("old confirmation callback and token cannot select the same number in another batch", async () => {
    let server = placesFixture({ batch: batchFixture() });
    vi.stubGlobal("fetch", vi.fn(async () => response(server)));
    const hook = renderHook(() => useDialoguePlaces(placesConversation()));
    await act(async () => { await hook.result.current.read(); });
    let token = ""; act(() => { token = hook.result.current.preview(1).previewToken!; });
    const oldConfirm = hook.result.current.confirm;
    server = placesFixture({ revision: 1, batch: { ...batchFixture([placeFixture("new", "同名新批次")]), id: "40000000-0000-4000-8000-000000000002" } });
    await act(async () => { await hook.result.current.read(); });
    act(() => { hook.result.current.preview(1); });
    await act(async () => { expect((await oldConfirm(1)).text).toContain("旧编号"); expect((await hook.result.current.confirm(1, token)).text).toContain("旧编号"); });
    expect(fetch).toHaveBeenCalledTimes(2);
  });
  it.each(["duplicate", "capacity"])("blocks %s at operation time", async kind => {
    const server = placesFixture({ batch: batchFixture(), required_places: kind === "duplicate" ? [placeFixture()] : Array.from({ length: 6 }, (_, i) => placeFixture(`p${i}`)) });
    vi.stubGlobal("fetch", vi.fn(async () => response(server)));
    const hook = renderHook(() => useDialoguePlaces(placesConversation()));
    await act(async () => { await hook.result.current.read(); }); act(() => { hook.result.current.preview(1); });
    await act(async () => { expect((await hook.result.current.confirm(1)).text).toContain(kind === "duplicate" ? "未重复加入" : "已达六个"); });
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it.each(["success", "error"])("isolates late %s/finally after A→B→A and does not retry search", async outcome => {
    const late = deferred<Response>(); let sent: PlacesRequest | undefined;
    const fetcher = vi.fn(async (_url: string, init?: RequestInit) => {
      const command = init?.body ? JSON.parse(String(init.body)) as PlacesRequest : undefined;
      if (command?.action === "search") { sent = command; return late.promise; }
      return response(placesFixture());
    }); vi.stubGlobal("fetch", fetcher);
    const a = placesConversation(), b = { ...a, id: OTHER_CHAT_ID };
    const hook = renderHook(({ c }) => useDialoguePlaces(c), { initialProps: { c: a } });
    await act(async () => { await hook.result.current.read(); });
    let pending!: ReturnType<typeof hook.result.current.search>;
    act(() => { pending = hook.result.current.search("受控景点", "required"); });
    hook.rerender({ c: b }); hook.rerender({ c: { ...a } });
    await act(async () => { await hook.result.current.read(); });
    await act(async () => { late.resolve(outcome === "success" ? response(placesFixture({ revision: 2, batch: batchFixture(), last_operation: succeeded(sent!.operation_id) })) : new Response("{}", { status: 502 })); await pending; });
    expect(hook.result.current.state.workspace?.batch).toBeNull(); expect(hook.result.current.state.pending).toBe(false); expect(hook.result.current.state.error).toBeNull();
    expect(fetcher.mock.calls.filter(([, init]) => init?.body && JSON.parse(String(init.body)).action === "search")).toHaveLength(1);
  });
  it("media source expires after search; confirmation stays blocked despite a persisted candidate", async () => {
    let server = placesFixture(); const source = new AbortController();
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.body) { const command = JSON.parse(String(init.body)); server = placesFixture({ revision: 1, batch: batchFixture(), last_operation: succeeded(command.operation_id) }); }
      return response(server);
    }));
    const hook = renderHook(() => useDialoguePlaces(placesConversation()));
    await act(async () => { await hook.result.current.read(); });
    await act(async () => { await hook.result.current.search("受控景点", "required", { signal: source.signal, isCurrent: () => !source.signal.aborted }); });
    act(() => { hook.result.current.preview(1); source.abort(); });
    await act(async () => { expect((await hook.result.current.confirm(1)).text).toContain("旧编号"); });
    expect(fetch).toHaveBeenCalledTimes(2);
  });
  it("an external busy transition stops an operation without leaving the local pending state stuck", async () => {
    const late = deferred<Response>();
    const fetcher = vi.fn(async (_url: string, init?: RequestInit) => init?.body && JSON.parse(String(init.body)).action === "search" ? late.promise : response(placesFixture()));
    vi.stubGlobal("fetch", fetcher);
    const hook = renderHook(({ blocked }) => useDialoguePlaces(placesConversation(), blocked), { initialProps: { blocked: false } });
    await act(async () => { await hook.result.current.read(); });
    let operation!: ReturnType<typeof hook.result.current.search>; act(() => { operation = hook.result.current.search("受控", "required"); });
    hook.rerender({ blocked: true }); expect(hook.result.current.state.pending).toBe(false);
    hook.rerender({ blocked: false }); expect(hook.result.current.state.needsRead).toBe(true); expect(hook.result.current.canUse()).toBe(false);
    await act(async () => { late.resolve(response(placesFixture())); await operation; });
    expect(hook.result.current.state.pending).toBe(false);
    await act(async () => { await hook.result.current.read(); }); expect(hook.result.current.canUse()).toBe(true);
  });
  it("stale intent requires explicit synchronization; removed-place selectors must be unique", async () => {
    let server = placesFixture({ stale: true });
    const fetcher = vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.body) { const command = JSON.parse(String(init.body)); server = placesFixture({ revision: 1, required_places: [placeFixture("a", "同名"), placeFixture("b", "同名")], last_operation: succeeded(command.operation_id) }); }
      return response(server);
    }); vi.stubGlobal("fetch", fetcher);
    const hook = renderHook(() => useDialoguePlaces(placesConversation()));
    await act(async () => { await hook.result.current.read(); expect((await hook.result.current.search("名字", "required")).text).toContain("同步"); });
    expect(fetcher).toHaveBeenCalledTimes(1);
    await act(async () => { await hook.result.current.sync(); });
    await act(async () => { expect((await hook.result.current.execute({ name: "remove_place", arguments: { selector: "同名" } })).text).toContain("没有找到"); });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});
