"use client";

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import Link from "next/link";
import { chatCity } from "@/lib/chat-cities";
import ChatPlaces from "./chat-places";
import { canConfirmRequirements, missingRequirements, useChatWorkspace } from "@/lib/chat-controller";
import type { ChatConversation, ChatIssue } from "@/lib/chat";
import { sendAssistantTurn, type AssistantState, type AssistantTaskState } from "@/lib/assistant";
import { useAssistantState } from "@/lib/assistant-controller";
import AssistantPlanning from "./assistant-planning";
import AssistantPreferences from "./assistant-preferences";
import type { PlacesWorkspace } from "@/lib/chat-places";
import type { PlacesPlanningAvailability } from "@/lib/chat-places-controller";

function Arrow({ className = "size-5" }: { className?: string }) {
  return <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d="M12 19V5m-6 6 6-6 6 6" strokeLinecap="round" strokeLinejoin="round" /></svg>;
}
function Plus() {
  return <svg className="size-4 shrink-0" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="M10 4v12M4 10h12" strokeLinecap="round" /></svg>;
}
function Spark() {
  return <svg className="size-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m12 3 2.2 6.8L21 12l-6.8 2.2L12 21l-2.2-6.8L3 12l6.8-2.2L12 3Z" strokeLinejoin="round" /></svg>;
}
const focus = "focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[#376d5b]";
const TASK_STATES: Record<AssistantTaskState | "skipped", string> = {
  queued: "等待执行", running: "执行中", waiting: "等待确认", awaiting_confirmation: "等待确认", paused: "已暂停",
  succeeded: "已完成", failed: "未完成", cancelled: "已取消", uncertain: "结果待核对", skipped: "未执行",
};
function ExecutionSummary({ value }: { value: AssistantState }) {
  return <section className="mt-3 rounded-xl border border-[#dce4dc] bg-[#f4f7f2] p-4 text-xs leading-6" aria-label="本轮执行记录">
    {!value.migration_ready && <p role="status">旅行助手存储尚未准备好，请管理员完成独立迁移；不会用固定回复代替真实执行。</p>}
    {value.task ? <>
      <p className="font-medium">{TASK_STATES[value.task.state]} · 模型调用{value.task.model_calls}次 · 工具执行{value.task.tool_calls}次</p>
      <ol className="mt-2 space-y-2">{value.task.steps.map((step, index) => <li key={`${index}-${step.name}`} className="flex flex-wrap justify-between gap-x-3 border-b border-[#e0e6db] py-1 last:border-0">
        <span className="min-w-0 break-words">{index + 1}. {step.name}</span><span>{TASK_STATES[step.state]} · {(step.elapsed_ms / 1000).toFixed(1)}秒</span>
      </li>)}</ol>
      {value.task.stop_reason && !["complete", "completed", "succeeded"].includes(value.task.stop_reason) && <p className="mt-3">本轮已经停止。请结合上方回复核对结果；继续需要你主动操作，不会在后台自动重试。</p>}
    </> : <p>这份会话还没有可展示的任务记录。</p>}
    {!!value.skills.length && <details className="mt-3"><summary className={`cursor-pointer py-1 ${focus}`}>可用旅行能力</summary><ul>{value.skills.map(skill => <li key={skill.id}>{skill.name} · {skill.version}</li>)}</ul></details>}
    <p className="mt-3 text-[#73816a]">这里只展示执行步骤与结果状态，不展示模型内部思考。读取记录不会重新执行任务。</p>
  </section>;
}
const ISSUE_LABELS: Record<ChatIssue, string> = {
  ambiguous: "仍有含糊或相互矛盾的内容，请补充说明。",
  unsupported_scope: "已保留你的需求。地点准备一次确认一城；跨城、更多住宿或超范围天数还需要澄清，不能当成已执行，也不会偷偷只处理其中一城。",
  unsupported_action: "这项要求超出当前可执行范围。已保留你的表达，请核对助手给出的说明。",
  unimplemented_preference: "偏好仅作为你本次表达的意向记录，尚未用于排程，也不是长期记忆。",
};
function Value({ children }: { children?: ReactNode }) {
  return <span className={children ? "text-[#263e34]" : "text-[#7c827b]"}>{children || "待补充"}</span>;
}

