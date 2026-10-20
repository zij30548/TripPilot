import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { AuthenticationBoundary } from "./authentication-boundary";

const csrf = "c".repeat(43);
const anonymous = { authenticated: false, user: null, csrf_token: csrf, expires_at: null, session_id: null };
const session = (username: string, identity: string) => ({ authenticated: true, user: { id: username, username }, csrf_token: csrf, expires_at: new Date(Date.now() + 3600_000).toISOString(), session_id: identity.repeat(32) });
const response = (body: unknown) => ({ ok: true, status: 200, json: async () => body });
const deferred = <T,>() => { let resolve!: (value: T) => void; let reject!: (error: Error) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const code = "i".repeat(43);
const fillRegistration = (password = "controlled-password") => {
  fireEvent.change(screen.getByLabelText("注册账号"), { target: { value: "new-invite" } });
  fireEvent.change(screen.getByLabelText("注册密码"), { target: { value: password } });
  fireEvent.change(screen.getByLabelText("确认密码"), { target: { value: password } });
  fireEvent.change(screen.getByLabelText("邀请码"), { target: { value: code } });
};

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

  it("toggles only the current password's visibility, never submits or sends requests, and clears on mode change", async () => {
    vi.stubGlobal("BroadcastChannel", undefined);
    const fetcher = vi.fn().mockResolvedValue(response(anonymous));
    vi.stubGlobal("fetch", fetcher);
    render(<AuthenticationBoundary><Work /></AuthenticationBoundary>);
    await screen.findByRole("button", { name: /^登录$/ });
    const password = screen.getByLabelText("密码") as HTMLInputElement;
    fireEvent.change(password, { target: { value: "typed-secret" } });
    expect(password.type).toBe("password"); expect(password.autocomplete).toBe("current-password");
    fireEvent.click(screen.getByRole("button", { name: "显示密码" }));
    expect(password.type).toBe("text"); expect(password.value).toBe("typed-secret");
    expect(screen.getByRole("button", { name: "隐藏密码" }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "隐藏密码" }));
    expect(password.type).toBe("password"); expect(fetcher).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "使用邀请码注册" }));
    expect((screen.getByLabelText("注册密码") as HTMLInputElement).autocomplete).toBe("new-password");
    fireEvent.click(screen.getByRole("button", { name: "取消注册，返回登录" }));
    expect((screen.getByLabelText("密码") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("密码") as HTMLInputElement).type).toBe("password");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("registers once using pre-login CSRF without confirmation in the body, then requires an explicit login", async () => {
    vi.stubGlobal("BroadcastChannel", undefined);
    const pending = deferred<ReturnType<typeof response>>();
    const fetcher = vi.fn().mockResolvedValueOnce(response(anonymous)).mockReturnValueOnce(pending.promise);
    vi.stubGlobal("fetch", fetcher);
    render(<AuthenticationBoundary><Work /></AuthenticationBoundary>);
    fireEvent.click(await screen.findByRole("button", { name: "使用邀请码注册" }));
    fillRegistration();
    fireEvent.click(screen.getByRole("button", { name: "显示注册密码" }));
    fireEvent.click(screen.getByRole("button", { name: "显示确认密码" }));
    fireEvent.submit(screen.getByRole("button", { name: "注册账号" }).closest("form")!);
    fireEvent.submit(screen.getByRole("button", { name: "正在注册…" }).closest("form")!);
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(fetcher.mock.calls[1][0]).toBe("/api/auth/register");
    expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ username: "new-invite", password: "controlled-password", invitation_code: code });
    expect(new Headers(fetcher.mock.calls[1][1].headers).get("X-CSRF-Token")).toBe(csrf);
    for (const label of ["注册密码", "确认密码", "邀请码"]) expect((screen.getByLabelText(label) as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("注册密码") as HTMLInputElement).type).toBe("password");
    pending.resolve(response({ registered: true }));
    await screen.findByText("注册成功，请使用新账号和密码登录；不会自动登录。");
    expect(screen.queryByLabelText("当前工作")).toBeNull(); expect(fetcher).toHaveBeenCalledTimes(2);
    expect((screen.getByLabelText("密码") as HTMLInputElement).value).toBe("");
  });

  it("rejects mismatched confirmation and invalid account/code locally, without claiming registration succeeded", async () => {
    vi.stubGlobal("BroadcastChannel", undefined);
    const fetcher = vi.fn().mockResolvedValue(response(anonymous));
    vi.stubGlobal("fetch", fetcher);
    render(<AuthenticationBoundary><Work /></AuthenticationBoundary>);
    fireEvent.click(await screen.findByRole("button", { name: "使用邀请码注册" }));
    fillRegistration();
    fireEvent.change(screen.getByLabelText("确认密码"), { target: { value: "different-password" } });
    fireEvent.submit(screen.getByRole("button", { name: "注册账号" }).closest("form")!);
    expect(screen.getByRole("alert").textContent).toContain("不一致");
    fireEvent.change(screen.getByLabelText("注册账号"), { target: { value: "MixedCase" } });
    fireEvent.submit(screen.getByRole("button", { name: "注册账号" }).closest("form")!);
    expect(screen.getByRole("alert").textContent).toContain("格式");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("cancel + reopen isolates late old success and finally from a new registration", async () => {
    vi.stubGlobal("BroadcastChannel", undefined);
    const old = deferred<ReturnType<typeof response>>(); const current = deferred<ReturnType<typeof response>>();
    const fetcher = vi.fn().mockResolvedValueOnce(response(anonymous)).mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise);
    vi.stubGlobal("fetch", fetcher);
    render(<AuthenticationBoundary><Work /></AuthenticationBoundary>);
    fireEvent.click(await screen.findByRole("button", { name: "使用邀请码注册" })); fillRegistration();
    fireEvent.click(screen.getByRole("button", { name: "注册账号" }));
    fireEvent.click(screen.getByRole("button", { name: "取消等待，返回登录" }));
    expect(fetcher.mock.calls[1][1].signal.aborted).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "使用邀请码注册" }));
    for (const label of ["注册密码", "确认密码", "邀请码"]) expect((screen.getByLabelText(label) as HTMLInputElement).value).toBe("");
    fillRegistration("next-controlled-password"); fireEvent.click(screen.getByRole("button", { name: "注册账号" }));
    await act(async () => { old.resolve(response({ registered: true })); });
    expect(screen.queryByText(/注册成功，请使用/)).toBeNull();
    expect(screen.getByRole("button", { name: "正在注册…" }).hasAttribute("disabled")).toBe(true);
    current.resolve(response({ registered: true })); await screen.findByText(/注册成功，请使用/);
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("cancel + reopen discards an old failure and never leaks its message into the new form", async () => {
    vi.stubGlobal("BroadcastChannel", undefined);
    const old = deferred<ReturnType<typeof response>>();
    const fetcher = vi.fn().mockResolvedValueOnce(response(anonymous)).mockReturnValueOnce(old.promise);
    vi.stubGlobal("fetch", fetcher);
    render(<AuthenticationBoundary><Work /></AuthenticationBoundary>);
    fireEvent.click(await screen.findByRole("button", { name: "使用邀请码注册" })); fillRegistration();
    fireEvent.click(screen.getByRole("button", { name: "注册账号" }));
    fireEvent.click(screen.getByRole("button", { name: "取消等待，返回登录" }));
    fireEvent.click(screen.getByRole("button", { name: "使用邀请码注册" }));
    await act(async () => { old.reject(new TypeError("private response must not appear")); });
    expect(screen.queryByRole("alert")).toBeNull(); expect(screen.queryByText(/private response/)).toBeNull();
    expect(screen.getByRole("heading", { name: "邀请码注册" })).toBeTruthy();
  });
});
