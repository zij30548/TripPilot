import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { AuthenticationBoundary } from "./authentication-boundary";

const csrf = "c".repeat(43);
const anonymous = { authenticated: false, user: null, csrf_token: csrf, expires_at: null, session_id: null };
const session = (username: string, identity: string) => ({ authenticated: true, user: { id: username, username }, csrf_token: csrf, expires_at: new Date(Date.now() + 3600_000).toISOString(), session_id: identity.repeat(32) });
const response = (body: unknown) => ({ ok: true, status: 200, json: async () => body });

function Work() {
  const [draft, setDraft] = useState("");
  return <label>当前工作<input value={draft} onChange={event => setDraft(event.target.value)} /></label>;
}

describe("whole-workspace invitation boundary", () => {
  it("gates every child until real session verification, logs in, and does not carry draft state to another account", async () => {
    vi.stubGlobal("BroadcastChannel", undefined);
    const fetcher = vi.fn().mockResolvedValueOnce(response(anonymous))
      .mockResolvedValueOnce(response(session("invite-a", "a"))).mockResolvedValueOnce(response(anonymous))
      .mockResolvedValueOnce(response(session("invite-b", "b")));
    vi.stubGlobal("fetch", fetcher);
    render(<AuthenticationBoundary><Work /></AuthenticationBoundary>);
    expect(screen.queryByLabelText("当前工作")).toBeNull();
    await screen.findByRole("button", { name: /^登录$/ });
    fireEvent.change(screen.getByLabelText("账号"), { target: { value: "invite-a" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "controlled-password" } });
    fireEvent.click(screen.getByRole("button", { name: /^登录$/ }));
    const input = await screen.findByLabelText("当前工作");
    fireEvent.change(input, { target: { value: "A private draft and costs" } });
    fireEvent.click(screen.getByRole("button", { name: "退出登录" }));
    expect(screen.queryByLabelText("当前工作")).toBeNull();
    await screen.findByRole("button", { name: /^登录$/ });
    fireEvent.change(screen.getByLabelText("账号"), { target: { value: "invite-b" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "other-controlled-password" } });
    fireEvent.click(screen.getByRole("button", { name: /^登录$/ }));
    await screen.findByText("邀请内测 · 当前账号：invite-b");
    expect((screen.getByLabelText("当前工作") as HTMLInputElement).value).toBe("");
    expect(fetcher.mock.calls.map(call => call[0])).toEqual(["/api/auth/session", "/api/auth/login", "/api/auth/logout", "/api/auth/login"]);
  });

  it("keeps a same-session work subtree mounted while a focus check is pending", async () => {
    let resolve!: (value: ReturnType<typeof response>) => void;
    vi.stubGlobal("BroadcastChannel", undefined);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(response(session("invite-a", "a"))).mockReturnValueOnce(new Promise(yes => { resolve = yes; })));
    render(<AuthenticationBoundary><Work /></AuthenticationBoundary>);
    await screen.findByText("邀请内测 · 当前账号：invite-a");
    const input = await screen.findByLabelText("当前工作");
    fireEvent.change(input, { target: { value: "still current" } });
    fireEvent.focus(window);
    await screen.findByText("正在核对登录状态…");
    resolve(response(session("invite-a", "a")));
    await waitFor(() => expect(screen.queryByText("正在核对登录状态…")).toBeNull());
    expect((screen.getByLabelText("当前工作") as HTMLInputElement).value).toBe("still current");
  });
});
