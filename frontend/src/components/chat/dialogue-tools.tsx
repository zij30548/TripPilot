"use client";

import { useCallback, useEffect, useImperativeHandle, useLayoutEffect, useRef, useState, type Ref } from "react";
import type { ChatConversation } from "@/lib/chat";
import { authSnapshot } from "@/lib/auth-session";
import { actionDescription, confirmationReply, isDialogueAction, needsActionConfirmation, type DialogueAction } from "@/lib/dialogue-action";
import { DialogueConfirmation, localDialogueAction } from "@/lib/dialogue-confirmation";
import { bindDialogueSelection } from "@/lib/dialogue-selection";
import { useDialoguePlaces } from "@/lib/dialogue-places-controller";
import { useDialoguePreferences } from "@/lib/dialogue-preferences-controller";
import { useDialoguePlanning, resolveDialogueSelector, type DialoguePlanning } from "@/lib/dialogue-planning-controller";
import { assertConfirmedPlanningRequirements, prepareAssistantPlanning, DEFAULT_ASSISTANT_WINDOW } from "@/lib/assistant-planning";
import { applyEditedRequirements, restoreSavedTrip, type WorkspaceInputs } from "@/lib/restore-trip";
import { useSavedHistory } from "@/lib/use-saved-trips";
import { historyDialogueText } from "@/lib/dialogue-travel-text";
import type { VenueId } from "@/lib/knowledge";
import { searchKnowledge } from "@/lib/knowledge-api";
import { DESTINATIONS } from "@/lib/destinations";
import { chatCity } from "@/lib/chat-cities";
import { canonical } from "@/lib/saved-trips";
import type { ScheduleCapture } from "@/lib/schedule-adjustment";
import type { DialogueSelectionContext } from "@/lib/assistant";
import { candidateItems } from "@/lib/candidate-pool-state";
import DialogueMedia from "./dialogue-media";
import DialogueMap from "./dialogue-map";
import TripRequestForm from "@/components/trip-request-form";

export type DialogueProposal = { conversation: string; revision: number; operation: string; action: DialogueAction | null; context?: DialogueSelectionContext; error?: string };
export type DialogueToolsHandle = { submit: (text: string) => boolean; cancel: () => void; beforeModel: () => void; selectionContext: () => DialogueSelectionContext };
type Prepared = { key: string; inputs: WorkspaceInputs; origin: string; requirementsKey: string; placesKey: string | null };
const button = "min-h-11 rounded-lg border border-stone-300 px-3 text-sm focus-visible:outline-2 focus-visible:outline-offset-2";

function Planner({ prepared, available, retryAllowed, controllerRef }: { prepared: Prepared; available: () => boolean; retryAllowed: () => boolean; controllerRef: { current: DialoguePlanning | null } }) {
  const planning = useDialoguePlanning(prepared.inputs, available, retryAllowed);
  useLayoutEffect(() => { controllerRef.current = planning; return () => { if (controllerRef.current === planning) controllerRef.current = null; }; }, [controllerRef, planning]);
  return null;
}

/** Stable local authority, mounted per account/conversation. Proposals are not execution receipts.
 * No effect performs an external business query; only an explicit command/confirmation can.
 */
