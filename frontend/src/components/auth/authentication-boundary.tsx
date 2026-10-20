"use client";

import { useEffect, useRef, useState, useSyncExternalStore, type FormEvent, type ReactNode } from "react";
import { authServerSnapshot, authSnapshot, checkSession, login, logout, registerAccount, startAuthLifecycle, subscribeAuth } from "@/lib/auth-session";
import { BusinessUsageControls } from "./business-usage-controls";
import { PasswordInput } from "./password-input";

export function AuthenticationBoundary({ children }: { children: ReactNode }) {
  const auth = useSyncExternalStore(subscribeAuth, authSnapshot, authServerSnapshot);
  useEffect(startAuthLifecycle, []);

  if (auth.phase === "authenticated" && auth.session?.user) return (
    <>
      <header className="border-b border-slate-200 bg-white px-4 py-3 text-sm">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3">
          <span>邀请内测 · 当前账号：{auth.session.user.username}</span>
          <button type="button" className="rounded-lg border px-3 py-2" onClick={() => void logout(auth.epoch)}>退出登录</button>
        </div>
      </header>
      <BusinessUsageControls key={`usage-${auth.epoch}`} epoch={auth.epoch} disabled={auth.verifying} />
      {auth.verifying && <p role="status" className="p-4 text-center">正在核对登录状态…</p>}
      {/* Same identity checks hide, but do not unmount, ongoing work. */}
      <div key={auth.epoch} hidden={auth.verifying} inert={auth.verifying}>{children}</div>
    </>
  );

  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-5 py-10">
      <h1 className="text-2xl font-semibold">TripPilot 邀请内测</h1>
      <p className="mt-3 text-sm text-slate-600">已有账号可直接登录；持管理员邀请码可自助注册。仅限受邀内测，账号之间的行程存档相互隔离。</p>
      {auth.message && <p role="alert" className="mt-4 rounded-xl bg-amber-50 p-3 text-sm text-amber-900">{auth.message}</p>}
      {auth.phase === "checking" || auth.verifying ? <p role="status" className="mt-6">正在核对登录状态…</p> :
        auth.phase === "error" || !auth.session ? <button type="button" className="mt-6 rounded-xl bg-slate-900 px-4 py-3 text-white" onClick={() => void checkSession()}>重新检查登录状态</button> : (
          <InvitationAccessForm key={auth.epoch} epoch={auth.epoch} />
        )}
      <p className="mt-6 text-xs text-slate-500">退出、切换账号或登录过期会清除本页临时状态和未发送内容；已持久保存的对话及行程仍归原账号。旧工作台没有自动保存。</p>
    </main>
  );
}

function InvitationAccessForm({ epoch }: { epoch: number }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [notice, setNotice] = useState<string | null>(null);
  return <>
    {notice && <p role="status" className="mt-4 rounded-xl bg-emerald-50 p-3 text-sm text-emerald-900">{notice}</p>}
    {mode === "login" ? <>
      <InvitationLoginForm epoch={epoch} />
      <button type="button" className="mt-4 text-sm underline" onClick={() => { setNotice(null); setMode("register"); }}>使用邀请码注册</button>
    </> : <InvitationRegistrationForm epoch={epoch}
      onCancel={() => { setNotice(null); setMode("login"); }}
      onSuccess={() => { setNotice("注册成功，请使用新账号和密码登录；不会自动登录。"); setMode("login"); }} />}
  </>;
}

function InvitationLoginForm({ epoch }: { epoch: number }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const secret = password;
    setPassword("");
    // Login changes the identity phase synchronously; stale form callbacks also
    // carry their old epoch, so neither can issue a second or cross-user login.
    void login(username.trim(), secret, epoch);
  }
  return <form className="mt-6 grid gap-4" onSubmit={submit}>
    <label className="grid gap-2 text-sm">账号
      <input autoComplete="username" name="username" value={username} maxLength={80} required className="rounded-xl border p-3" onChange={event => setUsername(event.target.value)} />
    </label>
    <PasswordInput label="密码" name="password" value={password} onChange={setPassword} autoComplete="current-password" />
    <button disabled={!username.trim() || !password} className="rounded-xl bg-slate-900 px-4 py-3 text-white disabled:opacity-50">登录</button>
    <button type="button" className="text-sm underline" onClick={() => void checkSession()}>重新检查登录状态</button>
  </form>;
}

