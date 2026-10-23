"use client";

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { authSnapshot } from "./auth-session";
import { requestPreferences, type PreferenceView } from "./assistant-preferences";
import type { DialoguePlaceAction, DialoguePlaceReply } from "./dialogue-places-controller";

/** Explicit commands only; messages and inferred preferences are never auto-saved. */
export function useDialoguePreferences(conversationId: string | null) {
  const epoch = authSnapshot().epoch;
  const context = useMemo(() => ({ id: conversationId, epoch }), [conversationId, epoch]);
  const binding = useRef(context), version = useRef(0), alive = useRef(true);
  const active = useRef<{ controller: AbortController; version: number; writing: boolean } | null>(null);
  const data = useRef<PreferenceView | null>(null), needsRead = useRef(false);
  const [rendered, setState] = useState({ context, pending: false, needsRead: false });
  const state = rendered.context === context ? rendered : { context, pending: false, needsRead: false };
  useLayoutEffect(() => {
    binding.current = context; data.current = null; needsRead.current = false;
    const clock = version, work = active;
    return () => { clock.current++; work.current?.controller.abort(); work.current = null; };
  }, [context]);
  useEffect(() => { alive.current = true; const clock = version, work = active; return () => { alive.current = false; clock.current++; work.current?.controller.abort(); work.current = null; }; }, []);
  async function execute(action: DialoguePlaceAction, confirmed = false): Promise<DialoguePlaceReply> {
    if (!alive.current || binding.current !== context || epoch !== authSnapshot().epoch || !conversationId || active.current)
      return { text: "偏好请求尚未完成或会话已经变化，请先重新连接当前工作区。" };
    const writing = action.name !== "read_preferences";
    if (!["read_preferences", "save_preference", "delete_preference"].includes(action.name)) return { text: "这个偏好操作暂不支持。" };
    if (writing && !confirmed) return { text: "只有你明确同意后，才会保存或删除长期偏好。" };
    const key = action.arguments.key, value = action.arguments.value;
    if (writing && (typeof key !== "string" || !/^[a-z][a-z0-9_]{0,39}$/.test(key) ||
      action.name === "save_preference" && (typeof value !== "string" || !value.trim() || value.trim().length > 500 || /[\u0000-\u001f\u007f]/.test(value))))
      return { text: "偏好内容或标识无效，未保存。" };
    if (writing && needsRead.current) return { text: "上次偏好写入结果尚未确认，请先读取偏好，不会自动重发。" };
    const work = { controller: new AbortController(), version: ++version.current, writing };
    active.current = work; setState({ context, pending: true, needsRead: needsRead.current });
    const valid = () => alive.current && binding.current === context && epoch === authSnapshot().epoch && active.current === work && version.current === work.version && !work.controller.signal.aborted;
    try {
      // The initial read has no model or external API side effects and obtains the current CAS version.
      let view = data.current;
      if (!writing || !view) view = await requestPreferences(conversationId, work.controller.signal);
      if (!valid()) return { text: "会话已变化，旧偏好结果未应用。" };
      if (writing) {
        view = await requestPreferences(conversationId, work.controller.signal, action.name === "save_preference" ?
          { action: "set", key: String(key), value: String(value).trim(), consent: true, expected_version: view.versions[String(key)] ?? 0 } :
          { action: "delete", key: String(key), expected_version: view.versions[String(key)] ?? 0 });
      }
      if (!valid()) return { text: "操作已停止；写入可能已完成，请重新读取核对。" };
      data.current = view; needsRead.current = false;
      return { text: `${writing ? "长期偏好已更新。\n" : ""}${view.items.length ? `你明确保存的偏好：\n${view.items.map((item, i) => `${i + 1}. ${item.value}`).join("\n")}` : "尚未保存长期偏好。"}\n这些偏好用于后续沟通，不代表排程已经满足所有偏好。` };
    } catch (error) {
      if (!valid()) return { text: "旧偏好响应未应用。" };
      needsRead.current = true;
      return { text: `${error instanceof Error ? error.message : "偏好请求未完成。"}${writing ? " 写入可能已经完成，请先重新读取，不会自动重发。" : ""}` };
    } finally { if (valid()) { active.current = null; setState({ context, pending: false, needsRead: needsRead.current }); } }
  }
  function cancel() {
    if (!alive.current || binding.current !== context) return;
    const writing = active.current?.writing;
    version.current++; active.current?.controller.abort(); active.current = null;
    needsRead.current ||= !!writing;
    setState({ context, pending: false, needsRead: needsRead.current });
  }
  return { execute, cancel, state };
}