export function RequirementCard({ conversation, pending, needsRefresh, onConfirm }: {
  conversation: ChatConversation; pending: boolean; needsRefresh: boolean; onConfirm: () => void;
}) {
  const r = conversation.requirements, missing = missingRequirements(conversation);
  const confirmed = conversation.confirmed_revision === conversation.revision;
  return <section aria-label="旅行需求意向卡" className="mt-8 rounded-[22px] border border-[#dce4dc] bg-[#f4f7f2] p-5 sm:p-6">
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div>
        <p className="text-[11px] font-semibold tracking-[0.16em] text-[#5c7466]">YOUR TRIP, TAKING SHAPE</p>
        <h2 className="mt-1.5 text-lg font-semibold tracking-tight">这次旅行的需求</h2>
      </div>
      <span className="rounded-full border border-[#cddbcf] px-2.5 py-1 text-xs text-[#46604f]">{confirmed ? "已由你确认" : "待你核对"} · v{conversation.revision}</span>
    </div>
    <dl className="mt-5 grid gap-x-6 gap-y-4 text-sm sm:grid-cols-2">
      <div><dt className="mb-1 text-xs text-[#727d71]">目的地</dt><dd><Value>{chatCity(r.destination_id)?.name}</Value></dd></div>
      <div><dt className="mb-1 text-xs text-[#727d71]">日期 · 含首尾</dt><dd><Value>{r.start_date || r.end_date ? `${r.start_date ?? "待补充"} → ${r.end_date ?? "待补充"}` : null}</Value></dd></div>
      <div><dt className="mb-1 text-xs text-[#727d71]">同行人数</dt><dd><Value>{r.travelers !== null && `${r.travelers}人`}</Value></dd></div>
      <div><dt className="mb-1 text-xs text-[#727d71]">全程总预算 · 全部旅客</dt><dd><Value>{r.budget_cny !== null && `人民币 ¥${r.budget_cny}`}</Value></dd></div>
      <div className="sm:col-span-2"><dt className="mb-1 text-xs text-[#727d71]">住宿参考名称 · 尚未核实POI</dt><dd className="break-words"><Value>{r.accommodation_hint}</Value></dd></div>
      <div className="sm:col-span-2"><dt className="mb-1 text-xs text-[#727d71]">想去的地点 · 顺序意向，尚未核实POI</dt><dd className="break-words"><Value>{r.required_place_hints.length ? r.required_place_hints.join(" → ") : "尚未提出，非必填"}</Value></dd></div>
      <div><dt className="mb-1 text-xs text-[#727d71]">交通意向</dt><dd><Value>{r.transport_mode === "walking" ? "步行参考" : r.transport_mode === "transit" ? "公交／地铁参考" : null}</Value></dd></div>
      <div><dt className="mb-1 text-xs text-[#727d71]">其他偏好 · 仅记录意向</dt><dd className="break-words"><Value>{r.preferences.length ? r.preferences.join("；") : "尚未提出，非必填"}</Value></dd></div>
    </dl>
    {!!conversation.issues.length && <ul className="mt-5 space-y-1.5 text-xs leading-5 text-[#69735e]">{conversation.issues.map((issue, index) => <li key={`${issue}-${index}`}>{ISSUE_LABELS[issue]}</li>)}</ul>}
    {missing.length > 0 && <p className="mt-5 text-xs leading-5 text-[#6a7565]">还需补充：{missing.join("、")}。可以在下方继续告诉我。</p>}
    <div className="mt-5 border-t border-[#dbe3d8] pt-4">
      <p className="text-xs leading-5 text-[#6d7869]">这是对你表达的整理，不是已生成的行程。地点仍需后续真实搜索与确认；确认不会查询交通、保存行程或自动执行。</p>
      <button type="button" disabled={pending || needsRefresh || !canConfirmRequirements(conversation)} onClick={onConfirm}
        className={`mt-4 min-h-11 w-full rounded-xl border border-[#325b46] bg-[#325b46] px-4 py-2.5 text-sm font-medium text-white transition hover:bg-[#264a38] disabled:cursor-not-allowed disabled:border-[#d5ded2] disabled:bg-[#d5ded2] disabled:text-[#7a8775] sm:w-auto ${focus}`}>
        {confirmed ? "这一版需求已确认" : "核对无误，确认需求"}
      </button>
      {confirmed && <p className="mt-2 text-xs leading-5 text-[#55704f]">可以继续发消息修正；新提议需要重新核对确认。</p>}
    </div>
    {!!conversation.sources.length && <details className="mt-4 text-xs text-[#6c7967]">
      <summary className={`min-h-8 cursor-pointer py-1 ${focus}`}>查看本轮修改依据</summary>
      <ul className="mt-2 space-y-2 border-l-2 border-[#d5e0d1] pl-3">{conversation.sources.map((source, index) => <li key={`${source.field}-${index}`} className="whitespace-pre-wrap break-words">“{source.quote}”</li>)}</ul>
    </details>}
  </section>;
}

