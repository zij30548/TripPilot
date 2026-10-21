"use client";

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { authSnapshot } from "@/lib/auth-session";
import { requestPreferences, type PreferenceView, type PreferenceCommand } from "@/lib/assistant-preferences";

const button = "min-h-11 rounded-xl border border-[#cbd6c6] px-3 py-2 text-xs disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-offset-2";
type Context = { cid: string; epoch: number };
type View = { context: Context | null; value: PreferenceView | null; error: string | null; pending: boolean; needsRead: boolean; notice: string | null };
type Work = { controller: AbortController; context: Context; version: number; writing: boolean };
const empty: View = { context: null, value: null, error: null, pending: false, needsRead: false, notice: null };
/** Consent is a separate, explicit write. Chat text never silently becomes memory. */
export default function AssistantPreferences({ conversationId }: { conversationId: string }) {
  const [state, setState] = useState<View>(empty), [draft, setDraft] = useState<{ context: Context; value: string } | null>(null);
  const identity = authSnapshot().epoch;
  const context = useMemo<Context>(() => ({ cid: conversationId, epoch: identity }), [conversationId, identity]);
  const version = useRef(0), active = useRef<Work | null>(null), alive = useRef(true), currentContext = useRef(context), currentState = useRef(state);
  const update = useCallback((value: View) => { if (alive.current) { currentState.current = value; setState(value); } }, []);
  useLayoutEffect(() => {
    currentContext.current = context;
    const clock = version, work = active;
    return () => { clock.current++; work.current?.controller.abort(); work.current = null; };
  }, [context]);
  useEffect(() => { alive.current = true; const clock = version, work = active; return () => { alive.current = false; clock.current++; work.current?.controller.abort(); work.current = null; }; }, []);
  const view = state.context === context ? state : empty;
  const data = view.value, items = data?.items ?? null, pending = view.pending;
  const text = draft?.context === context ? draft.value : "";
  const stop = () => {
    if (!alive.current || currentContext.current !== context || currentState.current !== state || !active.current) return;
    const writing = active.current.writing;
    version.current++; active.current.controller.abort(); active.current = null;
    update({ context, value: null, pending: false, error: null, needsRead: true,
      notice: writing ? "已停止等待。偏好保存或删除可能已提交，请重新读取后再操作；不会自动重发。" : "已停止读取，没有修改偏好。可主动重新读取。" });
  };
  const run = async (command?: PreferenceCommand) => {
    if (!alive.current || active.current || currentContext.current !== context || currentState.current !== state || authSnapshot().epoch !== identity || command && (view.needsRead || !data)) return;
    const work: Work = { controller: new AbortController(), context, version: ++version.current, writing: !!command };
    active.current = work; update({ ...view, context, pending: true, error: null, notice: null });
    const current = () => alive.current && active.current === work && work.version === version.current && currentContext.current === context && identity === authSnapshot().epoch && !work.controller.signal.aborted;
    try {
      const result = await requestPreferences(conversationId, work.controller.signal, command);
      if (current()) { update({ context, value: result, pending: false, error: null, needsRead: false, notice: command ? "偏好记录已更新。" : null }); if (command) setDraft({ context, value: "" }); }
    } catch (error) {
      if (current()) update({ context, value: null, pending: false, error: error instanceof Error ? error.message : "偏好操作未完成，请重新读取。", needsRead: true, notice: command ? "结果可能已经提交，请先重新读取；不会自动重发。" : null });
    } finally { if (current()) active.current = null; }
  };
  const current = items?.find(item => item.key === "travel_preferences");
  return <details className="mt-5 rounded-2xl border border-[#dce4dc] p-4" aria-label="用户明确保存的偏好" onToggle={event => { if (!event.currentTarget.open) stop(); }}>
    <summary className="min-h-10 cursor-pointer py-2 text-sm font-medium">我的旅行偏好 · 仅明确同意后记住</summary>
    <p className="mt-2 text-xs leading-6 text-[#73816a]">只记住你主动提交的文字，用于以后需求沟通，不代表排程已满足这些条件。不要填写证件、密码或支付信息。删除会清除偏好正文；不会自动恢复或重新执行旧任务。</p>
    <button type="button" className={`${button} mt-3`} disabled={pending} onClick={() => void run()}>读取已保存偏好</button>
    {pending && <button type="button" className={`${button} ml-2 mt-3`} onClick={stop}>停止偏好请求</button>}
    {pending && <p role="status" className="mt-2 text-xs">正在处理偏好记录…</p>}
    {view.error && <p role="alert" className="mt-2 text-xs text-[#8b6339]">{view.error}</p>}
    {view.notice && <p role="status" className="mt-2 text-xs">{view.notice}</p>}
    {items !== null && <>
      <p className="mt-3 whitespace-pre-wrap break-words text-sm">{current ? current.value : "尚未保存旅行偏好。"}</p>
      <label className="mt-3 block text-xs">希望记住的旅行偏好<textarea value={text} disabled={pending} maxLength={500} rows={2} onChange={e => setDraft({ context, value: e.target.value })} className="mt-2 w-full rounded-xl border border-[#cbd6c6] p-3 text-sm" /></label>
      <div className="mt-3 flex flex-wrap gap-2"><button type="button" className={button} disabled={pending || view.needsRead || !text.trim()} onClick={() => void run({ action: "set", key: "travel_preferences", value: text.trim(), consent: true, expected_version: data?.versions.travel_preferences ?? 0 })}>同意保存为长期偏好</button>
        {current && <button type="button" className={button} disabled={pending} onClick={() => void run({ action: "delete", key: current.key, expected_version: current.version })}>删除这项偏好</button>}</div>
    </>}
  </details>;
}
