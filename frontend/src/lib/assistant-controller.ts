"use client";

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { authSnapshot } from "./auth-session";
import { readAssistantState, type AssistantState } from "./assistant";
import type { ChatConversation } from "./chat";

type Context = { id: string | null; revision: number | null; blocked: boolean; epoch: number };
type View = { context: Context | null; value: AssistantState | null; pending: boolean; error: string | null };
type Work = { context: Context; controller: AbortController; version: number; epoch: number };
const initial: View = { context: null, value: null, pending: false, error: null };

/** This controller reads receipts only; it never starts or resumes an Agent task. */
export function useAssistantState(conversation: ChatConversation | null, blocked: boolean) {
  const [state, setState] = useState<View>(initial);
  const epoch = authSnapshot().epoch;
  const context = useMemo<Context>(() => ({ id: conversation?.id ?? null, revision: conversation?.revision ?? null, blocked, epoch }), [conversation?.id, conversation?.revision, blocked, epoch]);
  const current = useRef(context), active = useRef<Work | null>(null), version = useRef(0), alive = useRef(true);
  const stop = useCallback(() => { version.current += 1; active.current?.controller.abort(); active.current = null; }, []);
  useLayoutEffect(() => {
    current.current = context;
    return stop;
  }, [context, stop]);
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; stop(); };
  }, [stop]);
  const reload = useCallback(async () => {
    const bound = current.current;
    if (!alive.current || current.current !== context || !bound.id || bound.revision === null || bound.blocked || bound.epoch !== authSnapshot().epoch || active.current) return;
    const work: Work = { context: bound, controller: new AbortController(), version: ++version.current, epoch: authSnapshot().epoch };
    active.current = work;
    setState({ context: bound, value: null, pending: true, error: null });
    const valid = () => alive.current && active.current === work && current.current === work.context && version.current === work.version &&
      authSnapshot().epoch === work.epoch && !work.controller.signal.aborted;
    try {
      const value = await readAssistantState(bound.id, bound.revision, work.controller.signal);
      if (valid()) setState({ context: bound, value, pending: false, error: null });
    } catch (error) {
      if (valid()) setState({ context: bound, value: null, pending: false, error: error instanceof Error ? error.message : "无法读取执行记录。" });
    } finally { if (valid()) active.current = null; }
  }, [context]);
  const cancel = useCallback(() => {
    if (current.current !== context) return;
    stop();
    setState({ ...initial, context });
  }, [context, stop]);
  // Hide the previous receipt synchronously on a different conversation/revision.
  // The object identity, plus the monotonic generation above, isolates A→B→A.
  const usable = state.context === context && state.value?.conversation_id === context.id && state.value.revision === context.revision && !context.blocked;
  return { state: { value: usable ? state.value : null, pending: state.context === context && state.pending, error: state.context === context && !context.blocked ? state.error : null }, reload, cancel };
}
