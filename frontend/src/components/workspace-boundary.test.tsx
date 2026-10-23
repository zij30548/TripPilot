import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { WorkspaceBoundary } from "./workspace-boundary";

const csrf = "c".repeat(43);
const empty = { authenticated: false, user: null, csrf_token: csrf, expires_at: null, session_id: null };
const session = (identity = "a") => ({ authenticated: true, user: { id: `workspace-${identity}`, username: "当前工作区" }, csrf_token: csrf,
  expires_at: new Date(Date.now() + 3600_000).toISOString(), session_id: identity.repeat(32) });
const response = (body: unknown, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
const deferred = <T,>() => { let resolve!: (value: T) => void; const promise = new Promise<T>(yes => { resolve = yes; }); return { promise, resolve }; };
function Work() { const [draft, setDraft] = useState(""); return <label>当前工作<input value={draft} onChange={event => setDraft(event.target.value)} /></label>; }

describe("credential-free browser workspace boundary", () => {
  it("opens after isolated bootstrap with no credential form or account controls", async () => {
    vi.stubGlobal("BroadcastChannel", undefined);
    const fetcher = vi.fn().mockResolvedValueOnce(response(empty)).mockResolvedValueOnce(response(session()));
    vi.stubGlobal("fetch", fetcher); render(<WorkspaceBoundary><Work /></WorkspaceBoundary>);
    expect(screen.queryByLabelText("当前工作")).toBeNull(); await screen.findByLabelText("当前工作");
    expect(screen.queryByText(/登录|注册|邀请码|退出/)).toBeNull();
    expect(screen.queryByLabelText(/账号|密码/)).toBeNull(); expect(screen.queryByRole("form")).toBeNull();
    expect(fetcher.mock.calls.map(call => call[0])).toEqual(["/api/workspace/session", "/api/workspace/session"]);
  });

  it("keeps local work mounted through a same-workspace recheck", async () => {
    vi.stubGlobal("BroadcastChannel", undefined); const pending = deferred<ReturnType<typeof response>>();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(response(session())).mockReturnValueOnce(pending.promise));
    render(<WorkspaceBoundary><Work /></WorkspaceBoundary>);
    const input = await screen.findByLabelText("当前工作");
    fireEvent.change(input, { target: { value: "仍在编辑" } }); fireEvent.focus(window);
    await screen.findByText("正在连接工作区…"); await act(async () => pending.resolve(response(session())));
    await waitFor(() => expect(screen.queryByText("正在连接工作区…")).toBeNull());
    expect(screen.getByLabelText("当前工作")).toBe(input); expect((input as HTMLInputElement).value).toBe("仍在编辑");
  });

  it("clears the local subtree when the server workspace changes", async () => {
    vi.stubGlobal("BroadcastChannel", undefined);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(response(session())).mockResolvedValueOnce(response(session("b"))));
    render(<WorkspaceBoundary><Work /></WorkspaceBoundary>);
    fireEvent.change(await screen.findByLabelText("当前工作"), { target: { value: "旧工作区" } }); fireEvent.focus(window);
    await waitFor(() => expect((screen.getByLabelText("当前工作") as HTMLInputElement).value).toBe(""));
  });

  it("offers only an explicit retry after failed bootstrap", async () => {
    vi.stubGlobal("BroadcastChannel", undefined);
    const fetcher = vi.fn().mockResolvedValueOnce(response(empty)).mockResolvedValueOnce(response({}, 503)).mockResolvedValueOnce(response(session()));
    vi.stubGlobal("fetch", fetcher); render(<WorkspaceBoundary><Work /></WorkspaceBoundary>);
    await screen.findByRole("button", { name: "重新连接工作区" });
    expect(screen.queryByLabelText("当前工作")).toBeNull(); expect(fetcher).toHaveBeenCalledTimes(2);
    expect(screen.queryByText(/登录|注册|邀请码|退出/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "重新连接工作区" }));
    await screen.findByLabelText("当前工作"); expect(fetcher).toHaveBeenCalledTimes(3);
  });
});
