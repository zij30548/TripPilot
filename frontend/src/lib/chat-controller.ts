"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { authSnapshot } from "./auth-session";
import {
  cancelChatTurn, chatErrorMessage, confirmChatRequirements, createChatConversation, deleteChatConversation,
  listChatConversations, readChatConversation, sendChatTurn, type ChatConversation, type ChatSummary,
} from "./chat";

export type ChatWorkspaceState = {
  conversation: ChatConversation | null;
  items: ChatSummary[];
  nextOffset: number | null;
  listing: boolean;
  pending: "reading" | "sending" | "confirming" | "deleting" | "cancelling" | null;
  pendingText: string | null;
  draft: string;
  error: string | null;
  notice: string | null;
  needsRefresh: boolean;
};
const initial: ChatWorkspaceState = { conversation: null, items: [], nextOffset: null, listing: true, pending: null, pendingText: null, draft: "", error: null, notice: null, needsRefresh: false };
type Active = { version: number; controller: AbortController; kind: ChatWorkspaceState["pending"]; conversationId?: string; operationId?: string };

/** UI version and server revision have different jobs: ABA vs persisted conflicts. */
export function useChatWorkspace() {
  const [state, setState] = useState<ChatWorkspaceState>(initial);
  const current = useRef(state), alive = useRef(true), version = useRef(0), active = useRef<Active | null>(null);
  const listing = useRef<{ version: number; controller: AbortController } | null>(null), listVersion = useRef(0);
  const epoch = useRef(authSnapshot().epoch);
  const creation = useRef<string | null>(null);
  const update = useCallback((patch: Partial<ChatWorkspaceState>) => {
    if (!alive.current) return;
    current.current = { ...current.current, ...patch };
    setState(current.current);
  }, []);
  const invalidateList = useCallback(() => {
    listVersion.current += 1;
    listing.current?.controller.abort();
    listing.current = null;
    update({ listing: false });
  }, [update]);
  const upsert = useCallback((conversation: ChatConversation) => {
    // A list read predating a local write must not remove the new summary or
    // resurrect a deleted one when its transport ignores cancellation.
    invalidateList();
    const item = { id: conversation.id, title: conversation.title, revision: conversation.revision, updated_at: conversation.updated_at };
    return [item, ...current.current.items.filter(existing => existing.id !== item.id)].slice(0, 50);
  }, [invalidateList]);
  const cancelDetached = useCallback((request: Active | null) => {
    if (request?.kind !== "sending" || !request.operationId || !request.conversationId || epoch.current !== authSnapshot().epoch) return;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 5000);
    // This receipt is never applied to the selected conversation. No model retry.
    void cancelChatTurn(request.conversationId, request.operationId, controller.signal).catch(() => undefined).finally(() => clearTimeout(timer));
  }, []);
  const stop = useCallback((notifyServer: boolean) => {
    const previous = active.current;
    version.current += 1;
    active.current = null;
    previous?.controller.abort();
    if (notifyServer) cancelDetached(previous);
  }, [cancelDetached]);
  const begin = (kind: Active["kind"], conversationId?: string, operationId?: string): Active => {
    stop(false);
    const request = { version: version.current, controller: new AbortController(), kind, conversationId, operationId };
    active.current = request;
    return request;
  };
  const valid = (request: Active) => alive.current && active.current === request && version.current === request.version && !request.controller.signal.aborted && epoch.current === authSnapshot().epoch;
  const finish = (request: Active) => { if (valid(request)) { active.current = null; update({ pending: null }); } };
  const loadList = useCallback(async (offset = 0) => {
    if (!alive.current || epoch.current !== authSnapshot().epoch) return;
    listing.current?.controller.abort();
    const request = { version: ++listVersion.current, controller: new AbortController() };
    listing.current = request;
    update({ listing: true });
    const isCurrent = () => alive.current && listing.current === request && listVersion.current === request.version && !request.controller.signal.aborted && epoch.current === authSnapshot().epoch;
    try {
      const result = await listChatConversations(offset, request.controller.signal);
      if (!isCurrent()) return;
      const items = offset === 0 ? result.items : [...current.current.items, ...result.items.filter(item => !current.current.items.some(existing => existing.id === item.id))];
      update({ items, nextOffset: result.next_offset });
    } catch (error) {
      if (isCurrent()) update({ error: error instanceof Error ? error.message : "无法读取会话列表。" });
    } finally { if (isCurrent()) { listing.current = null; update({ listing: false }); } }
  }, [update]);
  useEffect(() => {
    alive.current = true;
    void loadList();
    return () => { alive.current = false; stop(true); listing.current?.controller.abort(); listing.current = null; };
  }, [loadList, stop]);

  function newConversation() {
    if (current.current !== state) return;
    stop(true);
    // A new chat is only persisted when its first message is explicitly sent.
    creation.current = null;
    update({ conversation: null, draft: "", pending: null, pendingText: null, error: null, notice: null, needsRefresh: false });
  }
  async function selectConversation(id: string) {
    if (current.current !== state) return;
    stop(true);
    const request = begin("reading", id);
    creation.current = null;
    update({ conversation: null, draft: "", pending: "reading", pendingText: null, error: null, notice: null, needsRefresh: false });
    try {
      const result = await readChatConversation(id, request.controller.signal);
      if (!valid(request)) return;
      update({ conversation: result, items: upsert(result), needsRefresh: result.last_turn?.state === "uncertain" });
    } catch (error) { if (valid(request)) update({ error: error instanceof Error ? error.message : "无法读取这份会话。" }); }
    finally { finish(request); }
  }
  async function refreshConversation() {
    const base = state.conversation;
    if (current.current !== state || !base || active.current) return;
    const request = begin("reading", base.id);
    update({ pending: "reading", error: null, notice: null });
    try {
      const result = await readChatConversation(base.id, request.controller.signal);
      if (!valid(request)) return;
      update({ conversation: result, items: upsert(result), pendingText: null, needsRefresh: result.last_turn?.state === "uncertain",
        notice: result.last_turn?.state === "uncertain" ? "上次发送结果不确定，已保留记录。本轮不会自动重发，可新建会话继续。" : "已读取服务端保存的最新会话；没有调用模型。" });
    } catch (error) { if (valid(request)) update({ error: error instanceof Error ? error.message : "读取未完成。", needsRefresh: true }); }
    finally { finish(request); }
  }
  async function recoverCreation() {
    const operationId = creation.current;
    if (current.current !== state || !operationId || state.conversation || active.current) return;
    const request = begin("reading");
    update({ pending: "reading", error: null });
    try {
      // Explicit retry reuses the frozen creation key. No turn/model is sent.
      const result = await createChatConversation(operationId, request.controller.signal);
      if (!valid(request)) return;
      creation.current = null;
      update({ conversation: result, items: upsert(result), needsRefresh: false, draft: state.pendingText ?? state.draft,
        pendingText: null, notice: "会话已核对，还未调用模型。请确认输入后再主动发送。" });
    } catch (error) { if (valid(request)) update({ error: error instanceof Error ? error.message : "会话创建结果仍未确认。", needsRefresh: true }); }
    finally { finish(request); }
  }
  async function preparePlaces(): Promise<boolean> {
    if (current.current !== state || active.current || state.needsRefresh || epoch.current !== authSnapshot().epoch) return false;
    if (state.conversation) return true;
    creation.current ??= crypto.randomUUID();
    const request = begin("reading");
    update({ pending: "reading", error: null, notice: null });
    try {
      const response = await createChatConversation(creation.current, request.controller.signal);
      if (!valid(request)) return false;
      creation.current = null;
      update({ conversation: response, items: upsert(response), needsRefresh: false, notice: "已创建你的空白会话，尚未调用模型。可以主动准备真实地点。" });
      return true;
    } catch (error) {
      if (valid(request)) update({ error: error instanceof Error ? error.message : "创建结果尚未确认。", needsRefresh: true });
      return false;
    } finally { finish(request); }
  }
  function editDraft(value: string) {
    if (!alive.current || epoch.current !== authSnapshot().epoch) return;
    update({ draft: value });
  }
  async function send() {
    if (current.current !== state || active.current || state.needsRefresh || epoch.current !== authSnapshot().epoch) return;
    const text = state.draft.trim();
    if (!text || Array.from(text).length > 2000 || /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(text)) {
      update({ error: "请输入1～2000字旅行需求，不接受控制字符。" }); return;
    }
    if (["running", "uncertain"].includes(state.conversation?.last_turn?.state ?? "")) return;
    const operationId = crypto.randomUUID();
    const request = begin("sending", state.conversation?.id, operationId);
    update({ pending: "sending", pendingText: text, error: null, notice: null, draft: "" });
    try {
      let base = state.conversation;
      if (!base) {
        creation.current ??= crypto.randomUUID();
        base = await createChatConversation(creation.current, request.controller.signal);
        if (!valid(request)) return;
        request.conversationId = base.id;
        creation.current = null;
        update({ conversation: base, items: upsert(base) });
      }
      const response = await sendChatTurn(base.id, { operation_id: operationId, expected_revision: base.revision, text }, request.controller.signal);
      if (!valid(request)) return;
      update({ conversation: response, items: upsert(response), pendingText: null,
        needsRefresh: response.last_turn?.state === "uncertain",
        error: response.last_turn?.state === "failed" ? chatErrorMessage(response.last_turn.error_code) : null });
    } catch (error) {
      if (valid(request)) update({ error: error instanceof Error ? error.message : "本次对话未完成。", needsRefresh: true });
    } finally { finish(request); }
  }
  async function cancel() {
    if (current.current !== state) return;
    const previous = active.current;
    const base = state.conversation;
    const operationId = previous?.kind === "sending" ? previous.operationId : base?.last_turn?.state === "running" ? base.last_turn.operation_id : null;
    const id = previous?.conversationId ?? base?.id;
    stop(false);
    update({ pending: null, pendingText: null, notice: "已取消本页等待；无法保证已经发送的上游请求停止处理或计费。", needsRefresh: !!operationId, error: null });
    if (!operationId || !id) return;
    const request = begin("cancelling", id, operationId);
    update({ pending: "cancelling" });
    try {
      const response = await cancelChatTurn(id, operationId, request.controller.signal);
      if (valid(request)) update({ conversation: response, items: upsert(response), needsRefresh: response.last_turn?.state === "uncertain" });
    } catch { if (valid(request)) update({ needsRefresh: true, error: "尚未确认服务端取消结果。请重新读取会话；不会重复发送原消息。" }); }
    finally { finish(request); }
  }
  async function confirm() {
    const base = state.conversation;
    if (current.current !== state || !base || active.current || state.needsRefresh || !canConfirmRequirements(base)) return;
    const request = begin("confirming", base.id, crypto.randomUUID());
    update({ pending: "confirming", error: null, notice: null });
    try {
      const response = await confirmChatRequirements(base.id, base.revision, request.operationId!, request.controller.signal);
      if (valid(request)) update({ conversation: response, items: upsert(response), notice: "已确认这一版需求意向。地点名称仍需后续真实搜索确认，尚未生成行程。" });
    } catch (error) { if (valid(request)) update({ error: error instanceof Error ? error.message : "确认未完成。", needsRefresh: true }); }
    finally { finish(request); }
  }
  async function remove() {
    const base = state.conversation;
    if (current.current !== state || !base || active.current) return;
    const request = begin("deleting", base.id);
    update({ pending: "deleting", error: null, notice: null });
    try {
      await deleteChatConversation(base.id, base.revision, request.controller.signal);
      if (valid(request)) {
        invalidateList();
        update({ conversation: null, items: current.current.items.filter(item => item.id !== base.id), draft: "", pendingText: null, needsRefresh: false, notice: "这份会话和需求意向已删除，未修改历史行程存档。" });
      }
    } catch (error) { if (valid(request)) update({ error: error instanceof Error ? error.message : "删除未完成，请重新读取确认。", needsRefresh: true }); }
    finally { finish(request); }
  }
  return { state, editDraft, send, cancel, confirm, remove, newConversation, selectConversation, refreshConversation, recoverCreation, preparePlaces, loadMore: () => state.nextOffset !== null && loadList(state.nextOffset), reloadList: () => loadList() };
}

export function missingRequirements(conversation: ChatConversation): string[] {
  const r = conversation.requirements;
  return [!r.destination_id && "目的地", (!r.start_date || !r.end_date) && "完整日期", !r.travelers && "人数", r.budget_cny === null && "全程总预算", !r.accommodation_hint && "住宿参考名称"].filter((value): value is string => typeof value === "string");
}
export function canConfirmRequirements(conversation: ChatConversation): boolean {
  return missingRequirements(conversation).length === 0 && conversation.last_turn?.state === "succeeded" &&
    !conversation.issues.some(issue => issue !== "unimplemented_preference") && conversation.confirmed_revision !== conversation.revision;
}
