"use client";

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { authSnapshot } from "./auth-session";
import type { ChatConversation } from "./chat";
import { chatCity } from "./chat-cities";
import { placesError, requestPlaces, type PlacesAction, type PlacesRequest, type PlacesWorkspace } from "./chat-places";

type View = { workspace: PlacesWorkspace | null; pending: boolean; error: string | null; notice: string | null; needsRead: boolean; dirty: boolean; previewId: string | null };
const initial: View = { workspace: null, pending: false, error: null, notice: null, needsRead: false, dirty: false, previewId: null };
type Context = { conversationId: string; intentRevision: number; open: boolean; blocked: boolean };
type Active = { controller: AbortController; generation: number; epoch: number; context: Context; command?: PlacesRequest };

export function useChatPlaces(conversation: ChatConversation, open: boolean, blocked: boolean) {
  const [state, setState] = useState<View>(initial);
  const rendered = useMemo<Context>(() => ({ conversationId: conversation.id, intentRevision: conversation.revision, open, blocked }),
    [conversation.id, conversation.revision, open, blocked]);
  const current = useRef(state), context = useRef<Context>(rendered);
  const alive = useRef(true), generation = useRef(0), active = useRef<Active | null>(null);
  const update = useCallback((patch: Partial<View>) => {
    if (!alive.current) return;
    current.current = { ...current.current, ...patch }; setState(current.current);
  }, []);
  const detachCancel = useCallback((work: Active | null) => {
    if (!work?.command || !["ask", "search"].includes(work.command.action) || work.epoch !== authSnapshot().epoch) return;
    const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 5000);
    void requestPlaces(work.context.conversationId, controller.signal, { action: "cancel", operation_id: work.command.operation_id,
      expected_intent_revision: work.command.expected_intent_revision, expected_revision: work.command.expected_revision + 1 })
      .catch(() => undefined).finally(() => clearTimeout(timer));
  }, []);
  const stop = useCallback((notify = true) => {
    const old = active.current; generation.current += 1; active.current = null; old?.controller.abort();
    if (notify) detachCancel(old);
  }, [detachCancel]);
  const valid = useCallback((work: Active) => alive.current && active.current === work && work.generation === generation.current &&
    !work.controller.signal.aborted && work.context === context.current && work.epoch === authSnapshot().epoch, []);
  useLayoutEffect(() => {
    context.current = rendered;
    return () => { stop(true); };
  }, [rendered, stop]);

  const load = useCallback(async () => {
    const bound = context.current;
    if (!alive.current || !bound.open || bound.blocked || active.current) return;
    const work: Active = { context: bound, generation: ++generation.current, epoch: authSnapshot().epoch, controller: new AbortController() };
    active.current = work; update({ pending: true, error: null, previewId: null });
    try {
      const workspace = await requestPlaces(bound.conversationId, work.controller.signal);
      if (valid(work)) update({ workspace, dirty: false, needsRead: false,
        error: ["failed", "uncertain"].includes(workspace.last_operation?.state ?? "") ? placesError(workspace.last_operation?.error_code ?? null) : null,
        notice: workspace.stale ? "需求已改变；请先核对并同步地点准备，旧候选不能确认。" : workspace.last_operation?.state === "uncertain" ?
          "已读取服务端收据，但旧操作结果仍未知，不会重发或退额。若你再次明确点击查询，将是新的请求并重新计额；不会把它当作旧请求恢复。" : null });
    } catch (error) { if (valid(work)) update({ error: error instanceof Error ? error.message : "无法读取地点准备。", needsRead: true }); }
    finally { if (valid(work)) { active.current = null; update({ pending: false }); } }
  }, [update, valid]);
  useEffect(() => {
    alive.current = true;
    stop(true); update({ pending: false, previewId: null, dirty: true });
    if (open && !blocked) void load();
    return () => { stop(true); };
  }, [conversation.id, conversation.revision, open, blocked, load, stop, update]);
  useEffect(() => () => { alive.current = false; stop(true); }, [stop]);

  const usable = state.workspace !== null && state.workspace.conversation_id === conversation.id &&
    state.workspace.intent_revision === conversation.revision && !state.workspace.stale;
  function edit() {
    if (current.current !== state || context.current !== rendered) return;
    const pending = active.current !== null;
    stop(true); update({ pending: false, dirty: true, previewId: null, needsRead: state.needsRead || pending, error: null });
  }
  function preview(id: string) {
    if (current.current !== state || context.current !== rendered || active.current || !usable || state.dirty || state.needsRead ||
      !state.workspace?.batch?.places.some(place => place.id === id)) return;
    update({ previewId: id });
  }
  async function act(action: PlacesAction) {
    const base = state.workspace;
    if (current.current !== state || context.current !== rendered || !base || !open || blocked || active.current || state.needsRead ||
      base.conversation_id !== conversation.id || action.action !== "sync" && !usable) return;
    if (action.action === "set_city" && !chatCity(action.city_id)) return;
    if (action.action === "confirm") {
      if (state.dirty || state.previewId !== action.place_id || base.batch?.id !== action.batch_id || base.batch.role !== action.role ||
        !base.batch.places.some(place => place.id === action.place_id) || action.role === "required" &&
        (base.required_places.length >= 6 || base.required_places.some(place => place.id === action.place_id))) return;
    }
    if (action.action === "search" && (!base.city_id || !action.keyword.trim() || Array.from(action.keyword.trim()).length > 80)) return;
    if (action.action === "ask" && (!action.text.trim() || Array.from(action.text).length > 2000)) return;
    const command: PlacesRequest = { ...action, operation_id: crypto.randomUUID(), expected_intent_revision: conversation.revision, expected_revision: base.revision };
    const work: Active = { context: rendered, generation: ++generation.current, epoch: authSnapshot().epoch, controller: new AbortController(), command };
    active.current = work; update({ pending: true, previewId: null, error: null, notice: null, dirty: true });
    try {
      const workspace = await requestPlaces(conversation.id, work.controller.signal, command);
      if (!valid(work)) return;
      const failed = ["failed", "uncertain"].includes(workspace.last_operation?.state ?? "");
      update({ workspace, needsRead: workspace.last_operation?.state === "uncertain", dirty: false,
        error: failed ? placesError(workspace.last_operation?.error_code ?? null) : null,
        notice: !failed && action.action === "confirm" ? "这个真实地点已加入当前地点准备；尚未生成或保存行程。" : null });
    } catch (error) { if (valid(work)) update({ error: error instanceof Error ? error.message : "操作未完成。", needsRead: true }); }
    finally { if (valid(work)) { active.current = null; update({ pending: false }); } }
  }
  async function cancel() {
    if (current.current !== state || context.current !== rendered) return;
    const work = active.current;
    stop(true);
    update({ pending: false, needsRead: !!work?.command, dirty: true, previewId: null,
      notice: "已停止本页等待。已发送请求无法保证停止处理或计费；请重新读取服务端记录，不会自动重发。" });
    if (!work && state.workspace?.last_operation?.state === "running") {
      const base = state.workspace;
      const cancellation: Active = { context: rendered, generation: ++generation.current, epoch: authSnapshot().epoch,
        controller: new AbortController() };
      active.current = cancellation; update({ pending: true });
      try {
        const workspace = await requestPlaces(conversation.id, cancellation.controller.signal, { action: "cancel",
          operation_id: base.last_operation!.operation_id, expected_revision: base.revision, expected_intent_revision: conversation.revision });
        if (valid(cancellation)) update({ workspace, needsRead: false });
      } catch { if (valid(cancellation)) update({ needsRead: true, error: "尚未确认服务端取消结果，请重新读取核对，不会重发原查询。" }); }
      finally { if (valid(cancellation)) { active.current = null; update({ pending: false }); } }
    }
  }
  return { state, usable, edit, preview, act, cancel, reload: load };
}
