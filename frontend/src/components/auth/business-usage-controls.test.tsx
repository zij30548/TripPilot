import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import { BusinessUsageControls } from "./business-usage-controls";
import { queryBusinessUsage, type BusinessUsage } from "@/lib/business-usage";
import { BUSINESS_SERVICES, clearBusinessUsageNotice, recordBusinessUsageNotice } from "@/lib/business-usage-notices";

const identity = vi.hoisted(() => ({ epoch: 11, phase: "authenticated" }));
vi.mock("@/lib/auth-session", () => ({ authSnapshot: () => identity }));
vi.mock("@/lib/business-usage", () => ({ queryBusinessUsage: vi.fn() }));
const deferred = <T,>() => { let resolve!: (value: T) => void; let reject!: (error: Error) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const fixture = (): BusinessUsage => ({ version: "business-usage-v1", timezone: "Asia/Shanghai", now: "2026-10-18T09:00:01+08:00", reset_at: "2026-10-19T00:00:00+08:00",
  rate: { used: 3, limit: 60, window_seconds: 60, reset_at: "2026-10-18T09:01:00+08:00" },
  services: Object.fromEntries(BUSINESS_SERVICES.map(service => [service, { used: 1, limit: 10, remaining: 9 }])) as BusinessUsage["services"] });
afterEach(() => { vi.mocked(queryBusinessUsage).mockReset(); clearBusinessUsageNotice(); identity.epoch = 11; identity.phase = "authenticated"; });

describe("manual own-account quota panel", () => {
  it("does not query on mount or unrelated work edits; opening/closing never unmounts workspace state", async () => {
    vi.mocked(queryBusinessUsage).mockResolvedValue(fixture());
    function Composite() { const [value, setValue] = useState(""); return <><BusinessUsageControls epoch={11} /><label>正式费用<input value={value} onChange={event => setValue(event.target.value)} /></label></>; }
    render(<Composite />);
    expect(queryBusinessUsage).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("正式费用"), { target: { value: "120.00" } });
    expect(queryBusinessUsage).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "我的调用额度" }));
    await screen.findByText("地点搜索");
    expect(screen.getAllByText("所示日期已计 1 / 10，剩余 9")).toHaveLength(8);
    expect(screen.getByText(/不是精确人民币费用上限/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "关闭额度" }));
    expect((screen.getByLabelText("正式费用") as HTMLInputElement).value).toBe("120.00");
    expect(queryBusinessUsage).toHaveBeenCalledTimes(1);
  });
  it("blocks duplicates, aborts on close, and ignores late success/error/finally even after reopening", async () => {
    const first = deferred<BusinessUsage>(); const second = deferred<BusinessUsage>();
    vi.mocked(queryBusinessUsage).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    render(<BusinessUsageControls epoch={11} />);
    fireEvent.click(screen.getByRole("button", { name: "我的调用额度" }));
    fireEvent.click(screen.getByRole("button", { name: "刷新额度" }));
    expect(queryBusinessUsage).toHaveBeenCalledTimes(1);
    const firstSignal = vi.mocked(queryBusinessUsage).mock.calls[0][0];
    fireEvent.click(screen.getByRole("button", { name: "取消查询并关闭" }));
    expect(firstSignal.aborted).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "我的调用额度" }));
    await act(async () => { first.reject(new Error("old failure")); });
    expect(screen.queryByText("old failure")).toBeNull();
    expect(screen.getByText("正在读取调用额度…")).toBeTruthy();
    await act(async () => { second.resolve(fixture()); });
    await screen.findByText("视频抽帧");
  });
  it("does not restore previous-user counters after identity ABA and aborts on unmount", async () => {
    const old = deferred<BusinessUsage>();
    vi.mocked(queryBusinessUsage).mockReturnValue(old.promise);
    const view = render(<BusinessUsageControls key={11} epoch={11} />);
    fireEvent.click(screen.getByRole("button", { name: "我的调用额度" }));
    const signal = vi.mocked(queryBusinessUsage).mock.calls[0][0];
    identity.epoch = 13;
    view.rerender(<BusinessUsageControls key={13} epoch={13} />);
    expect(signal.aborted).toBe(true);
    await act(async () => { old.resolve(fixture()); });
    expect(screen.queryByText("地点搜索")).toBeNull();
    expect(screen.queryByText("正在读取调用额度…")).toBeNull();
  });
  it("shows fixed limit notices and permits closing them without automatically retrying business", async () => {
    render(<BusinessUsageControls epoch={11} />);
    act(() => recordBusinessUsageNotice(new Response(null, { status: 429, headers: { "X-Business-Usage-Code": "business_rate_limited", "X-Business-Usage-Scope": "account", "Retry-After": "12" } }), 11));
    expect(screen.getByRole("alert").textContent).toContain("至少等待 12 秒后手动重试");
    expect(queryBusinessUsage).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "关闭提示" }));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  });
});
