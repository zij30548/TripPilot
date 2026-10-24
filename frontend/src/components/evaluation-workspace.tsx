"use client";

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { authServerSnapshot, authSnapshot, subscribeAuth } from "@/lib/auth-session";
import { listChatConversations, readChatConversation, type ChatSummary } from "@/lib/chat";
import { readVersionedAssistantState, type VersionedAssistantState, type AssistantTaskState } from "@/lib/assistant";

type Context = { epoch: number; session: string | null; authenticated: boolean };
type View = {
  context: Context | null;
  items: ChatSummary[];
  nextOffset: number | null;
  listed: boolean;
  selected: ChatSummary | null;
  receipt: VersionedAssistantState | null;
  pending: "list" | "receipt" | null;
  error: string | null;
  notice: string | null;
};
type Work = { context: Context; controller: AbortController; version: number };
const EMPTY: View = { context: null, items: [], nextOffset: null, listed: false, selected: null, receipt: null, pending: null, error: null, notice: null };
const focus = "focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[#294e43]";
const button = `min-h-11 rounded-lg px-4 py-2 text-sm transition disabled:cursor-not-allowed disabled:opacity-40 ${focus}`;
const STATES: Record<AssistantTaskState | "skipped", string> = {
  queued: "等待执行", running: "执行中", waiting: "等待补充", awaiting_confirmation: "等待确认", paused: "已暂停",
  succeeded: "执行完成", failed: "执行失败", cancelled: "已取消", uncertain: "结果待核对", skipped: "未执行",
};
const STOPS: Record<string, string> = {
  completed: "本轮执行结束", complete: "本轮执行结束", succeeded: "本轮执行结束", awaiting_user: "等待用户补充或确认",
  awaiting_confirmation: "等待用户确认", context_changed: "会话已变化，旧任务结果不能直接继续使用", cancelled: "用户已取消",
  model_budget: "已达到模型尝试上限", tool_budget: "已达到工具执行上限", deadline: "已达到任务时间上限",
  no_progress: "没有取得新进展，已停止", read_receipts_only: "结果尚不确定，只能读取回执核对",
};
function updatedAt(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(value));
}
export type TechnicalReceiptCheck = { id: "model_budget" | "tool_budget" | "completion"; label: string; state: "passed" | "failed" | "pending"; detail: string };
/** Deterministic receipt checks only. These cannot establish answer quality. */
export function technicalReceiptChecks(value: VersionedAssistantState): TechnicalReceiptCheck[] {
  const task = value.task;
  if (!value.migration_ready || !task) return [
    { id: "model_budget", label: "模型尝试上限", state: "pending", detail: "没有可核查的任务回执" },
    { id: "tool_budget", label: "工具执行上限", state: "pending", detail: "没有可核查的任务回执" },
    { id: "completion", label: "任务完成状态", state: "pending", detail: "没有可核查的任务回执" },
  ];
  const receipt = "execution_receipt" in task ? task.execution_receipt : undefined;
  const models = receipt?.budget.model_limit ?? (value.schema_version === "assistant-state-v1" ? 3 : null);
  const tools = receipt?.budget.tool_limit ?? (value.schema_version === "assistant-state-v1" ? 4 : null);
  return [
    { id: "model_budget", label: "模型尝试上限", state: models === null ? "pending" : task.model_calls <= models ? "passed" : "failed", detail: models === null ? "旧任务缺少完整预约回执，不能推断上限或发送数" : `${task.model_calls} / ${models} 次${receipt ? "（预算预约）" : ""}` },
    { id: "tool_budget", label: "工具执行上限", state: tools === null ? "pending" : task.tool_calls <= tools ? "passed" : "failed", detail: tools === null ? "旧任务缺少完整预约回执，不能推断上限" : `${task.tool_calls} / ${tools} 次${receipt ? "（预算预约）" : ""}` },
    { id: "completion", label: "任务完成状态", state: task.state === "succeeded" ? "passed" : ["failed", "cancelled"].includes(task.state) ? "failed" : "pending",
      detail: task.state === "succeeded" ? "程序记录为执行完成，非答案质量判定" : `${STATES[task.state]}，不记为成功` },
  ];
}

