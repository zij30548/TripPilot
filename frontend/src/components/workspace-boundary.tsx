"use client";

import { useEffect, useSyncExternalStore, type ReactNode } from "react";
import { authServerSnapshot, authSnapshot, checkSession, startAuthLifecycle, subscribeAuth } from "@/lib/auth-session";

/** Establish an isolated browser workspace; never asks for account credentials. */
export function WorkspaceBoundary({ children }: { children: ReactNode }) {
  const workspace = useSyncExternalStore(subscribeAuth, authSnapshot, authServerSnapshot);
  useEffect(startAuthLifecycle, []);

  if (workspace.phase === "authenticated" && workspace.session?.user) return <>
    {workspace.verifying && <p role="status" className="p-3 text-center text-xs text-[#71807a]">正在连接工作区…</p>}
    {/* Rechecking the same cookie retains controllers and frozen operation IDs. */}
    <div key={workspace.epoch} hidden={workspace.verifying} inert={workspace.verifying}>{children}</div>
  </>;

  return <main className="mx-auto flex min-h-dvh max-w-md flex-col justify-center px-6 py-10 text-[#17211f]">
    <h1 className="text-2xl font-medium tracking-tight">TripPilot</h1>
    {workspace.phase === "checking" || workspace.verifying ?
      <p role="status" className="mt-4 text-sm text-[#71807a]">正在连接工作区…</p> : <>
        <p role="alert" className="mt-4 text-sm leading-7 text-[#945746]">{workspace.message ?? "工作区连接已失效，请重新连接。"}</p>
        <button type="button" className="mt-6 min-h-11 rounded-lg bg-[#294e43] px-4 py-3 text-sm text-white focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[#294e43]" onClick={() => void checkSession()}>重新连接工作区</button>
      </>}
  </main>;
}
