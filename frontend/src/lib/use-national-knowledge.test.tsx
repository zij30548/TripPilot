import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useNationalKnowledge } from "./use-national-knowledge";
import { nationalCatalogFixture, nationalResponseFixture } from "./knowledge-national.test-fixtures";

const auth = vi.hoisted(() => ({ epoch: 0, listeners: new Set<() => void>() }));
vi.mock("./auth-session", async importOriginal => ({ ...await importOriginal<typeof import("./auth-session")>(), authSnapshot: () => ({ epoch: auth.epoch }), subscribeAuth: (listener: () => void) => { auth.listeners.add(listener); return () => auth.listeners.delete(listener); } }));
const response = (data: unknown) => ({ ok: true, text: async () => JSON.stringify(data) }) as Response;
const defer = () => { let resolve!: (value: Response) => void, reject!: (error: Error) => void; const promise = new Promise<Response>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
let fetcher: ReturnType<typeof vi.fn>;
beforeEach(() => { auth.epoch = 0; auth.listeners.clear(); fetcher = vi.fn().mockResolvedValueOnce(response(nationalCatalogFixture())); vi.stubGlobal("fetch", fetcher); });
async function start() { const hook = renderHook(() => useNationalKnowledge()); await act(async () => {}); return hook; }
function fill(hook: ReturnType<typeof useNationalKnowledge>) { hook.update({ city_id: "310000", question: "预约说明" }); hook.update({ venue_id: "museum_people" }); }

describe("national reference active queries and monotonic isolation", () => {
  it("does not even load the local directory while hidden; navigation retains existing inputs/results", async () => {
    const hook = renderHook(({ active }) => useNationalKnowledge(active), { initialProps: { active: false } });
    expect(fetcher).not.toHaveBeenCalled(); hook.rerender({ active: true }); await act(async () => {});
    expect(fetcher).toHaveBeenCalledTimes(1); expect(fetcher.mock.calls[0][0]).toBe("/api/knowledge/catalog-v2");
    act(() => fill(hook.result.current)); fetcher.mockResolvedValueOnce(response(nationalResponseFixture()));
    await act(() => hook.result.current.query()); hook.rerender({ active: false }); hook.rerender({ active: true });
    expect(hook.result.current.input.question).toBe("预约说明"); expect(hook.result.current.state.status).toBe("success"); expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it("edits do not search, duplicate submit is blocked, and only the exact request is sent", async () => {
    const hook = await start(), pending = defer(); act(() => fill(hook.result.current)); expect(fetcher).toHaveBeenCalledTimes(1);
    fetcher.mockReturnValueOnce(pending.promise); let operation!: Promise<void>;
    act(() => { operation = hook.result.current.query(); void hook.result.current.query(); });
    expect(fetcher).toHaveBeenCalledTimes(2); expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual(nationalResponseFixture().request);
    await act(async () => { pending.resolve(response(nationalResponseFixture())); await operation; });
    act(() => hook.result.current.update({ visit_date: "2026-11-01" }));
    expect(hook.result.current.state.response).toBeNull(); expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it.each(["success", "error"])("city A→B→A isolates old %s and finally, including old cancel callback", async outcome => {
    const hook = await start(), old = defer(), current = defer(); act(() => fill(hook.result.current));
    fetcher.mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise); let first!: Promise<void>, second!: Promise<void>;
    act(() => { first = hook.result.current.query(); }); const oldCancel = hook.result.current.cancel;
    act(() => { hook.result.current.update({ city_id: "110000" }); fill(hook.result.current); second = hook.result.current.query(); });
    act(() => oldCancel()); expect(hook.result.current.state.status).toBe("loading");
    await act(async () => { if (outcome === "success") old.resolve(response(nationalResponseFixture())); else old.reject(new Error("private old error")); await first; });
    expect(hook.result.current.state.status).toBe("loading"); act(() => { void hook.result.current.query(); }); expect(fetcher).toHaveBeenCalledTimes(3);
    await act(async () => { current.resolve(response(nationalResponseFixture())); await second; }); expect(hook.result.current.state.status).toBe("success");
  });
  it("type changes clear incompatible venue/results without a query", async () => {
    const hook = await start(); act(() => fill(hook.result.current)); act(() => hook.result.current.update({ kind: "amusement_park" }));
    expect(hook.result.current.input.venue_id).toBe(""); expect(fetcher).toHaveBeenCalledTimes(1);
    await act(() => hook.result.current.query()); expect(hook.result.current.state.status).toBe("failed"); expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it("cancel and unmount reject late result and cannot restart an old callback", async () => {
    const hook = await start(), pending = defer(); act(() => fill(hook.result.current)); fetcher.mockReturnValueOnce(pending.promise);
    let operation!: Promise<void>; act(() => { operation = hook.result.current.query(); }); act(() => hook.result.current.cancel());
    expect(hook.result.current.state.message).toContain("取消"); const query = hook.result.current.query; hook.unmount();
    await act(async () => { pending.resolve(response(nationalResponseFixture())); await operation; await query(); }); expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it("owner epoch changes synchronously invalidate old work and clear the query before late results", async () => {
    const hook = await start(), pending = defer(); act(() => fill(hook.result.current)); fetcher.mockReturnValueOnce(pending.promise);
    let operation!: Promise<void>; act(() => { operation = hook.result.current.query(); });
    fetcher.mockResolvedValueOnce(response(nationalCatalogFixture()));
    act(() => { auth.epoch++; for (const listener of auth.listeners) listener(); });
    expect(hook.result.current.input.venue_id).toBe(""); expect(hook.result.current.state.response).toBeNull();
    await act(async () => { pending.resolve(response(nationalResponseFixture())); await operation; });
    expect(hook.result.current.state.response).toBeNull(); expect(hook.result.current.input.question).toBe("");
  });
  it("safe failure has no retry or raw details; explicit retry is allowed", async () => {
    const hook = await start(); act(() => fill(hook.result.current)); fetcher.mockRejectedValueOnce(new Error("private upstream"));
    await act(() => hook.result.current.query()); expect(hook.result.current.state.status).toBe("failed"); expect(hook.result.current.state.message).not.toContain("private"); expect(fetcher).toHaveBeenCalledTimes(2);
    fetcher.mockResolvedValueOnce(response(nationalResponseFixture())); await act(() => hook.result.current.query()); expect(hook.result.current.state.status).toBe("success");
  });
});