/** Read-only receipts, not a paid evaluation runner or a semantic quality score. */
export default function EvaluationWorkspace({ active = true }: { active?: boolean }) {
  const auth = useSyncExternalStore(subscribeAuth, authSnapshot, authServerSnapshot);
  const context = useMemo<Context>(() => ({ epoch: auth.epoch, session: auth.session?.session_id ?? null, authenticated: auth.phase === "authenticated" }), [auth.epoch, auth.session?.session_id, auth.phase]);
  const [state, setState] = useState<View>(EMPTY);
  const currentContext = useRef(context), currentState = useRef(state), currentActive = useRef(active), alive = useRef(true), version = useRef(0), work = useRef<Work | null>(null);
  const update = useCallback((value: View) => { if (alive.current) { currentState.current = value; setState(value); } }, []);
  const invalidate = useCallback(() => { version.current++; work.current?.controller.abort(); work.current = null; }, []);
  useLayoutEffect(() => {
    currentContext.current = context;
    return invalidate;
  }, [context, invalidate]);
  useLayoutEffect(() => { currentActive.current = active; }, [active]);
  useEffect(() => { alive.current = true; return () => { alive.current = false; invalidate(); }; }, [invalidate]);
  const view = state.context === context ? state : EMPTY;
  const allowed = (starting = true) => alive.current && (!starting || currentActive.current) && currentContext.current === context && context.authenticated &&
    authSnapshot().phase === "authenticated" && authSnapshot().epoch === context.epoch && authSnapshot().session?.session_id === context.session;
  const begin = (): Work => {
    invalidate();
    const next = { context, controller: new AbortController(), version: ++version.current };
    work.current = next;
    return next;
  };
  const valid = (request: Work) => allowed(false) && work.current === request && version.current === request.version && !request.controller.signal.aborted;
  const load = async (more = false) => {
    if (!allowed() || currentState.current !== state || work.current || more && view.nextOffset === null) return;
    const request = begin(), offset = more ? view.nextOffset! : 0;
    const retained: View = more ? view : { ...EMPTY, context };
    update({ ...retained, context, pending: "list", error: null, notice: null });
    try {
      const result = await listChatConversations(offset, request.controller.signal);
      if (!valid(request)) return;
      const combined = more ? [...retained.items, ...result.items] : result.items;
      if (new Set(combined.map(item => item.id)).size !== combined.length || result.next_offset !== null && result.next_offset <= offset) {
        throw new Error("会话列表在读取期间发生变化，请重新读取；没有合并重复记录。");
      }
      update({ ...retained, context, items: combined, nextOffset: result.next_offset, listed: true, pending: null, error: null, notice: null });
    } catch (error) {
      if (valid(request)) update({ ...retained, context, pending: null, error: error instanceof Error ? error.message : "无法读取会话，请稍后主动重试。", notice: null });
    } finally { if (valid(request)) work.current = null; }
  };
  const select = async (item: ChatSummary) => {
    if (!allowed() || currentState.current !== state || !view.items.some(value => value.id === item.id)) return;
    const request = begin();
    const retained = { ...view, context, selected: item, receipt: null, pending: "receipt" as const, error: null, notice: null };
    update(retained);
    try {
      // A list summary may be older than a newly completed turn. Bind the receipt
      // to a freshly validated conversation revision, never to a timestamp alone.
      const conversation = await readChatConversation(item.id, request.controller.signal);
      if (!valid(request)) return;
      const result = await readVersionedAssistantState(conversation.id, conversation.revision, request.controller.signal);
      if (valid(request)) update({ ...retained, selected: conversation, receipt: result, pending: null });
    } catch (error) {
      if (valid(request)) update({ ...retained, pending: null, receipt: null, error: error instanceof Error ? error.message : "无法读取执行记录，请主动重新读取。" });
    } finally { if (valid(request)) work.current = null; }
  };
  const cancel = () => {
    if (!allowed() || currentState.current !== state || !work.current) return;
    invalidate();
    update({ ...view, pending: null, error: null, notice: "已停止读取。没有重新执行任务。" });
  };
  const task = view.receipt?.task;
  return <section aria-label="Agent 评测工作区" className="mx-auto w-full max-w-[1160px] px-5 py-8 text-[#17211f] sm:px-9 sm:py-12">
    <header className="flex flex-wrap items-end justify-between gap-5 border-b border-[#e4e8e6] pb-7">
      <div><p className="text-xs tracking-[0.16em] text-[#71807a]">OBSERVABILITY</p><h1 className="mt-2 text-2xl font-medium tracking-tight sm:text-3xl">Agent 评测</h1><p className="mt-3 text-sm leading-6 text-[#71807a]">查看真实执行，不把运行状态当作答案分数。</p></div>
      <button type="button" className={`${button} bg-[#294e43] text-white hover:bg-[#203e35]`} disabled={!!view.pending || !context.authenticated} onClick={() => void load()}>读取会话</button>
    </header>
    <div aria-live="polite" className="text-sm leading-6">
      {view.pending && <div className="flex flex-wrap items-center gap-3 py-4"><p role="status">{view.pending === "list" ? "正在读取会话…" : "正在读取最新执行记录…"}</p><button type="button" className={`${button} text-[#294e43] underline underline-offset-4`} onClick={cancel}>取消读取</button></div>}
      {view.error && <p role="alert" className="break-words py-4 text-[#945746]">{view.error}</p>}
      {view.notice && <p role="status" className="py-4 text-[#71807a]">{view.notice}</p>}
    </div>
    <div className="grid min-w-0 gap-8 pt-7 md:grid-cols-[240px_minmax(0,1fr)] md:gap-10">
      <aside aria-label="评测会话" className="min-w-0 md:border-r md:border-[#e4e8e6] md:pr-6">
        <h2 className="mb-4 text-xs font-medium text-[#71807a]">我的会话</h2>
        {!view.listed && !view.items.length && <p className="text-sm leading-7 text-[#71807a]">主动读取后，选择一份会话查看记录。</p>}
        {view.listed && !view.items.length && <p className="text-sm leading-7 text-[#71807a]">还没有会话。先在对话页开始聊天。</p>}
        <ul className="max-h-48 space-y-1 overflow-y-auto md:max-h-none">{view.items.map(item => <li key={item.id}><button type="button" title={item.title} aria-current={view.selected?.id === item.id ? "true" : undefined} onClick={() => void select(item)} className={`min-h-14 w-full rounded-lg px-3 py-3 text-left ${focus} ${view.selected?.id === item.id ? "bg-[#edf2ef] text-[#294e43]" : "hover:bg-[#f7f8fa]"}`}><span className="line-clamp-2 break-words text-sm leading-6">{item.title}</span><span className="mt-1 block text-xs text-[#71807a]">{updatedAt(item.updated_at)}</span></button></li>)}</ul>
        {view.nextOffset !== null && <button type="button" className={`${button} mt-3 text-[#294e43]`} disabled={!!view.pending} onClick={() => void load(true)}>加载更多</button>}
      </aside>
      <div className="min-w-0">
        {!view.selected && <div className="py-12 sm:py-16"><p className="text-lg font-medium">每一次执行，都有迹可循。</p><p className="mt-3 max-w-md text-sm leading-7 text-[#71807a]">这里只读取当前浏览器工作区的任务回执。不会调用模型、重新执行工具或修改旅行数据。</p></div>}
        {view.selected && !view.pending && view.receipt && <>
          <div className="flex flex-wrap items-start justify-between gap-4"><div className="min-w-0"><h2 className="break-words text-lg font-medium">{view.selected.title}</h2><p className="mt-2 text-xs text-[#71807a]">会话版本 {view.receipt.revision} · 最新任务回执</p></div><button type="button" className={`${button} border border-[#d9e1dc] text-[#294e43]`} onClick={() => void select(view.selected!)}>重新读取</button></div>
          {!view.receipt.migration_ready && <p role="status" className="mt-7 text-sm leading-7 text-[#945746]">任务存储尚未准备好，请联系管理员。没有替代或虚构执行记录。</p>}
          {view.receipt.migration_ready && !task && <p className="mt-8 text-sm leading-7 text-[#71807a]">这份会话还没有任务回执。读取不会自动开始任务。</p>}
          {task && <>
            <dl className="mt-7 grid grid-cols-3 gap-x-3 border-y border-[#e4e8e6] py-5"><div><dt className="text-xs text-[#71807a]">执行状态</dt><dd className="mt-2 break-words text-sm font-medium text-[#294e43]">{STATES[task.state]}</dd></div><div><dt className="text-xs text-[#71807a]">模型尝试</dt><dd className="mt-2 text-lg tabular-nums">{task.model_calls}<span className="ml-1 text-xs text-[#71807a]">次</span></dd></div><div><dt className="text-xs text-[#71807a]">工具执行</dt><dd className="mt-2 text-lg tabular-nums">{task.tool_calls}<span className="ml-1 text-xs text-[#71807a]">次</span></dd></div></dl>
            {"execution_receipt" in task && <p className="mt-3 text-xs leading-6 text-[#71807a]">{task.execution_receipt?.dispatch.status === "observed"
              ? `实际发送 ${task.execution_receipt.dispatch.sent} 次；收到 ${task.execution_receipt.dispatch.received}、失败 ${task.execution_receipt.dispatch.failed}、取消 ${task.execution_receipt.dispatch.cancelled}、结果不明 ${task.execution_receipt.dispatch.uncertain}。不是人民币费用。`
              : "实际发送次数未知；预算预约和工具提议不等于已发请求或浏览器执行完成。"}
              {task.execution_receipt && !task.execution_receipt.trace_complete ? " 分步记录不完整，不能据此补造全部执行过程。" : ""}</p>}
            <section aria-label="本轮技术核查" className="mt-7">
              <h3 className="text-xs font-medium text-[#71807a]">技术核查</h3>
              <ul className="mt-3">{technicalReceiptChecks(view.receipt).map(check => <li key={check.id} className="border-b border-[#edf0ee] py-3 text-sm leading-6"><div className="flex flex-wrap justify-between gap-x-4"><span>{check.label}</span><span className={check.state === "failed" ? "text-[#945746]" : check.state === "passed" ? "text-[#294e43]" : "text-[#71807a]"}>{check.state === "passed" ? "通过" : check.state === "failed" ? "未通过" : "待核"}</span></div><p className="mt-1 text-xs text-[#71807a]">{check.detail}</p></li>)}</ul>
              <p className="mt-3 text-xs leading-6 text-[#71807a]">预算尝试与上限核查，不是供应商计费次数；语义正确性、事实支持和需求覆盖未评。</p>
            </section>
            <h3 className="mb-3 mt-7 text-xs font-medium text-[#71807a]">执行步骤</h3>
            {!task.steps.length && <p className="text-sm text-[#71807a]">本次回执没有分步记录。</p>}
            <ol>{task.steps.map((step, index) => <li key={`${index}-${step.name}`} className="flex flex-wrap justify-between gap-x-4 gap-y-1 border-b border-[#edf0ee] py-4 text-sm leading-6"><span className="min-w-0 break-words"><span className="mr-3 text-xs tabular-nums text-[#9aa49e]">{String(index + 1).padStart(2, "0")}</span>{step.name}</span><span className="shrink-0 text-xs text-[#71807a]">{STATES[step.state]} · {step.elapsed_ms === 0 ? "耗时未记录" : `${(step.elapsed_ms / 1000).toFixed(2)} 秒`}</span></li>)}</ol>
            {task.stop_reason && <p className="mt-5 break-words text-sm leading-7 text-[#71807a]">停止原因：{STOPS[task.stop_reason] ?? task.stop_reason}</p>}
            <details className="mt-5 text-xs text-[#71807a]"><summary className={`cursor-pointer py-2 ${focus}`}>记录标识</summary><p className="mt-2 break-all font-mono">{task.id}</p><p className="mt-2 leading-6">步骤耗时来自服务端回执；未记录时不宣称为实测零秒，也不代表整次对话的端到端延迟。不展示模型内部思考。</p></details>
          </>}
        </>}
      </div>
    </div>
    <footer className="mt-12 border-t border-[#e4e8e6] pt-5 text-xs leading-6 text-[#71807a]">执行成功不等于语义评测通过。答案相关性、事实支持和需求覆盖仍须独立评审；自动批量付费评测尚未开放。</footer>
  </section>;
}
