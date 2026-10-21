"use client";

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { authSnapshot } from "./auth-session";
import type { ChatConversation } from "./chat";
import { chatCity, resolveChatCity } from "./chat-cities";
import { requestPlaces, placesError, type PlaceRole, type PlacesAction, type PlacesRequest, type PlacesWorkspace } from "./chat-places";
import type { PlacesPlanningAvailability } from "./chat-places-controller";

export type DialoguePlaceReply = { text: string; workspace?: PlacesWorkspace; previewToken?: string };
export type DialoguePlaceAction = { name: string; arguments: Record<string, unknown> };
export type DialogueMediaSource = { signal: AbortSignal; isCurrent: () => boolean };
type Preview = { token: string; batchId: string; placeId: string; index: number; revision: number; role: PlaceRole };
type View = { workspace: PlacesWorkspace | null; pending: boolean; needsRead: boolean; error: string | null; preview: Preview | null };
type Context = { id: string | null; revision: number; blocked: boolean; epoch: number; city: string | null };
type Work = { context: Context; version: number; controller: AbortController; command?: PlacesRequest };
const empty: View = { workspace: null, pending: false, needsRead: false, error: null, preview: null };
const currentSource = (source: DialogueMediaSource | null) => !source || !source.signal.aborted && source.isCurrent();

export function describeDialoguePlaces(workspace: PlacesWorkspace): string {
  const city = chatCity(workspace.city_id);
  return [`地点准备：${city?.name ?? "尚未确认城市"}。`,
    `住宿：${workspace.accommodation ? `${workspace.accommodation.name}；${workspace.accommodation.address ?? "上游未提供地址"}` : "尚未确认"}。`,
    workspace.required_places.length ? `必去（按确认顺序）：\n${workspace.required_places.map((p, i) => `${i + 1}. ${p.name}；${p.address ?? "上游未提供地址"}`).join("\n")}` : "必去：尚未确认。",
    workspace.stale ? "需求已变化，请明确同步地点后再搜索或确认。" : "这些是已确认的真实地点，尚不代表已生成或保存行程。"].join("\n");
}
export function describeDialogueCandidates(workspace: PlacesWorkspace): string {
  const batch = workspace.batch;
  if (!batch) return "本次没有可确认的地点候选。";
  if (!batch.places.length) return `没有查到“${batch.keyword}”的可用地点，请调整关键词后主动搜索。`;
  return [`在${chatCity(batch.city_id)?.name ?? "当前城市"}找到以下${batch.role === "accommodation" ? "住宿参考点" : "必去地点"}候选：`,
    ...batch.places.map((p, i) => `${i + 1}. ${p.name}\n   ${p.address ?? "上游未提供地址"}`),
    `来源：高德；查询时间：${batch.queried_at}。`,
    "请先回复“预览地点 1”核对地址，再回复“确认地点 1”。仅搜索或预览不会加入需求。"].join("\n");
}

