"use client";

import { useCallback, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { ChatConversation } from "@/lib/chat";
import type { PlacesWorkspace } from "@/lib/chat-places";
import { assistantPlanningKey, DEFAULT_ASSISTANT_WINDOW, prepareAssistantPlanning } from "@/lib/assistant-planning";
import type { WorkspaceInputs } from "@/lib/restore-trip";
import EditablePlanningSession from "../trip/editable-planning-session";
import SavedHistory from "../trip/saved-history";

const focus = "focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[#376d5b]";
const button = `min-h-11 rounded-xl border border-[#cbd6c6] px-4 py-2 text-sm text-[#46604f] disabled:opacity-40 ${focus}`;
type Context = { key: string };
type Prepared = { context: Context; sessionId: string; inputs: WorkspaceInputs };

/** The established planner stays mounted while this disclosure is collapsed. */
export default function AssistantPlanning({ conversation, places = null, blocked = false, canUsePlaces, placesBlockedReason }: { conversation: ChatConversation; places?: PlacesWorkspace | null; blocked?: boolean; canUsePlaces?: () => boolean; placesBlockedReason?: string | null }) {
  const [open, setOpen] = useState(false);
  const [window, setWindow] = useState(() => ({ ...DEFAULT_ASSISTANT_WINDOW }));
  const [prepared, setPrepared] = useState<Prepared | null>(null);
  const [error, setError] = useState<{ context: Context; text: string } | null>(null);
  const key = assistantPlanningKey(conversation, places, window);
  const context = useMemo<Context>(() => ({ key }), [key]);
  const current = useRef(context), alive = useRef(true);
  const busy = useRef(blocked), preparedRef = useRef<Prepared | null>(null), capability = useRef(canUsePlaces);
  useLayoutEffect(() => { alive.current = true; current.current = context; busy.current = blocked; return () => { alive.current = false; }; }, [context, blocked]);
  useLayoutEffect(() => { capability.current = canUsePlaces; }, [canUsePlaces]);
  // `inert` is presentation only. Retained child callbacks must consult the
  // current synchronous place capability before freezing, sending or adopting.
  const canOperate = useCallback(() => alive.current && preparedRef.current?.context === current.current &&
    !busy.current && (capability.current?.() ?? true), []);
  const active = prepared?.context === context ? prepared : null;
  const prepare = () => {
    if (!alive.current || current.current !== context || busy.current || blocked || canUsePlaces && !canUsePlaces()) return;
    try {
      const inputs = prepareAssistantPlanning(conversation, places, window);
      const next = { context, sessionId: crypto.randomUUID(), inputs };
      preparedRef.current = next; setPrepared(next); setError(null);
    } catch (error) { setError({ context, text: error instanceof Error ? error.message : "行程输入无效，尚未准备工作副本。" }); }
  };
  return <section aria-label="聊天内行程工具" className="mt-5 min-w-0">
    <div className="flex flex-wrap gap-2"><button type="button" className={button} aria-expanded={open} aria-controls="assistant-planning-panel" onClick={() => setOpen(value => !value)}>{open ? "收起行程工具" : "打开行程工具"}</button><SavedHistory label="查看历史行程" /></div>
    <div id="assistant-planning-panel" hidden={!open} className="mt-4 min-w-0 rounded-2xl border border-[#dce4dc] bg-[#fffefa] p-4 sm:p-5">
      <h2 className="text-lg font-semibold">核对输入，再主动生成</h2>
      <p className="mt-2 text-xs leading-6 text-[#73816a]">使用当前需求与服务端已确认住宿／必去地点。未确认的地点文字不会加入排程。这里只准备独立规划副本，点击准备不会查询路线、天气或模型，也不会自动保存。</p>
      <p className="mt-2 text-xs leading-6 text-[#73816a]">行程工具目前支持六城单城、1～7天、单住宿。地点目录中的其他城市仍仅供地点准备。成都完整公交样本未通过；查询估时不保证旅行当天运营、营业或预约。</p>
      {placesBlockedReason && <p role="status" className="mt-3 text-xs leading-6 text-[#8b713e]">{placesBlockedReason}</p>}
      {!active && <>
        <fieldset className="mt-4 flex flex-wrap gap-4"><legend className="text-xs font-medium">每日活动窗口 · 默认设置，可在准备前修改</legend>
          <label className="mt-2 text-xs">每日开始<input type="time" value={window.start} className={`ml-2 min-h-11 rounded-lg border border-[#cbd6c6] px-2 ${focus}`} onChange={event => setWindow(value => ({ ...value, start: event.target.value }))} /></label>
          <label className="mt-2 text-xs">每日结束<input type="time" value={window.end} className={`ml-2 min-h-11 rounded-lg border border-[#cbd6c6] px-2 ${focus}`} onChange={event => setWindow(value => ({ ...value, end: event.target.value }))} /></label>
        </fieldset>
        <p className="mt-3 text-xs leading-6 text-[#73816a]">初始停留每处60分钟、午餐12:00–13:00、节奏均衡，均为程序默认设置，不是你已表达的要求；可在规划副本修改。其他聊天偏好仅保留在需求卡，未自动转成排程约束。</p>
        <button type="button" disabled={blocked} className={`${button} mt-3 bg-[#325b46] text-white`} onClick={prepare}>核对并准备行程</button>
      </>}
      {prepared && !active && <p role="status" className="mt-3 text-xs leading-6 text-[#8b713e]">聊天需求或已确认地点已变化，原规划副本已失效。请核对当前输入后重新准备，不会恢复旧草案。</p>}
      {error?.context === context && <p role="alert" className="mt-3 break-words text-sm text-[#8b713e]">{error.text}</p>}
      {active && <>
        <p className="mb-4 mt-4 rounded-xl bg-[#f0f4e9] p-3 text-xs leading-6">下方为独立的本地规划副本。修改规划需求不会回写聊天意向或地点准备；确认会按既有规则清理草案／估算，取消保留原工作。刷新会丢失未保存的草案和费用；明确保存后的行程可在历史中读取。</p>
        <div inert={blocked}><EditablePlanningSession key={active.sessionId} sessionId={active.sessionId} initial={active.inputs} origin={{ kind: "new" }} canOperate={canOperate} /></div>
      </>}
    </div>
  </section>;
}