export default function ChatWorkspace() {
  const chat = useChatWorkspace(sendAssistantTurn), { state } = chat;
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [deletePrompt, setDeletePrompt] = useState<string | null>(null);
  const [placesOpen, setPlacesOpen] = useState(false);
  const [requirementsOpen, setRequirementsOpen] = useState(false);
  const [executionOpen, setExecutionOpen] = useState(false);
  const [sharedPlaces, setSharedPlaces] = useState<PlacesWorkspace | null>(null);
  const [placesAvailability, setPlacesAvailability] = useState<PlacesPlanningAvailability | null>(null);
  const placesAvailabilityRef = useRef<PlacesPlanningAvailability | null>(null);
  const composer = useRef<HTMLTextAreaElement>(null), end = useRef<HTMLDivElement>(null);
  const current = state.conversation;
  const placesContext = useMemo(() => ({ id: current?.id ?? null }), [current?.id]);
  const placesContextRef = useRef<typeof placesContext | null>(placesContext);
  useLayoutEffect(() => { placesContextRef.current = placesContext; return () => { placesContextRef.current = null; }; }, [placesContext]);
  const acceptPlaces = useCallback((value: PlacesPlanningAvailability) => {
    if (placesContextRef.current !== placesContext || value.conversationId !== placesContext.id) return;
    placesAvailabilityRef.current = value; setPlacesAvailability(value);
    if (value.workspace) setSharedPlaces(value.workspace);
  }, [placesContext]);
  const canUsePlaces = useCallback(() => placesAvailabilityRef.current?.conversationId === placesContextRef.current?.id &&
    placesAvailabilityRef.current?.canUse() === true, []);
  const busy = !!state.pending;
  const running = current?.last_turn?.state === "running";
  const sendBlocked = busy || running || state.needsRefresh || !state.draft.trim();
  const textLength = Array.from(state.draft).length;
  const execution = useAssistantState(current, busy || state.needsRefresh);
  const { reload: reloadExecution } = execution;
  const sent = useRef(false);
  useEffect(() => {
    if (state.pending === "sending") sent.current = true;
    else if (sent.current) {
      sent.current = false;
      if (current?.last_turn?.state === "succeeded") void reloadExecution();
    }
  }, [state.pending, current?.id, current?.revision, current?.last_turn?.state, reloadExecution]);
  useEffect(() => { end.current?.scrollIntoView({ block: "end", behavior: "smooth" }); }, [current?.id, current?.messages.length, state.pendingText]);
  function choose(id: string) { placesAvailabilityRef.current = null; setPlacesAvailability(null); setSidebarOpen(false); setDeletePrompt(null); setPlacesOpen(false); setSharedPlaces(null); setRequirementsOpen(false); setExecutionOpen(false); void chat.selectConversation(id); }
  function start() { placesAvailabilityRef.current = null; setPlacesAvailability(null); setSidebarOpen(false); setDeletePrompt(null); setPlacesOpen(false); setSharedPlaces(null); setRequirementsOpen(false); setExecutionOpen(false); chat.newConversation(); composer.current?.focus(); }
  function keyboard(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229) {
      event.preventDefault();
      if (!sendBlocked) event.currentTarget.form?.requestSubmit();
    }
  }
  return <main className="min-h-[calc(100dvh-100px)] bg-[#faf9f5] text-[#24372d]">
    <div className="mx-auto flex min-h-[calc(100dvh-100px)] max-w-[1600px]">
      <aside aria-label="我的旅行对话" className={`${sidebarOpen ? "fixed inset-x-0 top-0 z-30 flex h-dvh" : "hidden"} w-full shrink-0 flex-col border-r border-[#e4e7df] bg-[#eeefe8] px-5 py-7 md:static md:flex md:h-auto md:w-[252px] lg:w-[272px]`}>
        <div className="mb-8 flex items-center justify-between">
          <Link href="/" prefetch={false} className={`inline-flex items-center gap-2 text-xl font-semibold tracking-[-0.05em] ${focus}`} aria-label="TripPilot 聊天首页"><span className="grid size-8 place-items-center rounded-full border border-[#a4b5a1] text-[#3a6049]"><Spark /></span>TripPilot<span className="ml-0.5 mt-0.5 size-1.5 rounded-full bg-[#a8b99a]" /></Link>
          <button type="button" className={`min-h-11 rounded-lg px-3 text-sm md:hidden ${focus}`} onClick={() => setSidebarOpen(false)}>关闭</button>
        </div>
        <button type="button" className={`flex min-h-11 items-center justify-center gap-2 rounded-xl border border-[#cbd6c6] bg-[#f8f9f3] px-4 py-3 text-sm font-medium transition hover:bg-white ${focus}`} onClick={start}><Plus />新建旅行对话</button>
        <div className="mb-3 mt-8 flex items-center justify-between"><h2 className="text-[11px] font-semibold tracking-[0.12em] text-[#7d8977]">最近的对话</h2><button type="button" disabled={state.listing} onClick={() => void chat.reloadList()} className={`rounded-md px-2 py-1.5 text-xs text-[#71816a] disabled:opacity-40 ${focus}`}>刷新</button></div>
        <nav aria-label="旅行对话列表" className="min-h-0 flex-1 overflow-y-auto">
          {state.listing && !state.items.length && <p role="status" className="px-2 py-4 text-xs text-[#7b8576]">正在读取你的会话…</p>}
          {!state.listing && !state.items.length && <p className="px-2 py-4 text-xs leading-6 text-[#7b8576]">还没有旅行对话。<br />从一个想法开始就好。</p>}
          <ul className="space-y-1">{state.items.map(item => <li key={item.id}><button type="button" aria-current={current?.id === item.id ? "page" : undefined} onClick={() => choose(item.id)}
            className={`min-h-12 w-full rounded-xl px-3 py-3 text-left text-sm leading-5 break-words transition ${current?.id === item.id ? "bg-[#dfe7d8] font-medium text-[#294d36]" : "text-[#63715b] hover:bg-[#e5e9de]"} ${focus}`}>{item.title}</button></li>)}</ul>
          {state.nextOffset !== null && <button disabled={state.listing} type="button" className={`mt-3 min-h-11 w-full rounded-xl text-xs text-[#6d7a65] ${focus}`} onClick={() => void chat.loadMore()}>加载更多对话</button>}
        </nav>
        <div className="mt-7 border-t border-[#dce1d4] pt-5">
          <p className="text-xs leading-5 text-[#7c8676]">对话和需求卡保存于你的账号，直到你主动删除。不要发送证件、支付信息或其他敏感内容。</p>
          <div className="mt-4 flex flex-wrap gap-x-4 gap-y-3 text-xs text-[#76866c]"><a href="/knowledge" target="_blank" rel="noopener noreferrer" className={`underline underline-offset-4 ${focus}`}>官方资料检索</a></div>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex min-h-[76px] items-center justify-between gap-2 border-b border-[#eceee7] px-5 sm:px-9">
          <div className="flex min-w-0 items-center gap-3"><button type="button" aria-expanded={sidebarOpen} aria-label="打开旅行对话列表" onClick={() => setSidebarOpen(true)} className={`grid size-11 shrink-0 place-items-center rounded-xl border border-[#e1e6db] md:hidden ${focus}`}><svg viewBox="0 0 20 20" fill="none" stroke="currentColor" className="size-5" aria-hidden="true"><path d="M4 5h12M4 10h12M4 15h8" strokeLinecap="round" /></svg></button><div className="min-w-0"><p className="truncate text-sm font-medium">{current?.title || "你的下一段旅行"}</p><p className="mt-1 text-[11px] text-[#89917f]">先聊清楚，再出发</p></div></div>
          {current && <button type="button" disabled={busy} onClick={() => void chat.refreshConversation()} className={`min-h-11 shrink-0 rounded-xl px-2 text-xs text-[#74816a] hover:bg-[#f0f3ea] disabled:opacity-40 ${focus}`}>重新读取</button>}
        </header>
        <div className="mx-auto flex w-full max-w-[880px] flex-1 flex-col px-5 sm:px-9">
          <div className="flex-1 pb-5 pt-9 sm:pt-12">
            {!current?.messages.length && !state.pendingText && state.pending !== "reading" && <section className="mx-auto max-w-[600px] pb-9 pt-5 sm:pt-14" aria-label="开始旅行对话">
              <div className="mb-7 grid size-12 place-items-center rounded-2xl border border-[#cfdbc8] bg-[#eef3e7] text-[#6b8b58]"><Spark /></div>
              <p className="mb-3 text-[11px] font-semibold tracking-[0.16em] text-[#829170]">A LITTLE IDEA. A NEW JOURNEY.</p>
              <h1 className="text-[2rem] leading-[1.3] font-medium tracking-[-0.045em] sm:text-[2.65rem]">想去哪里，<br /><span className="text-[#718b5e]">我们慢慢聊。</span></h1>
              <p className="mt-5 max-w-lg text-sm leading-7 text-[#7c8872]">不必一次想好所有细节。告诉我目的地、日期，或者一个旅行念头，我们一起把需求理清楚。</p>
              <div className="mt-8 flex flex-wrap gap-2.5">{["想去杭州，带父母轻松玩几天", "帮我整理一趟北京旅行的需求"].map(example => <button type="button" key={example} disabled={busy} onClick={() => { chat.editDraft(example); composer.current?.focus(); }} className={`min-h-11 rounded-xl border border-[#e0e6d9] bg-[#fffefa] px-3.5 py-2.5 text-left text-xs leading-5 text-[#718064] hover:border-[#b8c9a8] disabled:opacity-40 ${focus}`}>{example}<span className="ml-3 inline-block text-[#9aab87]" aria-hidden="true">↗</span></button>)}</div>
              <p className="mt-7 text-xs leading-6 text-[#929a89]">可以先整理1～7天的需求，也可以主动准备真实地点。全国城市目录用于地点准备，不等于全国交通已开放；跨城要求会保留待澄清，不替你预订或自动规划。</p>
            </section>}
            {state.pending === "reading" && <p role="status" className="py-12 text-center text-sm text-[#7d8a72]">正在读取这份旅行对话…</p>}
            {!!current?.messages.length && <ol aria-label="对话消息" className="space-y-7">{current.messages.map(message => <li key={message.id} className={message.role === "user" ? "ml-auto max-w-[90%] sm:max-w-[82%]" : "max-w-[95%]"}>
              <p className={`mb-2 text-[11px] ${message.role === "user" ? "text-right text-[#8d977f]" : "text-[#6f8560]"}`}>{message.role === "user" ? "你" : "TripPilot · 旅行助手"}</p>
              <p className={`whitespace-pre-wrap break-words text-sm leading-7 ${message.role === "user" ? "rounded-[20px] rounded-br-md bg-[#e8eee0] px-5 py-3.5 text-[#40533a]" : "text-[#4b5e42]"}`}>{message.text}</p>
            </li>)}</ol>}
            {!!current?.messages.length && <p className="mt-4 text-xs leading-6 text-[#89917f]">对话建议尚需核实；真实地点、时间和费用以你确认后的程序卡片为准。</p>}
            {state.pendingText && <div className="ml-auto mt-7 max-w-[90%] sm:max-w-[82%]"><p className="mb-2 text-right text-[11px] text-[#8d977f]">你 · {state.pending === "sending" ? "正在发送" : "待核对发送结果"}</p><p className="rounded-[20px] rounded-br-md bg-[#e8eee0] px-5 py-3.5 text-sm leading-7 whitespace-pre-wrap break-words text-[#40533a]">{state.pendingText}</p></div>}
            {state.pending === "sending" && <p role="status" className="mt-6 flex items-center gap-2 text-sm text-[#81936e]"><span className="inline-block size-2 animate-pulse rounded-full bg-[#94ab7d]" />正在处理这次旅行请求…</p>}
            <div className="mt-7 flex flex-wrap gap-2" aria-label="按需查看旅行信息">
              {current && current.messages.length > 0 && <button type="button" aria-expanded={requirementsOpen} aria-controls="travel-requirements-panel" className={`min-h-11 rounded-xl border border-[#cbd6c6] px-4 py-2 text-sm text-[#46604f] ${focus}`} onClick={() => setRequirementsOpen(value => !value)}>{requirementsOpen ? "收起需求卡" : "查看需求卡"}</button>}
            <button type="button" disabled={busy || !!running || state.needsRefresh} aria-expanded={placesOpen}
              className={`min-h-11 rounded-xl border border-[#cbd6c6] px-4 py-2 text-sm text-[#46604f] disabled:opacity-40 ${focus}`}
              onClick={() => { if (placesOpen) setPlacesOpen(false); else void chat.preparePlaces().then(ready => { if (ready) setPlacesOpen(true); }); }}>
              {placesOpen ? "收起地点准备" : "准备真实地点"}
            </button>
              {current && <button type="button" disabled={busy || state.needsRefresh} aria-expanded={executionOpen} aria-controls="assistant-execution-panel" className={`min-h-11 rounded-xl border border-[#cbd6c6] px-4 py-2 text-sm text-[#46604f] disabled:opacity-40 ${focus}`} onClick={() => { setExecutionOpen(value => !value); if (!executionOpen) void execution.reload(); else execution.cancel(); }}>{executionOpen ? "收起执行记录" : "查看执行记录"}</button>}
            </div>
            {current && current.messages.length > 0 && <div id="travel-requirements-panel" hidden={!requirementsOpen}><RequirementCard conversation={current} pending={busy} needsRefresh={state.needsRefresh} onConfirm={() => void chat.confirm()} /></div>}
            {current && <ChatPlaces key={`places:${current.id}`} conversation={current} open={placesOpen} blocked={busy || !!running || state.needsRefresh} onAvailability={acceptPlaces} />}
            {current && <AssistantPlanning conversation={current} places={sharedPlaces?.conversation_id === current.id ? sharedPlaces : null}
              blocked={busy || !!running || state.needsRefresh || placesAvailability?.conversationId !== current.id || !placesAvailability.available}
              canUsePlaces={canUsePlaces} placesBlockedReason={placesAvailability?.conversationId === current.id && placesAvailability.available ? null :
                placesAvailability?.needsRead ? "地点操作可能已提交，结果尚未核对。请打开地点准备，主动重新读取后再准备或操作行程。" :
                  placesAvailability?.pending ? "地点准备正在处理中，请等待结束后核对；不会自动重复操作。" : "请先打开地点准备并读取当前已确认地点，再准备行程。"} />}
            {current && <AssistantPreferences key={`preferences:${current.id}`} conversationId={current.id} />}
            {current && <section id="assistant-execution-panel" hidden={!executionOpen} aria-label="旅行助手执行状态" className="mt-5">
              <h2 className="text-sm font-semibold">本轮做了哪些步骤</h2>
              {execution.state.pending && <p role="status" className="mt-3 text-sm">正在读取执行记录…</p>}
              {execution.state.error && <p role="alert" className="mt-3 break-words text-sm text-[#8b713e]">{execution.state.error}</p>}
              {execution.state.value && <ExecutionSummary value={execution.state.value} />}
              <button type="button" disabled={busy || state.needsRefresh || execution.state.pending} className={`mt-3 min-h-11 rounded-lg px-2 text-xs underline disabled:opacity-40 ${focus}`} onClick={() => void execution.reload()}>重新读取执行记录</button>
            </section>}
            {state.error && <div role="alert" className="mt-6 rounded-2xl border border-[#ead9bc] bg-[#fff8eb] px-4 py-3 text-sm leading-6 break-words text-[#8b713e]">{state.error}{!current && <button type="button" disabled={busy} className={`mt-2 block min-h-9 text-left underline ${focus}`} onClick={() => void (state.needsRefresh ? chat.recoverCreation() : chat.reloadList())}>{state.needsRefresh ? "核对本次会话创建结果" : "重新读取会话列表"}</button>}</div>}
            {state.notice && <p role="status" className="mt-5 text-xs leading-6 text-[#76886a]">{state.notice}</p>}
            {current && ["running", "uncertain"].includes(current.last_turn?.state ?? "") && !busy && <p className="mt-5 text-xs leading-6 text-[#907d53]">{running ? "服务端仍在处理上次请求。请取消或重新读取，不会自动重发。" : "上次请求结果不确定。本会话不自动继续发送，可先重新读取核对。"}</p>}
            {current && <div className="mt-6 text-xs text-[#8b9780]">
              {deletePrompt === `${current.id}:${current.revision}` ? <div className="rounded-xl border border-[#e8dbcd] p-3"><p className="leading-6">删除这份对话及需求卡？此操作不删除历史行程存档，不能撤销。</p><div className="mt-2 flex flex-wrap gap-3"><button type="button" disabled={busy} className={`min-h-11 px-2 text-[#9c654e] underline ${focus}`} onClick={() => { setDeletePrompt(null); void chat.remove(); }}>确认删除这份对话</button><button type="button" className={`min-h-11 px-2 ${focus}`} onClick={() => setDeletePrompt(null)}>保留对话</button></div></div> : <button type="button" disabled={busy} className={`min-h-11 rounded-lg px-1 underline underline-offset-4 disabled:opacity-40 ${focus}`} onClick={() => setDeletePrompt(`${current.id}:${current.revision}`)}>删除这份对话</button>}
            </div>}
            <div ref={end} />
          </div>
          <div className="sticky bottom-0 bg-[#faf9f5]/95 pb-5 pt-3 backdrop-blur-sm sm:pb-7">
            <form aria-label="发送旅行需求" onSubmit={event => { event.preventDefault(); void chat.send(); }} className="rounded-[22px] border border-[#d6dfcd] bg-[#fffefa] p-3 shadow-[0_5px_24px_-12px_#45613230] focus-within:border-[#9eb48a]">
              <label htmlFor="travel-message" className="sr-only">告诉我你的旅行想法</label>
              <textarea id="travel-message" ref={composer} rows={3} value={state.draft} onChange={event => chat.editDraft(event.target.value)} onKeyDown={keyboard} placeholder="告诉我你的旅行想法…" maxLength={4000}
                className="block max-h-52 min-h-20 w-full resize-y rounded-xl bg-transparent px-2 py-1 text-sm leading-6 text-[#42563a] outline-none placeholder:text-[#a2ad98]" />
              <div className="mt-1 flex items-end justify-between gap-3 pl-2"><p className={`text-[10px] leading-5 ${textLength > 2000 ? "text-[#a45a45]" : "text-[#a1ab96]"}`}>Enter发送 · Shift+Enter换行<span className="ml-2">{textLength}/2000</span></p>
                {state.pending === "sending" || running ? <button type="button" onClick={() => void chat.cancel()} aria-label="停止本次旅行请求" className={`grid size-11 shrink-0 place-items-center rounded-xl border border-[#bccbad] bg-[#e9efdf] text-[#5a7746] ${focus}`}><span className="size-3 rounded-sm bg-current" /></button> : <button type="submit" aria-label="发送旅行需求" disabled={!!sendBlocked || textLength > 2000} className={`grid size-11 shrink-0 place-items-center rounded-xl bg-[#5b7848] text-white transition hover:bg-[#486439] disabled:cursor-not-allowed disabled:bg-[#d5dfc9] disabled:text-[#f8fbf3] ${focus}`}><Arrow /></button>}
              </div>
            </form>
            <p className="mt-2.5 text-center text-[10px] leading-5 text-[#929c87]">模型可能理解错误；真实地点与重要操作仍由你明确确认。未发送的文字不会保存。</p>
          </div>
        </div>
      </div>
    </div>
  </main>;
}