/** Headless dialogue adapter. The existing strict Places API remains the sole POI authority. */
export function useDialoguePlaces(conversation: ChatConversation | null, blocked = false,
  onAvailability?: (value: PlacesPlanningAvailability) => void) {
  const epoch = authSnapshot().epoch;
  const city = chatCity(conversation?.requirements.destination_id ?? null)?.id ?? null;
  const context = useMemo<Context>(() => ({ id: conversation?.id ?? null, revision: conversation?.revision ?? 0, blocked, epoch, city }),
    [conversation?.id, conversation?.revision, blocked, epoch, city]);
  const [rendered, setRendered] = useState<{ context: Context; value: View }>({ context, value: empty });
  const view = rendered.context.id === context.id && rendered.context.revision === context.revision && rendered.context.epoch === context.epoch ? rendered.value : empty;
  const current = useRef(view), binding = useRef(context), active = useRef<Work | null>(null), version = useRef(0), alive = useRef(true);
  const source = useRef<DialogueMediaSource | null>(null), notify = useRef(onAvailability);
  const publish = useCallback((patch: Partial<View>) => {
    if (!alive.current) return;
    current.current = { ...current.current, ...patch };
    setRendered({ context: binding.current, value: current.current });
  }, []);
  const isBound = () => alive.current && binding.current === context && authSnapshot().epoch === context.epoch && !context.blocked;
  const canUse = useCallback(() => {
    const c = binding.current, s = current.current, w = s.workspace;
    return alive.current && c.epoch === authSnapshot().epoch && !c.blocked && !active.current && !s.needsRead && !!w && !w.stale &&
      w.conversation_id === c.id && w.intent_revision === c.revision && !["running", "uncertain"].includes(w.last_operation?.state ?? "");
  }, []);
  const abort = useCallback(() => {
    const previous = active.current;
    version.current++; active.current = null; previous?.controller.abort();
    if (previous?.command?.action === "search" && previous.context.epoch === authSnapshot().epoch && previous.context.id) {
      const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 5000);
      void requestPlaces(previous.context.id, controller.signal, { action: "cancel", operation_id: previous.command.operation_id,
        expected_intent_revision: previous.command.expected_intent_revision, expected_revision: previous.command.expected_revision + 1 })
        .catch(() => undefined).finally(() => clearTimeout(timer));
    }
    return previous;
  }, []);
  useLayoutEffect(() => {
    const prior = binding.current;
    const sameIdentity = prior.id === context.id && prior.revision === context.revision && prior.epoch === context.epoch;
    if (!sameIdentity) {
      current.current = empty; source.current = null;
    }
    binding.current = context;
    if (sameIdentity && prior !== context) publish({ pending: false, preview: null });
    return () => {
      const previous = abort();
      if (previous) current.current = { ...current.current, pending: false, preview: null, needsRead: current.current.needsRead || !!previous.command };
    };
  }, [context, abort, publish]);
  useLayoutEffect(() => { notify.current = onAvailability; }, [onAvailability]);
  useEffect(() => {
    notify.current?.({ conversationId: context.id ?? "", workspace: view.workspace, pending: view.pending,
      needsRead: view.needsRead, available: canUse(), canUse });
  }, [context.id, view, canUse]);
  useEffect(() => { alive.current = true; return () => { alive.current = false; abort(); }; }, [abort]);

  async function run(action?: PlacesAction, mediaSource?: DialogueMediaSource): Promise<DialoguePlaceReply> {
    const base = current.current.workspace;
    if (!isBound() || !context.id || active.current) return { text: "当前操作尚未完成，或会话已经变化，请稍后核对。" };
    if (action && (!base || current.current.needsRead || action.action !== "sync" && !canUse()))
      return { text: "请先读取并同步当前地点准备，旧结果不能继续确认。" };
    if (mediaSource && !currentSource(mediaSource)) return { text: "媒体画面已变化，请重新选择并核对线索。" };
    const command = action && base ? { ...action, operation_id: crypto.randomUUID(), expected_intent_revision: context.revision, expected_revision: base.revision } : undefined;
    const work: Work = { context, version: ++version.current, controller: new AbortController(), command };
    active.current = work; source.current = mediaSource ?? null;
    publish({ pending: true, error: null, preview: null });
    const cancelSource = () => { if (active.current === work) { abort(); publish({ pending: false, needsRead: true, preview: null }); } };
    mediaSource?.signal.addEventListener("abort", cancelSource, { once: true });
    const valid = () => isBound() && active.current === work && work.version === version.current && !work.controller.signal.aborted && currentSource(mediaSource ?? null);
    try {
      const workspace = await requestPlaces(context.id, work.controller.signal, command);
      if (!valid()) return { text: "操作上下文已变化，旧结果未应用。" };
      const uncertain = workspace.last_operation?.state === "uncertain", failed = ["failed", "uncertain"].includes(workspace.last_operation?.state ?? "");
      const error = failed ? placesError(workspace.last_operation?.error_code ?? null) : null;
      publish({ workspace, needsRead: uncertain, error });
      return { workspace, text: error ?? (action?.action === "search" ? describeDialogueCandidates(workspace) : describeDialoguePlaces(workspace)) };
    } catch (error) {
      if (!valid()) return { text: "操作已停止，旧响应未应用。" };
      const text = error instanceof Error ? error.message : "地点操作未完成，请重新读取。";
      publish({ error: text, needsRead: true }); return { text };
    } finally {
      mediaSource?.signal.removeEventListener("abort", cancelSource);
      if (valid()) { active.current = null; publish({ pending: false }); }
    }
  }
  const read = () => run();
  const sync = () => run({ action: "sync" });
  async function ensureReady(confirmed = false): Promise<DialoguePlaceReply> {
    if (!isBound() || !context.id) return { text: "请先进入当前有效会话。" };
    let result: DialoguePlaceReply = { text: "地点准备已读取。" };
    if (!current.current.workspace || current.current.needsRead) {
      result = await read();
      if (!isBound() || current.current.needsRead || !current.current.workspace) return result;
    }
    if (current.current.workspace.stale || current.current.workspace.intent_revision !== context.revision) {
      if (!confirmed) return { text: "需求已变化，请明确同意同步地点准备；城市变化时旧地点会清除。" };
      result = await sync();
      if (!isBound() || !canUse()) return result;
    }
    if (!context.city) return { text: "当前需求还没有明确的单城，请先确认城市后再准备地点。" };
    if (current.current.workspace?.city_id !== context.city) {
      if (!confirmed) return { text: "地点准备城市与当前需求尚未一致，请明确同意切换后再搜索。" };
      result = await run({ action: "set_city", city_id: context.city });
      if (!isBound() || !canUse()) return result;
    }
    const workspace = current.current.workspace;
    return workspace && canUse() ? { text: describeDialoguePlaces(workspace), workspace } : result;
  }
  function preview(index: number, freshlyRead?: PlacesWorkspace): DialoguePlaceReply {
    const base = current.current.workspace, candidate = base?.batch?.places[index - 1];
    const matchingSnapshot = freshlyRead ? freshlyRead === base : current.current === view;
    if (!isBound() || !matchingSnapshot || !canUse() || !currentSource(source.current) || !base?.batch || !Number.isInteger(index) || !candidate)
      return { text: "这个候选编号已失效或不存在，请重新搜索并核对当前候选。" };
    const next: Preview = { token: crypto.randomUUID(), batchId: base.batch.id, placeId: candidate.id, index, revision: base.revision, role: base.batch.role };
    publish({ preview: next });
    return { text: `候选${index}：${candidate.name}\n地址：${candidate.address ?? "上游未提供地址"}\n城市：${candidate.region.cityname}\n来源：高德；查询时间：${base.batch.queried_at}\n尚未加入。地址符合你的意图时，请回复“确认地点 ${index}”。`, previewToken: next.token };
  }
  async function confirm(index: number, token?: string): Promise<DialoguePlaceReply> {
    const base = current.current.workspace, p = current.current.preview;
    if (!isBound() || current.current !== view || !canUse() || !currentSource(source.current) || !base?.batch || !p || p.index !== index ||
      p.batchId !== base.batch.id || p.revision !== base.revision || token !== undefined && token !== p.token || !base.batch.places.some(place => place.id === p.placeId))
      return { text: "请先预览当前候选并核对地址；旧编号或旧确认不能用于新的搜索结果。" };
    if (p.role === "required" && (base.required_places.length >= 6 || base.required_places.some(place => place.id === p.placeId)))
      return { text: base.required_places.some(place => place.id === p.placeId) ? "这个真实地点已经在必去列表中，未重复加入。" : "必去地点已达六个，请先明确移除一个。" };
    return run({ action: "confirm", batch_id: p.batchId, place_id: p.placeId, role: p.role });
  }
  async function search(keyword: string, role: PlaceRole, mediaSource?: DialogueMediaSource) {
    if (!keyword.trim() || Array.from(keyword.trim()).length > 80 || !["required", "accommodation"].includes(role)) return { text: "请提供1～80字的地点名称，并说明是住宿还是必去。" };
    if (!current.current.workspace?.city_id) return { text: "请先明确确认地点准备城市，再搜索真实地点。" };
    return run({ action: "search", keyword: keyword.trim(), role }, mediaSource);
  }
  async function execute(action: DialoguePlaceAction): Promise<DialoguePlaceReply> {
    const args = action.arguments;
    switch (action.name) {
      case "read_places": case "inspect_places": return read();
      case "sync_places": return sync();
      case "set_place_city": case "confirm_place_city": {
        const city = typeof args.city_id === "string" ? chatCity(args.city_id) : typeof args.city === "string" ? resolveChatCity(args.city) : null;
        return city ? run({ action: "set_city", city_id: city.id }) : { text: "无法唯一确定城市，请提供明确的城市名称。" };
      }
      case "search_places": case "search_place": return typeof args.keyword === "string" && (args.role === "required" || args.role === "accommodation") ? search(args.keyword, args.role) : { text: "请明确搜索关键词及住宿／必去用途。" };
      case "preview_place": return preview(Number(args.number ?? args.index));
      case "confirm_place": return confirm(Number(args.number ?? args.index), typeof args.preview_token === "string" ? args.preview_token : undefined);
      case "remove_place": {
        const base = current.current.workspace;
        let role = args.role;
        let place = role === "accommodation" ? base?.accommodation : role === "required" && Number.isInteger(args.index) ? base?.required_places[Number(args.index) - 1] : null;
        if (typeof args.selector === "string" && base) {
          const selected = args.selector.trim(), candidates = [
            ...(base.accommodation ? [{ place: base.accommodation, role: "accommodation" as const }] : []),
            ...base.required_places.map((p, index) => ({ place: p, role: "required" as const, number: String(index + 1) })),
          ];
          const matches = candidates.filter(row => row.place.id === selected || row.place.name === selected || "number" in row && row.number === selected || row.role === "accommodation" && selected === "住宿");
          if (matches.length === 1) { place = matches[0].place; role = matches[0].role; }
        }
        return place && (role === "required" || role === "accommodation") ? run({ action: "remove", role, place_id: place.id }) : { text: "没有找到要移除的已确认地点，请先查看当前地点列表。" };
      }
      default: return { text: "这个地点操作尚不支持，未改变当前需求。" };
    }
  }
  function cancel(): DialoguePlaceReply {
    if (!isBound()) return { text: "会话已变化，旧操作未应用。" };
    const old = abort(); publish({ pending: false, preview: null, needsRead: current.current.needsRead || !!old?.command });
    return { text: "已停止等待。已发送操作可能已经完成，请主动读取核对，不会自动重发。" };
  }
  return { state: view, current: () => canUse() ? current.current.workspace : null, canUse, ensureReady, read, reload: read, sync, search, preview, confirm, execute, cancel };
}
export type DialoguePlacesController = ReturnType<typeof useDialoguePlaces>;
