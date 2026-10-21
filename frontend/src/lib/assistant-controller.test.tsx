import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAssistantState } from "./assistant-controller";
import type { AssistantState } from "./assistant";
import { CHAT_ID, OP_ID, OTHER_CHAT_ID, conversationFixture, deferred } from "./chat-test-fixtures";

const api = vi.hoisted(() => ({ read: vi.fn(), epoch: 0 }));
vi.mock("./assistant", () => ({ readAssistantState: api.read }));
vi.mock("./auth-session", () => ({ authSnapshot: () => ({ epoch: api.epoch }) }));
const fixture = (id = CHAT_ID, revision = 2): AssistantState => ({ schema_version: "assistant-state-v1", conversation_id: id, revision, task: { id: OP_ID, state: "succeeded", model_calls: 1, tool_calls: 1, stop_reason: null, steps: [] }, migration_ready: true, preferences: [], skills: [] });
beforeEach(() => { vi.resetAllMocks(); api.epoch = 0; api.read.mockImplementation(async (id, revision) => fixture(id, revision)); });

describe("assistant receipt isolation (controlled responses)", () => {
  it("mount/ordinary rerender are local; explicit read runs once and duplicate clicks do not overlap", async () => {
    const work = deferred<AssistantState>(); api.read.mockReturnValueOnce(work.promise);
    const view = renderHook(({ full, blocked }) => useAssistantState(full, blocked), { initialProps: { full: conversationFixture(), blocked: false } });
    expect(api.read).not.toHaveBeenCalled();
    view.rerender({ full: conversationFixture(), blocked: false }); expect(api.read).not.toHaveBeenCalled();
    act(() => { void view.result.current.reload(); void view.result.current.reload(); });
    expect(api.read).toHaveBeenCalledTimes(1);
    await act(async () => work.resolve(fixture()));
    expect(view.result.current.state.value?.task?.state).toBe("succeeded");
  });
  it.each(["success", "error"])("A→B→A rejects old %s and its finally, and stale buttons cannot read the new conversation", async outcome => {
    const first = deferred<AssistantState>(); api.read.mockReturnValueOnce(first.promise);
    const view = renderHook(({ full }) => useAssistantState(full, false), { initialProps: { full: conversationFixture() } });
    const oldButton = view.result.current.reload;
    act(() => { void oldButton(); });
    view.rerender({ full: conversationFixture({ id: OTHER_CHAT_ID }) });
    expect(api.read.mock.calls[0][2].aborted).toBe(true);
    view.rerender({ full: conversationFixture() });
    await act(async () => oldButton()); expect(api.read).toHaveBeenCalledTimes(1);
    await act(async () => view.result.current.reload());
    const current = view.result.current.state.value;
    await act(async () => { if (outcome === "success") first.resolve(fixture()); else first.reject(new Error("stale")); });
    expect(view.result.current.state.value).toBe(current);
    expect(view.result.current.state.error).toBeNull();
  });
  it("new revision, pending turn, account change, cancellation and unmount all discard old receipt", async () => {
    const view = renderHook(({ full, blocked }) => useAssistantState(full, blocked), { initialProps: { full: conversationFixture(), blocked: false } });
    await act(async () => view.result.current.reload());
    view.rerender({ full: conversationFixture({ revision: 3 }), blocked: false });
    expect(view.result.current.state.value).toBeNull();
    await act(async () => view.result.current.reload());
    view.rerender({ full: conversationFixture({ revision: 3 }), blocked: true });
    expect(view.result.current.state.value).toBeNull();
    await act(async () => view.result.current.reload()); expect(api.read).toHaveBeenCalledTimes(2);
    view.rerender({ full: conversationFixture(), blocked: false });
    await act(async () => view.result.current.reload());
    api.epoch = 1; view.rerender({ full: conversationFixture(), blocked: false });
    expect(view.result.current.state.value).toBeNull();
    const late = deferred<AssistantState>(); api.read.mockReturnValueOnce(late.promise);
    act(() => { void view.result.current.reload(); });
    act(() => view.result.current.cancel());
    expect(api.read.mock.calls.at(-1)![2].aborted).toBe(true);
    expect(view.result.current.state.pending).toBe(false);
    await act(async () => late.resolve(fixture())); expect(view.result.current.state.value).toBeNull();
    const unmounted = deferred<AssistantState>(); api.read.mockReturnValueOnce(unmounted.promise);
    act(() => { void view.result.current.reload(); }); view.unmount();
    expect(api.read.mock.calls.at(-1)![2].aborted).toBe(true);
    await act(async () => unmounted.reject(new Error("late")));
  });
  it("read error is explicit, clears on context change, and cannot cause a model retry", async () => {
    api.read.mockRejectedValueOnce(new Error("migration missing"));
    const view = renderHook(({ full }) => useAssistantState(full, false), { initialProps: { full: conversationFixture() } });
    await act(async () => view.result.current.reload());
    await waitFor(() => expect(view.result.current.state.error).toBe("migration missing"));
    view.rerender({ full: conversationFixture({ id: OTHER_CHAT_ID }) });
    expect(view.result.current.state.error).toBeNull(); expect(api.read).toHaveBeenCalledTimes(1);
  });
  it.each(["success", "error"])("account change rejects a late %s even when transport ignores cancellation", async outcome => {
    const late = deferred<AssistantState>(); api.read.mockReturnValueOnce(late.promise);
    const view = renderHook(({ full }) => useAssistantState(full, false), { initialProps: { full: conversationFixture() } });
    act(() => { void view.result.current.reload(); });
    const oldRead = view.result.current.reload;
    api.epoch = 1; view.rerender({ full: conversationFixture() });
    expect(api.read.mock.calls[0][2].aborted).toBe(true);
    await act(async () => oldRead()); expect(api.read).toHaveBeenCalledTimes(1);
    await act(async () => { if (outcome === "success") late.resolve(fixture()); else late.reject(new Error("previous account")); });
    expect(view.result.current.state.value).toBeNull();
    expect(view.result.current.state.error).toBeNull();
    expect(view.result.current.state.pending).toBe(false);
  });
});
