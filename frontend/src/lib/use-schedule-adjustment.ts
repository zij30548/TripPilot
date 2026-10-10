"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { querySchedulePreview, ScheduleError } from "./schedule-api";
import { isScheduleResponse, type ScheduleResponse } from "@/types/schedule";
import { adoptionIssue, prepareAdjustmentInput, type PreparedAdjustment, type ScheduleCapture } from "./schedule-adjustment";
import type { useCandidatePool, CandidateExclusion } from "./use-candidate-pool";
import type { useSchedulePreview } from "./use-schedule-preview";
import type { useWeatherForecast } from "./use-weather-forecast";
import type { useWeatherCheckRevision, useWeatherScheduleCheck } from "./use-weather-schedule-check";
import type { WeatherForecastResponse } from "@/types/weather";
import type { WeatherScheduleReport } from "./weather-schedule-check";

type Context = { schedule: ReturnType<typeof useSchedulePreview>; candidates: ReturnType<typeof useCandidatePool>; weather: ReturnType<typeof useWeatherForecast>; check: ReturnType<typeof useWeatherScheduleCheck> };
type FrozenInput = { base: ScheduleCapture; proposed: PreparedAdjustment; targetId: string; targetName: string; weather: WeatherForecastResponse; report: WeatherScheduleReport; reportVersion: number };
type State = { id: number; status: "idle" | "loading" | "ready" | "failed" | "expired"; input: FrozenInput | null; response: ScheduleResponse | null; message: string | null };
type Session = { id: number; input: FrozenInput; exclusion: CandidateExclusion; authorize: (id: string) => boolean; controller: AbortController };
const idle = (id = 0, message: string | null = null): State => ({ id, status: "idle", input: null, response: null, message });

export function useScheduleAdjustment({ schedule, candidates, weather, check }: Context, revision: ReturnType<typeof useWeatherCheckRevision>) {
  const { subscribe } = revision;
  const [state, setState] = useState<State>(() => idle());
  const stateRef = useRef(state), version = useRef(0), active = useRef<Session | null>(null), pending = useRef(false);
  const publish = useCallback((next: State) => { stateRef.current = next; setState(next); }, []);
  const stop = useCallback(() => { version.current++; active.current?.controller.abort(); active.current = null; pending.current = false; }, []);
  const expire = useCallback(() => {
    stop();
    if (stateRef.current.status !== "idle") publish({ ...stateRef.current, status: "expired", message: "条件已变化，调整预览已过期，不能采用。请重新检查后再预览。" });
  }, [stop, publish]);
  useLayoutEffect(() => subscribe(expire), [subscribe, expire]);
  useEffect(() => stop, [stop]);

  const start = async (targetId: string): Promise<boolean> => {
    if (pending.current || !check.canAdjustNow(targetId) || !check.report || !weather.state.response) return false;
    const base = schedule.capture(), exclusion = candidates.prepareExclusion(targetId);
    if (!base || !exclusion) return false;
    const prepared = prepareAdjustmentInput(base, targetId, exclusion.snapshot);
    stop();
    if (!prepared.value) { publish({ ...idle(version.current), status: "failed", message: prepared.message }); return false; }
    const input: FrozenInput = structuredClone({ base, proposed: prepared.value, targetId,
      targetName: base.original.request.optional_places!.find((p) => p.id === targetId)!.name,
      weather: weather.state.response, report: check.report, reportVersion: revision.read() });
    const session: Session = { id: version.current, input, exclusion, authorize: check.canAdjustNow, controller: new AbortController() };
    active.current = session; pending.current = true;
    publish({ id: session.id, status: "loading", input, response: null, message: null });
    const current = () => active.current === session && version.current === session.id && !session.controller.signal.aborted;
    try {
      const response = await querySchedulePreview(input.proposed.request, session.controller.signal);
      if (!current()) return false;
      if (!session.authorize(targetId) || !exclusion.isCurrent()) { expire(); return false; }
      if (!isScheduleResponse(response, input.proposed.request)) throw new ScheduleError("error", "调整预览数据未通过完整校验，原方案保持不变。");
      publish({ id: session.id, status: "ready", input, response: structuredClone(response), message: null });
      return true;
    } catch (error) {
      if (!current()) return false;
      if (!session.authorize(targetId) || !exclusion.isCurrent()) { expire(); return false; }
      publish({ id: session.id, status: "failed", input, response: null,
        message: error instanceof ScheduleError ? `${error.message} 原方案和正式候选未改变。` : "调整预览失败，原方案和正式候选未改变，请主动重试。" });
      return false;
    } finally { if (current()) pending.current = false; }
  };
  const cancel = () => {
    if (stateRef.current !== state) return false; // An old cancel must not erase a newer preview.
    stop(); publish(idle(version.current, "已取消调整预览，未回写任何方案或设置。")); return true;
  };
  const issue = state.input && state.response ? adoptionIssue(state.input.base, state.input.targetId, state.input.proposed.request, state.response) : null;
  const adopt = () => {
    const session = active.current;
    if (stateRef.current !== state || state.status !== "ready" || !session || session.id !== state.id || !state.response || !state.input) return false;
    if (!session.authorize(session.input.targetId) || !session.exclusion.isCurrent()) { expire(); return false; }
    if (adoptionIssue(session.input.base, session.input.targetId, session.input.proposed.request, state.response)) return false;
    // Consume this identity BEFORE the ordinary candidate publish invalidates
    // the check. Everything below is one synchronous operation, never an effect.
    stop();
    const accepted = schedule.adoptAdjustment(session.input.base, session.input.targetId, session.exclusion, session.input.proposed, state.response);
    if (!accepted) { publish({ ...state, status: "expired", message: "当前输入已变化，未采用此预览。" }); return false; }
    publish(idle(version.current, `已采用调整方案，并在全行程候选中排除“${session.input.targetName}”。请重新检查天气；可在候选区恢复该地点。`));
    return true;
  };
  return { state, start, cancel, adopt, adoptionIssue: issue, canAdopt: state.status === "ready" && !issue, loading: state.status === "loading" };
}
