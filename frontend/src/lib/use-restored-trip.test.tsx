import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { readTrip } from "./saved-trips";
import { useRestoredTrip } from "./use-restored-trip";
import { savedFixture } from "./restore-trip.test-fixtures";
import { deferred } from "./weather-schedule-check.test-fixtures";
import type { SavedDetail } from "./saved-trips";
vi.mock("./saved-trips", async original => ({ ...await original<typeof import("./saved-trips")>(), readTrip: vi.fn() }));
const api=vi.mocked(readTrip), a="a".repeat(32),b="b".repeat(32);
beforeEach(()=>{api.mockReset();});
describe("archive source and copy sessions",()=>{
  it("reads once, publishes a complete independent copy and a new identity on each opening",async()=>{
    const record=savedFixture();api.mockResolvedValue(record);
    const one=renderHook(()=>useRestoredTrip(a));expect(one.result.current.copy).toBeNull();
    await act(async()=>{});const first=one.result.current.copy!;expect(first.source).toEqual(record);expect(first.inputs).toBeTruthy();
    one.rerender();expect(api).toHaveBeenCalledTimes(1);one.unmount();
    const two=renderHook(()=>useRestoredTrip(a));await act(async()=>{});
    expect(two.result.current.copy!.sessionId).not.toBe(first.sessionId);expect(two.result.current.copy!.inputs).toEqual(first.inputs);
  });
  it.each(["success","error"])("A→B→A isolates old %s and never resurrects a previously accepted A",async outcome=>{
    const oldB=deferred<SavedDetail>(),freshA=deferred<SavedDetail>();
    api.mockResolvedValueOnce(savedFixture()).mockReturnValueOnce(oldB.promise).mockReturnValueOnce(freshA.promise);
    const view=renderHook(({id})=>useRestoredTrip(id),{initialProps:{id:a}});await act(async()=>{});const first=view.result.current.copy!;
    view.rerender({id:b});expect(view.result.current.copy).toBeNull();const signal=api.mock.calls[1][1];
    view.rerender({id:a});expect(view.result.current.copy).toBeNull();expect(signal?.aborted).toBe(true);
    await act(async()=>{if(outcome==="success")oldB.resolve(savedFixture({},b));else oldB.reject(new Error("obsolete"));});
    expect(view.result.current.copy).toBeNull();expect(view.result.current.error).toBeNull();
    await act(async()=>{freshA.resolve(savedFixture());});expect(view.result.current.copy!.sessionId).not.toBe(first.sessionId);
  });
  it("rejects corrupt records atomically; retries only active error, never old error handlers",async()=>{
    const broken=savedFixture();broken.snapshot.schedule.days[0].items[0].edge_id="foreign";
    api.mockResolvedValueOnce(broken).mockResolvedValueOnce(savedFixture());const view=renderHook(()=>useRestoredTrip(a));await act(async()=>{});
    expect(view.result.current.copy).toBeNull();expect(view.result.current.error).toContain("未创建");const retry=view.result.current.retry;
    await act(async()=>{retry();retry();});expect(api).toHaveBeenCalledTimes(2);expect(view.result.current.copy).not.toBeNull();
    act(()=>retry());expect(api).toHaveBeenCalledTimes(2);view.unmount();act(()=>retry());expect(api).toHaveBeenCalledTimes(2);
  });
  it.each(["success","error"])("unmount aborts pending read and ignores %s",async outcome=>{
    const pending=deferred<SavedDetail>();api.mockReturnValue(pending.promise);const view=renderHook(()=>useRestoredTrip(a));
    const signal=api.mock.calls[0][1];view.unmount();expect(signal?.aborted).toBe(true);
    await act(async()=>{if(outcome==="success")pending.resolve(savedFixture());else pending.reject(new Error("obsolete"));});
    expect(view.result.current.copy).toBeNull();expect(view.result.current.error).toBeNull();
  });
});
