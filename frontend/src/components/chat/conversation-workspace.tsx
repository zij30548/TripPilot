"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { sendToolDialogueTurn, type DialogueSelectionContext } from "@/lib/assistant";
import { readDialogueAction } from "@/lib/dialogue-action";
import { useChatWorkspace } from "@/lib/chat-controller";
import DialogueTools, { type DialogueProposal, type DialogueToolsHandle } from "./dialogue-tools";

const focus = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#294e43]/40 focus-visible:ring-offset-2";
const suggestions = ["想去杭州，带父母轻松玩几天", "帮我想想北京周末可以怎么逛"];

function Plus() {
  return <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="M10 4v12M4 10h12" strokeLinecap="round" /></svg>;
}

export default function ConversationWorkspace() {
  const [proposal, setProposal] = useState<DialogueProposal | null>(null);
  const tools = useRef<DialogueToolsHandle | null>(null), input = useRef("");
  const selection = useRef<DialogueSelectionContext | undefined>(undefined);
  const send = useCallback<typeof sendToolDialogueTurn>(async (id, body, signal) => {
    const context = selection.current;
    const result = await sendToolDialogueTurn(id, { ...body, ...(context ? { dialogue_context: context } : {}) }, signal);
    if (signal.aborted) return result;
    try {
      const action = await readDialogueAction(id, result.revision, body.operation_id, signal);
      if (!signal.aborted) setProposal({ conversation: id, revision: result.revision, operation: body.operation_id, action, context });
    } catch (error) {
      // A lost readback cannot undo a committed conversation or invent an action.
      if (!signal.aborted) setProposal({ conversation: id, revision: result.revision, operation: body.operation_id, action: null,
        error: error instanceof Error ? error.message : "无法核对操作提议，未执行工具。" });
    }
    return result;
  }, []);
  const chat = useChatWorkspace(send), { state } = chat;
  const [toolBusy, setToolBusy] = useState(false);
  const [localMessages, setLocalMessages] = useState<{ id: string; conversation: string; after: string | null; role: "user" | "assistant"; text: string }[]>([]);
  const currentConversation = useRef(state.conversation);
  // Child receipt effects must anchor after the newly committed assistant message.
  useLayoutEffect(() => { currentConversation.current = state.conversation; }, [state.conversation]);
  const emit = useCallback((role: "user" | "assistant", text: string) => {
    const c = currentConversation.current;
    if (c) setLocalMessages(rows => [...rows, { id: crypto.randomUUID(), conversation: c.id, after: c.messages.at(-1)?.id ?? null, role, text }]);
  }, []);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [deletePrompt, setDeletePrompt] = useState<string | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const composer = useRef<HTMLTextAreaElement>(null), end = useRef<HTMLDivElement>(null);
  const sidebar = useRef<HTMLElement>(null), sidebarToggle = useRef<HTMLButtonElement>(null), sidebarClose = useRef<HTMLButtonElement>(null);
  const current = state.conversation;
  const busy = state.pending !== null || toolBusy;
  const running = current?.last_turn?.state === "running";
  const textLength = Array.from(state.draft).length;
  const sendBlocked = busy || running || state.needsRefresh || !state.draft.trim() || textLength > 2000;

  useEffect(() => { end.current?.scrollIntoView({ block: "end", behavior: "smooth" }); }, [current?.id, current?.messages.length, state.pendingText, localMessages]);
  useEffect(() => { if (sidebarOpen) sidebarClose.current?.focus(); }, [sidebarOpen]);

  function closeSidebar() { setSidebarOpen(false); sidebarToggle.current?.focus(); }
  function clearLocal() { tools.current?.beforeModel(); setProposal(null); setToolBusy(false); setLocalMessages([]); }
  function choose(id: string) { clearLocal(); setSidebarOpen(false); setDeletePrompt(null); setMenuOpen(false); void chat.selectConversation(id); }
  function start() { clearLocal(); setSidebarOpen(false); setDeletePrompt(null); setMenuOpen(false); chat.newConversation(); input.current = ""; composer.current?.focus(); }
  function submit() {
    if (busy || running || state.needsRefresh) return;
    const text = input.current.trim();
    if (!text || Array.from(text).length > 2000) return;
    if (tools.current?.submit(text)) { input.current = ""; chat.editDraft(""); return; }
    selection.current = tools.current?.selectionContext();
    tools.current?.beforeModel(); setProposal(null); void chat.send(); input.current = "";
  }
  const visibleMessages = current ? [
    ...localMessages.filter(row => row.conversation === current.id && row.after === null),
    ...current.messages.flatMap(message => [message, ...localMessages.filter(row => row.conversation === current.id && row.after === message.id)]),
  ] : [];
  function keyboard(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229) {
      event.preventDefault();
      // The controller reads the synchronous input snapshot; this also handles
      // input + Enter before React has committed another render.
      if (!busy && !running && !state.needsRefresh) event.currentTarget.form?.requestSubmit();
    }
  }
  function sidebarKeyboard(event: KeyboardEvent<HTMLElement>) {
    if (!sidebarOpen) return;
    if (event.key === "Escape") { event.preventDefault(); closeSidebar(); return; }
    if (event.key !== "Tab") return;
    const items = sidebar.current?.querySelectorAll<HTMLElement>("button:not(:disabled), [href], [tabindex='0']");
    if (!items?.length) return;
    const first = items[0], last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }

  return <main className="flex h-[calc(100dvh-114px)] min-h-[420px] bg-white text-[#17211f] md:h-[calc(100dvh-76px)]">
    {sidebarOpen && <button type="button" aria-label="关闭对话列表遮罩" onClick={closeSidebar} className="fixed inset-0 z-30 bg-[#17211f]/20 md:hidden" />}
    <aside ref={sidebar} aria-label="我的对话" role={sidebarOpen ? "dialog" : undefined} aria-modal={sidebarOpen || undefined} onKeyDown={sidebarKeyboard}
      className={`${sidebarOpen ? "fixed inset-y-0 left-0 z-40 flex w-[min(84vw,280px)] shadow-xl" : "hidden"} shrink-0 flex-col border-r border-[#e8ece9] bg-[#f7f8fa] p-4 md:static md:flex md:w-60 md:shadow-none`}>
      <div className="flex items-center justify-between px-2 pb-5 pt-2">
        <h2 className="text-sm font-medium">对话</h2>
        <button ref={sidebarClose} type="button" onClick={closeSidebar} className={`min-h-10 rounded-lg px-2 text-xs text-[#74817c] md:hidden ${focus}`}>关闭</button>
      </div>
      <button type="button" onClick={start} className={`flex min-h-11 items-center justify-center gap-2 rounded-xl border border-[#dce4df] bg-white px-4 text-sm font-medium text-[#294e43] transition hover:border-[#aebfb4] ${focus}`}><Plus />新建对话</button>
      <div className="mb-2 mt-7 flex items-center justify-between px-2"><p className="text-[11px] text-[#8b9690]">最近对话</p><button type="button" disabled={state.listing} onClick={() => void chat.reloadList()} className={`min-h-9 rounded-md px-2 text-[11px] text-[#74817c] disabled:opacity-40 ${focus}`}>刷新列表</button></div>
      <nav aria-label="旅行对话列表" className="min-h-0 flex-1 overflow-y-auto">
        {state.listing && !state.items.length && <p role="status" className="px-3 py-4 text-xs text-[#8b9690]">正在读取…</p>}
        {!state.listing && !state.items.length && <p className="px-3 py-4 text-xs leading-6 text-[#8b9690]">还没有对话</p>}
        <ul className="space-y-1">{state.items.map(item => <li key={item.id}><button type="button" aria-current={current?.id === item.id ? "page" : undefined} onClick={() => choose(item.id)} title={item.title} className={`block min-h-11 w-full truncate rounded-lg px-3 py-3 text-left text-xs leading-5 transition ${current?.id === item.id ? "bg-[#e7eeea] font-medium text-[#294e43]" : "text-[#68756f] hover:bg-[#edf0ee]"} ${focus}`}>{item.title}</button></li>)}</ul>
        {state.nextOffset !== null && <button type="button" disabled={state.listing} onClick={() => void chat.loadMore()} className={`mt-3 min-h-11 w-full rounded-lg text-xs text-[#74817c] disabled:opacity-40 ${focus}`}>加载更多对话</button>}
      </nav>
      <p className="mt-4 border-t border-[#e4e9e5] px-2 pt-4 text-[10px] leading-5 text-[#8b9690]">对话保存在当前浏览器工作区；清除Cookie后将无法找回。请勿发送证件或支付信息。</p>
    </aside>

    <section aria-label="旅行助手对话" className="flex min-w-0 flex-1 flex-col">
      <header className="flex min-h-16 shrink-0 items-center justify-between gap-3 border-b border-[#f0f2f0] px-5 sm:px-8">
        <div className="flex min-w-0 items-center gap-2">
          <button ref={sidebarToggle} type="button" aria-label="打开旅行对话列表" aria-expanded={sidebarOpen} onClick={() => setSidebarOpen(true)} className={`grid size-10 shrink-0 place-items-center rounded-lg text-[#68756f] md:hidden ${focus}`}><svg width="19" height="19" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="M4 5h12M4 10h12M4 15h8" strokeLinecap="round" /></svg></button>
          <p className="truncate text-xs font-medium text-[#74817c]">{current?.title || "旅行助手"}</p>
        </div>
        {current && <div className="relative shrink-0"><button type="button" aria-label="对话操作" aria-expanded={menuOpen} onClick={() => setMenuOpen(value => !value)} className={`grid size-10 place-items-center rounded-lg text-xl text-[#8a968f] hover:bg-[#f7f8fa] ${focus}`}>···</button>{menuOpen && <div className="absolute right-0 z-20 mt-2 w-36 rounded-xl border border-[#e5ebe7] bg-white p-2 shadow-lg"><button type="button" disabled={busy} onClick={() => { setMenuOpen(false); void chat.refreshConversation(); }} className={`min-h-10 w-full rounded-lg px-3 text-left text-xs text-[#74817c] hover:bg-[#f7f8fa] disabled:opacity-40 ${focus}`}>重新读取</button><button type="button" disabled={busy} onClick={() => { setMenuOpen(false); setDeletePrompt(`${current.id}:${current.revision}`); }} className={`min-h-10 w-full rounded-lg px-3 text-left text-xs text-[#986751] hover:bg-[#f7f8fa] disabled:opacity-40 ${focus}`}>删除对话</button></div>}</div>}
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-[800px] px-5 pb-8 pt-7 sm:px-10 sm:pt-10">
          {!visibleMessages.length && !state.pendingText && state.pending !== "reading" && <section aria-label="开始旅行对话" className="mx-auto max-w-xl py-12 text-center sm:py-20">
            <div aria-hidden="true" className="mx-auto mb-7 flex size-11 items-center justify-center rounded-2xl bg-[#edf2ee] text-[#587565]"><svg width="23" height="23" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.35"><path d="m12 3 2.1 6.9L21 12l-6.9 2.1L12 21l-2.1-6.9L3 12l6.9-2.1L12 3Z" strokeLinejoin="round" /></svg></div>
            <h1 className="text-[28px] leading-snug font-medium tracking-tight sm:text-4xl">下一站，想去哪里？</h1>
            <p className="mt-4 text-sm leading-7 text-[#8a968f]">从一个想法开始，慢慢把旅行聊清楚。</p>
            <div className="mt-8 flex flex-col items-stretch justify-center gap-2 sm:flex-row sm:flex-wrap">{suggestions.map(example => <button type="button" key={example} disabled={busy} onClick={() => { input.current = example; chat.editDraft(example); composer.current?.focus(); }} className={`min-h-11 rounded-xl border border-[#e6ebe7] px-4 py-2.5 text-left text-xs leading-5 text-[#74817c] transition hover:border-[#b8c9bd] hover:text-[#294e43] disabled:opacity-40 ${focus}`}>{example}<span aria-hidden="true" className="ml-3 text-[#a1afa5]">↗</span></button>)}</div>
          </section>}
          {state.pending === "reading" && <p role="status" className="py-10 text-center text-xs text-[#8b9690]">正在读取对话…</p>}
          {!!visibleMessages.length && <ol aria-label="对话消息" className="space-y-8">{visibleMessages.map(message => <li key={message.id} className={message.role === "user" ? "ml-auto max-w-[90%] sm:max-w-[85%]" : "max-w-full"}>
            <p className={`mb-2 text-[10px] ${message.role === "user" ? "text-right text-[#9aa59e]" : "font-medium tracking-wide text-[#587565]"}`}>{message.role === "user" ? "你" : "TripPilot"}</p>
            <p className={`whitespace-pre-wrap break-words text-sm leading-7 ${message.role === "user" ? "rounded-2xl rounded-br-md bg-[#f1f4f1] px-4 py-3 text-[#38493f]" : "text-[#38493f]"}`}>{message.text}</p>
          </li>)}</ol>}
          {state.pendingText && <div className="ml-auto mt-8 max-w-[90%] sm:max-w-[85%]"><p className="mb-2 text-right text-[10px] text-[#9aa59e]">{state.pending === "sending" ? "正在发送" : "发送结果待核对"}</p><p className="rounded-2xl rounded-br-md bg-[#f1f4f1] px-4 py-3 text-sm leading-7 whitespace-pre-wrap break-words text-[#38493f]">{state.pendingText}</p></div>}
          {(state.pending === "sending" || toolBusy) && <p role="status" className="mt-7 flex items-center gap-2 text-xs text-[#83968a]"><span aria-hidden="true" className="size-1.5 animate-pulse rounded-full bg-[#78947e]" />{toolBusy ? "正在执行已确认操作…" : "正在回复…"}</p>}
          {state.error && <div role="alert" className="mt-7 border-l-2 border-[#c3b399] pl-4 text-xs leading-6 break-words text-[#8b795b]">{state.error}{!current && <button type="button" disabled={busy} onClick={() => void (state.needsRefresh ? chat.recoverCreation() : chat.reloadList())} className={`mt-2 block min-h-9 rounded-md underline underline-offset-4 disabled:opacity-40 ${focus}`}>{state.needsRefresh ? "核对本次会话创建结果" : "重新读取会话列表"}</button>}</div>}
          {state.notice && <p role="status" className="mt-5 text-xs leading-6 text-[#839087]">{state.notice}</p>}
          {current && !busy && (state.needsRefresh || running) && <p className="mt-5 text-xs leading-6 text-[#8b795b]">{running ? "上次请求仍在处理，请取消或重新读取；不会自动重发。" : "上次请求结果待核对，请重新读取会话；不会自动重发。"}</p>}
          {current && deletePrompt === `${current.id}:${current.revision}` && <div role="group" aria-label="确认删除对话" className="mt-6 border-t border-[#e9ece9] pt-4 text-xs text-[#74817c]"><p className="leading-6">删除这份对话？不能撤销，已有行程存档不受影响。</p><div className="mt-2 flex flex-wrap gap-4"><button type="button" disabled={busy} onClick={() => { setDeletePrompt(null); void chat.remove(); }} className={`min-h-10 rounded-md px-1 text-[#986751] underline underline-offset-4 disabled:opacity-40 ${focus}`}>确认删除</button><button type="button" onClick={() => setDeletePrompt(null)} className={`min-h-10 rounded-md px-1 ${focus}`}>保留对话</button></div></div>}
          <div ref={end} />
        </div>
      </div>
      <div className="mx-auto w-full max-w-[800px] shrink-0 bg-white px-5 pb-4 pt-3 sm:px-10 sm:pb-5">
        <form aria-label="发送旅行需求" onSubmit={event => { event.preventDefault(); submit(); }} className="rounded-2xl border border-[#dfe6e0] bg-white p-3 shadow-[0_4px_20px_-12px_#294e4326] focus-within:border-[#9fb6a5]">
          <label htmlFor="conversation-message" className="sr-only">告诉我你的旅行想法</label>
          <textarea id="conversation-message" ref={composer} rows={2} maxLength={4000} value={state.draft} onChange={event => { input.current = event.target.value; chat.editDraft(event.target.value); }} onKeyDown={keyboard} placeholder="聊聊你的旅行想法…" className="block max-h-44 min-h-14 w-full resize-y bg-transparent px-1 py-1 text-sm leading-6 text-[#38493f] outline-none placeholder:text-[#a0aca3]" />
          <div className="flex items-end justify-between gap-3 pl-1"><p className={`text-[10px] leading-5 ${textLength > 2000 ? "text-[#a35f4b]" : "text-[#a0aca3]"}`}>Enter发送 · Shift+Enter换行<span className="ml-2">{textLength}/2000</span></p>
            {state.pending === "sending" || running || toolBusy ? <button type="button" aria-label="停止本次旅行请求" onClick={() => { if (toolBusy) tools.current?.cancel(); else void chat.cancel(); }} className={`grid size-10 shrink-0 place-items-center rounded-xl border border-[#c5d4c9] bg-[#edf2ee] text-[#587565] ${focus}`}><span aria-hidden="true" className="size-3 rounded-sm bg-current" /></button> : <button type="submit" aria-label="发送旅行需求" disabled={!!sendBlocked} className={`grid size-10 shrink-0 place-items-center rounded-xl bg-[#294e43] text-white transition hover:bg-[#203f36] disabled:cursor-not-allowed disabled:bg-[#e0e7e1] ${focus}`}><svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d="M12 19V5m-6 6 6-6 6 6" strokeLinecap="round" strokeLinejoin="round" /></svg></button>}
          </div>
        </form>
        <p className="mt-2.5 text-center text-[10px] leading-5 text-[#9aa59e]">操作先确认 · 地点与实时信息请核实 · 未保存规划刷新后丢失</p>
      </div>
    </section>
    {current && <DialogueTools key={current.id} conversation={current} blocked={state.pending !== null || state.needsRefresh}
      proposal={proposal} handle={tools} onMessage={emit} onBusy={setToolBusy} onConfirmRequirements={chat.confirm} />}
  </main>;
}