function InvitationRegistrationForm({ epoch, onCancel, onSuccess }: { epoch: number; onCancel: () => void; onSuccess: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [invitationCode, setInvitationCode] = useState("");
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const operation = useRef<{ version: number; controller: AbortController } | null>(null);
  const version = useRef(0);
  const [sensitiveKey, setSensitiveKey] = useState(0);
  useEffect(() => () => { version.current += 1; operation.current?.controller.abort(); operation.current = null; }, []);

  function clearSensitive() {
    setPassword(""); setConfirmation(""); setInvitationCode(""); setSensitiveKey(current => current + 1);
  }
  function cancel() {
    version.current += 1;
    operation.current?.controller.abort(); operation.current = null;
    clearSensitive(); setPending(false); onCancel();
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (operation.current) return;
    const currentAuth = authSnapshot();
    if (currentAuth.epoch !== epoch || currentAuth.phase !== "anonymous" || currentAuth.verifying) return;
    const passwordLength = Array.from(password).length;
    if (!/^[a-z0-9_.-]{3,48}$/.test(username) || passwordLength < 12 || passwordLength > 128 || !/^[A-Za-z0-9_-]{43}$/.test(invitationCode)) {
      setMessage("请检查账号格式、12～128字密码及完整邀请码。"); return;
    }
    if (password !== confirmation) { setMessage("两次输入的密码不一致，请重新检查。"); return; }
    const requestVersion = ++version.current;
    const controller = new AbortController();
    operation.current = { version: requestVersion, controller };
    setPending(true); setMessage(null);
    const secret = password; const code = invitationCode;
    clearSensitive();
    try {
      await registerAccount(username, secret, code, { signal: controller.signal, expectedEpoch: epoch });
      if (version.current !== requestVersion || controller.signal.aborted || authSnapshot().epoch !== epoch) return;
      onSuccess();
    } catch (error) {
      if (version.current !== requestVersion || controller.signal.aborted || authSnapshot().epoch !== epoch) return;
      setMessage(error instanceof Error && error.name !== "AbortError" ? error.message : "注册状态已变化，请重新检查登录状态。");
    } finally {
      if (version.current === requestVersion) { operation.current = null; setPending(false); }
    }
  }
  return <form className="mt-6 grid gap-4" onSubmit={submit}>
    <h2 className="text-lg font-medium">邀请码注册</h2>
    <p className="text-sm text-slate-600">账号为3～48位小写字母、数字、下划线、点或短横线；密码12～128字。邀请码由管理员提供，仅能使用一次。</p>
    {message && <p role="alert" className="rounded-xl bg-amber-50 p-3 text-sm text-amber-900">{message}</p>}
    <label className="grid gap-2 text-sm">注册账号
      <input name="username" autoComplete="username" value={username} onChange={event => setUsername(event.target.value)} autoCapitalize="none" spellCheck={false}
        minLength={3} maxLength={48} pattern="[a-z0-9_.\-]{3,48}" required disabled={pending} className="min-w-0 rounded-xl border p-3" />
    </label>
    <PasswordInput key={`password-${sensitiveKey}`} label="注册密码" name="password" value={password} onChange={setPassword} autoComplete="new-password" minLength={12} maxLength={256} disabled={pending} />
    <PasswordInput key={`confirmation-${sensitiveKey}`} label="确认密码" name="password-confirmation" value={confirmation} onChange={setConfirmation} autoComplete="new-password" minLength={12} maxLength={256} disabled={pending} />
    <label className="grid gap-2 text-sm">邀请码
      <input name="invitation_code" autoComplete="off" value={invitationCode} onChange={event => setInvitationCode(event.target.value)}
        minLength={43} maxLength={43} required disabled={pending} className="min-w-0 rounded-xl border p-3" spellCheck={false} autoCapitalize="none" />
    </label>
    <button type="submit" disabled={pending || !username || !password || !confirmation || !invitationCode} className="rounded-xl bg-slate-900 px-4 py-3 text-white disabled:opacity-50">{pending ? "正在注册…" : "注册账号"}</button>
    {pending && <p role="status" className="text-sm text-slate-600">只提交一次。取消等待不保证撤销已创建的账号；结果不确定时请先尝试登录。</p>}
    <button type="button" className="text-sm underline" onClick={cancel}>{pending ? "取消等待，返回登录" : "取消注册，返回登录"}</button>
  </form>;
}
