"use client";

import { useEffect, useState, useSyncExternalStore, type FormEvent, type ReactNode } from "react";
import { authServerSnapshot, authSnapshot, checkSession, login, logout, startAuthLifecycle, subscribeAuth } from "@/lib/auth-session";
import { BusinessUsageControls } from "./business-usage-controls";

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
      <p className="mt-3 text-sm text-slate-600">请使用管理员提供的邀请账号登录。暂不开放注册；账号之间的行程存档相互隔离。</p>
      {auth.message && <p role="alert" className="mt-4 rounded-xl bg-amber-50 p-3 text-sm text-amber-900">{auth.message}</p>}
      {auth.phase === "checking" || auth.verifying ? <p role="status" className="mt-6">正在核对登录状态…</p> :
        auth.phase === "error" || !auth.session ? <button type="button" className="mt-6 rounded-xl bg-slate-900 px-4 py-3 text-white" onClick={() => void checkSession()}>重新检查登录状态</button> : (
          <InvitationLoginForm key={auth.epoch} epoch={auth.epoch} />
        )}
      <p className="mt-6 text-xs text-slate-500">退出、切换账号或登录过期会清除本页未保存的工作；没有自动保存。</p>
    </main>
  );
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
    <label className="grid gap-2 text-sm">密码
      <input type="password" autoComplete="current-password" name="password" value={password} maxLength={256} required className="rounded-xl border p-3" onChange={event => setPassword(event.target.value)} />
    </label>
    <button disabled={!username.trim() || !password} className="rounded-xl bg-slate-900 px-4 py-3 text-white disabled:opacity-50">登录</button>
    <button type="button" className="text-sm underline" onClick={() => void checkSession()}>重新检查登录状态</button>
  </form>;
}
