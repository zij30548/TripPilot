"use client";

import { useCallback, useLayoutEffect, useRef, useState } from "react";
import { queryExplanation, type ExplanationResponse } from "./explanation-api";
import type { useSchedulePreview } from "./use-schedule-preview";

type State = { id: number; status: "idle" | "loading" | "success" | "failed"; response: ExplanationResponse | null; message: string | null };
type Schedule = Pick<ReturnType<typeof useSchedulePreview>, "capture" | "readVersion" | "subscribeInvalidation" | "state">;
export function useScheduleExplanation(schedule: Schedule) {
  const [state, setState] = useState<State>({ id: 0, status: "idle", response: null, message: null });
  const current = useRef(state); const version = useRef(0); const pending = useRef(false); const active = useRef<AbortController | null>(null);
  const publish = useCallback((next: State) => { current.current = next; setState(next); }, []);
  const stop = useCallback(() => { version.current++; active.current?.abort(); active.current = null; pending.current = false; }, []);
  const invalidate = useCallback(() => { stop(); publish({ id: version.current, status: "idle", response: null, message: null }); }, [stop, publish]);
  const subscribe = schedule.subscribeInvalidation;
  useLayoutEffect(() => { const unsubscribe = subscribe(invalidate); return () => { unsubscribe(); stop(); }; }, [subscribe, invalidate, stop]);

  const explain = async () => {
    if (pending.current) return;
    // capture reads current response AND version synchronously; even a retained
    // old click handler cannot pair an old render's draft with the latest version.
    const snapshot = schedule.capture();
    if (!snapshot) return;
    stop(); const id = version.current; const controller = new AbortController(); active.current = controller; pending.current = true;
    const requestId = crypto.randomUUID();
    const isCurrent = () => version.current === id && !controller.signal.aborted && schedule.readVersion() === snapshot.version;
    publish({ id, status: "loading", response: null, message: null });
    try {
      const response = await queryExplanation(snapshot.original, requestId, controller.signal);
      if (!isCurrent()) return;
      publish({ id, status: "success", response, message: null });
    } catch (error) {
      if (!isCurrent()) return;
      publish({ id, status: "failed", response: null, message: error instanceof Error ? error.message : "暂时无法解读，请主动重试。" });
    } finally { if (isCurrent()) { active.current = null; pending.current = false; } }
  };
  const cancel = () => {
    // A cancel callback from an earlier render must not cancel a newer request.
    if (current.current !== state || state.status !== "loading") return;
    stop(); publish({ id: version.current, status: "idle", response: null, message: "已取消等待；上游是否停止处理或计费无法保证。" });
  };
  return { state, explain, cancel, available: schedule.state.status === "success", loading: state.status === "loading" };
}
