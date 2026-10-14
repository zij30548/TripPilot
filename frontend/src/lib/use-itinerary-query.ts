"use client";
import { useCallback, useLayoutEffect, useRef, useState } from "react";
import type { useSchedulePreview } from "./use-schedule-preview";
import type { CostInputs } from "./schedule-costs";
import { executeReadOnlyTool, queryItinerary, QUERY_VERSION, type LocalQueryResult, type QueryRequest, type QueryResponse } from "./itinerary-query";

type Schedule = Pick<ReturnType<typeof useSchedulePreview>, "capture" | "readVersion" | "subscribeInvalidation" | "state">;
type State = { status: "idle" | "loading" | "success" | "failed"; result: LocalQueryResult | null; response: QueryResponse | null; message: string | null };
const idle: State = { status: "idle", result: null, response: null, message: null };
export function useItineraryQuery(schedule: Schedule, getCosts: () => CostInputs) {
  const [state, setState] = useState<State>(idle), [question, setQuestion] = useState("");
  const current = useRef(state), input = useRef(""), version = useRef(0), costVersion = useRef(0), pending = useRef(false), alive = useRef(true), active = useRef<AbortController | null>(null);
  const publish = useCallback((next: State) => { current.current = next; setState(next); }, []);
  const stop = useCallback(() => { version.current++; active.current?.abort(); active.current = null; pending.current = false; }, []);
  const invalidate = useCallback(() => { stop(); publish(idle); }, [stop, publish]);
  const costsChanged = useCallback(() => { costVersion.current++; invalidate(); }, [invalidate]);
  const subscribe = schedule.subscribeInvalidation;
  useLayoutEffect(() => { alive.current = true; const unsubscribe = subscribe(invalidate); return () => { alive.current = false; unsubscribe(); stop(); }; }, [subscribe, invalidate, stop]);
  const editQuestion = (value: string) => { if (!alive.current) return; invalidate(); input.current = value; setQuestion(value); };
  const query = async () => {
    if (!alive.current || pending.current) return;
    const base = schedule.capture(); // current ref, never a retained render's response
    if (!base) { publish({ ...idle, message: "请先生成有效的正式草案。" }); return; }
    const text = input.current.trim();
    if (!text || text.length > 400 || /[\u0000-\u001f]/.test(text)) { publish({ ...idle, message: "请输入 1～400 字的单项查询，不接受控制字符。" }); return; }
    stop(); const id = version.current, costs = structuredClone(getCosts()), costId = costVersion.current;
    const controller = new AbortController(); active.current = controller; pending.current = true;
    const request: QueryRequest = { contract_version: QUERY_VERSION, request_id: crypto.randomUUID(), draft_version: base.version, cost_version: costId, dates: base.original.days.map(d => d.date), question: text };
    const valid = () => alive.current && version.current === id && costVersion.current === costId && !controller.signal.aborted && schedule.readVersion() === base.version;
    publish({ ...idle, status: "loading" });
    try {
      const response = await queryItinerary(request, controller.signal);
      if (!valid()) return;
      const result = executeReadOnlyTool(response, request, base, costs);
      if (valid()) publish({ status: "success", result, response, message: null });
    } catch (error) {
      if (valid()) publish({ ...idle, status: "failed", message: error instanceof Error ? error.message : "只读查询未完成。" });
    } finally { if (valid()) { active.current = null; pending.current = false; } }
  };
  const cancel = () => {
    if (!alive.current || current.current !== state || state.status !== "loading") return;
    stop(); publish({ ...idle, message: "已取消等待；无法保证上游停止处理或计费。" });
  };
  return { state, question, editQuestion, query, cancel, costsChanged, available: schedule.state.status === "success" };
}