export default function DialogueTools({ conversation, blocked, proposal, handle, onMessage, onBusy, onConfirmRequirements }: {
  conversation: ChatConversation; blocked: boolean; proposal: DialogueProposal | null; handle: Ref<DialogueToolsHandle>;
  onMessage: (role: "user" | "assistant", text: string) => void; onBusy: (busy: boolean) => void;
  onConfirmRequirements: () => Promise<void>;
}) {
  const places = useDialoguePlaces(conversation, blocked), preferences = useDialoguePreferences(conversation.id), history = useSavedHistory();
  const [prepared, setPrepared] = useState<Prepared | null>(null), [media, setMedia] = useState<"photo" | "video" | null>(null);
  const [map, setMap] = useState<{ key: string; date?: string; edgeId?: string; capture: ScheduleCapture } | null>(null), [editing, setEditing] = useState<WorkspaceInputs | null>(null);
  const planner = useRef<DialoguePlanning | null>(null), current = useRef({ conversation, blocked, prepared });
  const mounted = useRef(true), serial = useRef(0), active = useRef<AbortController | null>(null), confirmation = useRef(new DialogueConfirmation());
  const consumed = useRef<string | null>(null), epoch = useRef(authSnapshot().epoch), selectionToken = useRef<string | undefined>(undefined);
  const display = useRef<DialogueSelectionContext["display"]>("none");
  const requirementsKey = canonical(conversation.requirements);
  const knownPlacesKey = useRef<string | null>(null);
  const visiblePlaces = places.state.workspace;
  const placesKey = visiblePlaces && visiblePlaces.intent_revision === conversation.revision && !visiblePlaces.stale ?
    canonical({ city: visiblePlaces.city_id, home: visiblePlaces.accommodation, required: visiblePlaces.required_places }) : null;
  const allowed = useCallback(() => mounted.current && epoch.current === authSnapshot().epoch && !current.current.blocked, []);
  const planningAllowed = useCallback(() => allowed() && !!current.current.prepared &&
    current.current.prepared.requirementsKey === canonical(current.current.conversation.requirements) &&
    current.current.prepared.placesKey === knownPlacesKey.current, [allowed]);
  useLayoutEffect(() => { current.current = { conversation, blocked, prepared }; }, [conversation, blocked, prepared]);
  useLayoutEffect(() => {
    if (placesKey !== null) knownPlacesKey.current = placesKey;
    if (prepared && (prepared.requirementsKey !== requirementsKey || prepared.placesKey !== knownPlacesKey.current)) {
      confirmation.current.clear(); planner.current?.invalidate();
      // External intent/place authority changed; remove obsolete SDK state before paint.
      setMap(null);
    }
  }, [requirementsKey, placesKey, prepared]);
  const binding = () => ({ conversation: conversation.id, revision: conversation.revision, epoch: authSnapshot().epoch,
    workspace: `${prepared?.key ?? "none"}:${planner.current?.readVersion() ?? 0}:${places.current()?.revision ?? -1}` });
  function cancel(silent = false) {
    const wasActive = active.current !== null;
    serial.current++; active.current?.abort(); active.current = null;
    confirmation.current.clear(); selectionToken.current = undefined;
    planner.current?.cancelOperation(); places.cancel(); preferences.cancel(); if (wasActive) history.cancel();
    setMedia(null); setEditing(null); onBusy(false);
    if (!silent) onMessage("assistant", "已取消本次等待或待确认操作，未采用新方案。已发送的调用或存档可能已经处理，不会自动重试。");
  }
  useEffect(() => { mounted.current = true; const clock = serial, pending = confirmation; return () => { mounted.current = false; clock.current++; active.current?.abort(); pending.current.clear(); }; }, []);
  useEffect(() => {
    // Any new persisted turn invalidates its predecessor's proposal, even ABA.
    confirmation.current.clear(); selectionToken.current = undefined;
    // An external conversation revision synchronously cancels this attachment session.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setMedia(null);
  }, [conversation.revision]);
  function requireResolvedSave() {
    const status = planner.current?.saving?.readState().status;
    if (status === "uncertain" || status === "saving") throw new Error("上一份冻结快照的保存结果尚未确认，暂不能替换规划输入或创建历史副本。请先重新发送“保存行程 原名称”并确认；只会核对原快照与原操作标识，不会创建新存档。");
  }
  function install(inputs: WorkspaceInputs, origin: string) {
    requireResolvedSave();
    planner.current?.invalidate(); planner.current = null; setMap(null);
    const livePlaces = places.current();
    const source = livePlaces ? canonical({ city: livePlaces.city_id, home: livePlaces.accommodation, required: livePlaces.required_places }) : knownPlacesKey.current;
    knownPlacesKey.current = source;
    const next = { key: crypto.randomUUID(), inputs: structuredClone(inputs), origin, requirementsKey, placesKey: source };
    current.current = { ...current.current, prepared: next }; setPrepared(next);
  }
  async function execute(action: DialogueAction, confirmed = false) {
    if (!allowed() || active.current || !isDialogueAction(action)) return;
    const controller = new AbortController(), version = ++serial.current, revision = conversation.revision;
    active.current = controller; onBusy(true);
    const valid = () => allowed() && active.current === controller && serial.current === version && current.current.conversation.revision === revision && !controller.signal.aborted;
    const reply = (text: string) => { if (valid()) onMessage("assistant", text); };
    try {
      if (["prepare_planning", "restore_saved", "set_daily_window"].includes(action.name)) requireResolvedSave();
      if (action.name === "prepare_planning") assertConfirmedPlanningRequirements(conversation);
      if (action.name === "confirm_requirements") { await onConfirmRequirements(); return; }
      if (["read_preferences", "save_preference", "delete_preference"].includes(action.name)) {
        const result = await preferences.execute(action, confirmed); reply(result.text); return;
      }
      if (["search_places", "remove_place", "open_photo", "open_video", "prepare_planning"].includes(action.name)) {
        const ready = await places.ensureReady(confirmed);
        if (!valid()) return;
        const workspace = places.current();
        if (!workspace || workspace.stale || !workspace.city_id) { reply(ready.text); return; }
        if (action.name === "prepare_planning") {
          const before = planner.current?.readInputs();
          const window = before ? { start: before.request.daily_start_time.slice(0, 5), end: before.request.daily_end_time.slice(0, 5) } : DEFAULT_ASSISTANT_WINDOW;
          const seed = prepareAssistantPlanning(conversation, workspace, window);
          const edited = before ? applyEditedRequirements(before, seed.request) : { inputs: seed, notices: [] };
          edited.inputs.settings.transportMode = seed.settings.transportMode;
          install(edited.inputs, "当前对话已确认需求与地点");
          reply(["已准备规划输入，尚未生成路线。", `${seed.request.start_date}—${seed.request.end_date}，${seed.request.travelers} 人；住宿：${seed.request.accommodation_place?.name}。`,
            `每日 ${window.start}—${window.end}；新地点默认停留 60 分钟；午餐预留 ${edited.inputs.settings.lunch.start}—${edited.inputs.settings.lunch.end}${edited.inputs.settings.lunch.enabled ? "（启用）" : "（关闭）"}。`,
            ...edited.notices, "可继续修改停留、午餐和费用，或回复“生成行程”。本页未保存工作在刷新或切换对话后丢失。"].join("\n")); return;
        }
        if (action.name === "open_photo" || action.name === "open_video") {
          if (!DESTINATIONS.some(city => city.adcode === workspace.city_id)) { reply("媒体地点线索目前只接入六个排程城市；不自动切换城市。"); return; }
          if (workspace.required_places.length >= 6) { reply("必去已达六个，请先明确移除一个。没有发起识别。"); return; }
          setMedia(action.name === "open_photo" ? "photo" : "video"); return;
        }
        const result = await places.execute(action); reply(result.text); return;
      }
      if (["confirm_place", "preview_place"].includes(action.name)) {
        const ready = action.name === "preview_place" && !places.canUse() ? await places.ensureReady(false) : null;
        if (!valid()) return;
        const result = action.name === "confirm_place" ? await places.confirm(Number(action.arguments.number), selectionToken.current) : places.preview(Number(action.arguments.number), ready?.workspace);
        reply(result.text); return;
      }
      if (action.name === "search_materials") {
        const result = await searchKnowledge({ venue_id: action.arguments.venue_id as VenueId, question: String(action.arguments.question), limit: 5 }, controller.signal);
        reply(result.matches.length ? [`${result.venue.name} · 资料检索（不是已核验答案）`, ...result.matches.map(row =>
          `${row.section.heading}\n整理摘要：${row.section.summary}\n短摘录：${row.section.excerpt}\n来源：${row.source.title}\n${row.source.url}\n发布日期：${row.source.published_at ?? "未知"}；更新：${row.source.updated_at ?? "未知"}；获取：${row.source.fetched_at}\n${row.source.applicability}`),
          "相关文字不保证回答了全部问题，也不保证今天适用；请查看官方原文。"].join("\n\n") : "未找到相关资料；不能据此认定免费、无需预约或可以入场。"); return;
      }
      if (["list_saved", "read_saved", "restore_saved"].includes(action.name)) {
        if (action.name === "list_saved") await history.load(null, Number(action.arguments.offset));
        else {
          const h = history.readState(), chosen = resolveDialogueSelector(h.list?.items ?? (h.detail ? [h.detail] : []), action.arguments.selector);
          if (!chosen) { reply("请先回复“查看历史”，再用准确记录 ID、名称或列表编号选择；同名不会自动选第一份。"); return; }
          await history.load(chosen.id);
        }
        if (!valid()) return;
        const h = history.readState();
        if (h.error) { reply(h.error); return; }
        if (action.name === "restore_saved" && h.detail) {
          const restored = restoreSavedTrip(h.detail, crypto.randomUUID()); install(restored.inputs, `历史快照“${h.detail.name}”，保存于 ${h.detail.saved_at}`);
          reply("已创建独立规划输入，原存档不变，历史结果没有成为新正式草案。请核对设置后主动生成；旧 v1 地点缺少城市归属时须先编辑并重新确认。当前对话需求不会伪装成已同步历史输入。");
        } else reply(h.detail ? historyDialogueText(h.detail) : h.list ? historyDialogueText(h.list) : "未取得历史记录。");
        return;
      }
      const p = planner.current;
      if (!p) { reply("还没有当前规划输入。先把日期、人数、预算、交通方式聊清楚，搜索并确认住宿／必去，再回复“准备规划”。也可从历史创建副本。"); return; }
      const frozenRetry = action.name === "save_trip" && p.saving?.readState().status === "uncertain";
      if (!planningAllowed() && !frozenRetry) { reply("需求或已确认地点已变化，旧正式草案已经失效。请回复“准备规划”，核对并应用新输入后再主动生成；不会使用旧城市、地点或费用结果。"); return; }
      const result = await p.execute(action, controller.signal);
      if (!valid()) return;
      if (result.replacement) install(result.replacement, prepared?.origin ?? "当前输入");
      if (result.restored) install(result.restored.inputs, `历史快照“${result.restored.source.name}”`);
      if (result.view === "map") { const capture = p.schedule.capture(); if (capture) setMap({ key: crypto.randomUUID(), date: result.date, edgeId: result.edgeId, capture }); }
      else if (map && p.schedule.capture()?.version !== map.capture.version) setMap(null);
      reply(result.text);
    } catch (error) { reply(error instanceof Error ? error.message : "这项操作未完成，没有生成替代事实。请核对后主动处理。"); }
    finally { if (active.current === controller) {
      const screens: Record<string, DialogueSelectionContext["display"]> = { search_places: "places", preview_place: "places", confirm_place: "places", prepare_candidates: "candidates", exclude_candidate: "candidates", restore_candidate: "candidates", generate_schedule: "schedule", read_schedule: "schedule", read_budget: "budget", set_cost: "budget", read_preview: "preview", preview_exclusion: "preview", list_saved: "history", read_saved: "history" };
      if (screens[action.name]) display.current = screens[action.name];
      active.current = null; onBusy(false);
    } }
  }
  async function offer(action: DialogueAction, explicit = false) {
    if (!allowed() || active.current) return;
    confirmation.current.clear(); selectionToken.current = undefined;
    if (action.name === "cancel_operation") { cancel(); return; }
    if (action.name === "confirm_place") {
      if (explicit) { void execute(action, true); return; }
      const revision = conversation.revision, version = ++serial.current, controller = new AbortController();
      active.current = controller; onBusy(true);
      const ready = await places.ensureReady(false);
      if (active.current !== controller || serial.current !== version || controller.signal.aborted || !allowed() || current.current.conversation.revision !== revision) return;
      active.current = null; onBusy(false);
      const preview = places.preview(Number(action.arguments.number), ready.workspace);
      if (!preview.previewToken) { onMessage("assistant", preview.text); return; }
      selectionToken.current = preview.previewToken;
      confirmation.current.offer(action, binding());
      onMessage("assistant", `${preview.text}\n请回复“确认”或“取消”本次操作。`); return;
    }
    // Opening a media picker may need a city sync, never hide that write behind a read.
    const needs = needsActionConfirmation(action) || ["open_photo", "open_video"].includes(action.name) && !places.canUse();
    if (!needs) { void execute(action); return; }
    confirmation.current.offer(action, binding());
    const city = chatCity(conversation.requirements.destination_id)?.name;
    onMessage("assistant", `${actionDescription(action)}${["search_places", "prepare_planning", "open_photo", "open_video"].includes(action.name) ? `\n按当前需求城市（${city ?? "尚未明确"}）读取并同步地点；切城会清除不相容的旧地点。` : ""}\n请回复“确认”或“取消”。未确认前不执行。`);
  }
  function submit(text: string): boolean {
    const answer = confirmationReply(text), token = confirmation.current.currentToken();
    if (answer) {
      onMessage("user", text);
      if (answer === "cancel") { cancel(); return true; }
      const action = token && confirmation.current.take(token, binding());
      if (!action) onMessage("assistant", "没有与当前上下文匹配的待确认操作，请重新提出要求。");
      else void execute(action, true);
      return true;
    }
    if (["编辑需求", "修改需求"].includes(text.trim())) {
      onMessage("user", text); confirmation.current.clear();
      if (!planner.current) onMessage("assistant", "先确认并准备当前规划输入，再打开完整编辑；尚未准备时可以直接在聊天中补充需求。");
      else { setEditing(planner.current.readInputs()); setMedia(null); }
      return true;
    }
    const action = localDialogueAction(text);
    if (!action) return false;
    onMessage("user", text); void offer(action, true); return true;
  }
  function selectionContext(): DialogueSelectionContext {
    const p = planner.current, kind = display.current;
    let items: Omit<DialogueSelectionContext["items"][number], "ordinal">[] = [];
    if (kind === "places") items = (places.current()?.batch?.places ?? []).map(place => ({ id: place.id, name: place.name, type: "place" }));
    else if (kind === "history") { const h = history.readState(); items = (h.list?.items ?? (h.detail ? [h.detail] : [])).map(row => ({ id: row.id, name: row.name, type: "saved" })); }
    else if (p && planningAllowed()) {
      if (kind === "candidates") items = candidateItems(p.readInputs().candidates).filter(row => row.role === "optional").map(row => ({ id: row.place.id, name: row.place.name, type: "place" }));
      else {
        const response = kind === "preview" ? p.adjustment.readState().response : p.schedule.capture()?.original;
        if (response) {
          const byId = new Map([...response.request.must_visit_places, ...response.request.optional_places ?? []].map(place => [place.id, place]));
          const visited = [...new Set(response.days.flatMap(day => day.items.filter(item => item.kind === "visit").map(item => item.place_id!)))];
          items = visited.flatMap(id => { const place = byId.get(id); return place ? [{ id, name: place.name, type: "place" as const }] : []; });
          items.push(...response.days.map(day => ({ id: day.date, name: day.date, type: "date" as const })));
        }
      }
    }
    return { workspace_version: crypto.randomUUID(), display: kind, items: items.slice(0, 20).map((row, index) => ({ ...row, ordinal: index + 1 })).filter(row => row.id.length <= 120 && row.name.length <= 120) };
  }
  useImperativeHandle(handle, () => ({ submit, cancel: () => cancel(), beforeModel: () => cancel(true), selectionContext }));
  useEffect(() => {
    if (blocked || !proposal || proposal.conversation !== conversation.id || proposal.revision !== conversation.revision ||
      proposal.operation !== conversation.last_turn?.operation_id || consumed.current === proposal.operation) return;
    consumed.current = proposal.operation;
    if (proposal.error) onMessage("assistant", proposal.error);
    // Receipt arrival is an external event; only this previously unseen, bound
    // operation may publish its pending-confirmation UI.
    else if (proposal.action) {
      const action = bindDialogueSelection(proposal.action, proposal.context);
      // eslint-disable-next-line react-hooks/set-state-in-effect
      if (action) void offer(action);
      else onMessage("assistant", "这个编号不能对应到提问时的地点或历史列表，未执行。请先查看对应列表，再用明确名称或编号选择。");
    }
    // Proposal identity, not an ordinary render, is the trigger. Execution callbacks
    // use this committed render; dependencies intentionally include their context.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [proposal, blocked, conversation.id, conversation.revision]);
  const mediaCity = DESTINATIONS.find(city => city.adcode === places.state.workspace?.city_id)?.id;
  return <>
    {prepared && <Planner key={prepared.key} prepared={prepared} available={planningAllowed} retryAllowed={allowed} controllerRef={planner} />}
    {media && mediaCity && <DialogueMedia mode={media} sessionKey={`${conversation.id}:${conversation.revision}`} destinationId={mediaCity} places={places}
      onClose={() => { places.cancel(); setMedia(null); }} onResult={text => onMessage("assistant", `${text}\n已确认地点变化后旧正式草案失效。请核对新的规划输入，再主动生成；尚未采用或保存新行程。`)} />}
    {map && <DialogueMap key={`${prepared?.key}:${map.capture.version}`} capture={map.capture} selection={map} isCurrent={() => planner.current?.schedule.capture()?.version === map.capture.version}
      onClose={() => setMap(null)} />}
    {editing && prepared && <div className="fixed inset-0 z-50 overflow-y-auto bg-black/25 p-3 sm:p-8"><section role="dialog" aria-modal="true" aria-label="编辑规划需求" className="mx-auto max-w-3xl rounded-2xl bg-white p-4">
      <p className="mb-3 text-sm">{prepared.origin}。这里编辑的是本页规划副本；取消保留原草案、费用和地图。不会自动重生成或保存。</p>
      <TripRequestForm initialRequest={editing.request} onCancel={() => setEditing(null)} onConfirm={request => {
        const p = planner.current; if (!p || !allowed()) return;
        try {
          requireResolvedSave();
          const result = applyEditedRequirements(p.readInputs(), request); install(result.inputs, "明确确认的编辑副本"); setEditing(null);
          onMessage("assistant", ["已应用编辑，旧正式草案已失效；请主动重新生成。聊天中的需求摘要尚未自动改写，后续“准备规划”将使用对话需求，请勿混用。", ...result.notices].join("\n"));
        } catch (error) { onMessage("assistant", error instanceof Error ? error.message : "未应用编辑，原输入保留。"); }
      }} /><button type="button" className={button} onClick={() => setEditing(null)}>取消整次编辑</button>
    </section></div>}
  </>;
}
