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
import { intentMessages, isIntentResponse, queryAdjustmentIntent } from "./adjustment-intent-api";
import { unpackSchedule, type DrawingState } from "./schedule-drawing";

type Context = { schedule: ReturnType<typeof useSchedulePreview>; candidates: ReturnType<typeof useCandidatePool>; weather: ReturnType<typeof useWeatherForecast>; check: ReturnType<typeof useWeatherScheduleCheck> };
type Source = "weather" | "natural";
type FrozenInput = { base: ScheduleCapture; proposed: PreparedAdjustment; targetId: string; targetName: string; weather: WeatherForecastResponse | null; report: WeatherScheduleReport | null; reportVersion: number; source: Source; requestId: string | null };
type State = { id: number; status: "idle" | "parsing" | "clarify" | "unsupported" | "loading" | "ready" | "failed" | "expired"; source: Source | null; input: FrozenInput | null; response: ScheduleResponse | null; message: string | null; drawing?: DrawingState };
type Session = { id: number; input: FrozenInput; exclusion: CandidateExclusion; authorize: (id: string) => boolean; controller: AbortController };
const idle = (id = 0, message: string | null = null): State => ({ id, status: "idle", source: null, input: null, response: null, message });

export function useScheduleAdjustment({ schedule, candidates, weather, check }: Context, revision: ReturnType<typeof useWeatherCheckRevision>, canOperate?:()=>boolean) {
  const { subscribe } = revision;
  const [state, setState] = useState<State>(() => idle());
  const [instruction, setInstructionState] = useState("");
  const instructionRef = useRef(""), parseController = useRef<AbortController | null>(null);
  const stateRef = useRef(state), version = useRef(0), active = useRef<Session | null>(null), pending = useRef(false);
  const publish = useCallback((next: State) => { stateRef.current = next; setState(next); }, []);
  const stop = useCallback(() => { version.current++; active.current?.controller.abort(); parseController.current?.abort(); parseController.current = null; active.current = null; pending.current = false; }, []);
  const expire = useCallback(() => {
    stop();
    if (stateRef.current.status !== "idle") publish({ ...stateRef.current, status: "expired", message: "条件已变化，调整预览已过期，不能采用。请重新检查后再预览。" });
  }, [stop, publish]);
  const weatherExpire = useCallback(() => { if (stateRef.current.source === "weather") expire(); }, [expire]);
  useLayoutEffect(() => subscribe(weatherExpire), [subscribe, weatherExpire]);
  const subscribeDraft = schedule.subscribeInvalidation;
  useLayoutEffect(() => subscribeDraft(expire), [subscribeDraft, expire]);
  useEffect(() => stop, [stop]);

  const generate = async (base: ScheduleCapture, targetId: string, authorize: (id: string) => boolean, source: Source, requestId: string | null): Promise<boolean> => {
    if (canOperate?.() === false) return false;
    const exclusion = candidates.prepareExclusion(targetId);
    if (!authorize(targetId) || !exclusion) { expire(); return false; }
    const prepared = prepareAdjustmentInput(base, targetId, exclusion.snapshot);
    stop();
    if (!prepared.value) { publish({ ...idle(version.current), source, status: "failed", message: prepared.message }); return false; }
    const input: FrozenInput = structuredClone({ base, proposed: prepared.value, targetId,
      targetName: base.original.request.optional_places!.find((p) => p.id === targetId)!.name,
      weather: source === "weather" ? weather.state.response : null, report: source === "weather" ? check.report : null,
      reportVersion: revision.read(), source, requestId });
    const session: Session = { id: version.current, input, exclusion, authorize, controller: new AbortController() };
    active.current = session; pending.current = true;
    publish({ id: session.id, source, status: "loading", input, response: null, message: null });
    const current = () => active.current === session && version.current === session.id && !session.controller.signal.aborted;
    try {
      const { schedule: response, drawing } = unpackSchedule(await querySchedulePreview(input.proposed.request, session.controller.signal, true));
      if (!current()) return false;
      if (canOperate?.() === false || !session.authorize(targetId) || !exclusion.isCurrent()) { expire(); return false; }
      if (!isScheduleResponse(response, input.proposed.request)) throw new ScheduleError("error", "调整预览数据未通过完整校验，原方案保持不变。");
      publish({ id: session.id, source, status: "ready", input, response: structuredClone(response), drawing: structuredClone(drawing), message: null });
      return true;
    } catch (error) {
      if (!current()) return false;
      if (!session.authorize(targetId) || !exclusion.isCurrent()) { expire(); return false; }
      publish({ id: session.id, source, status: "failed", input, response: null,
        message: error instanceof ScheduleError ? `${error.message} 原方案和正式候选未改变。` : "调整预览失败，原方案和正式候选未改变，请主动重试。" });
      return false;
    } finally { if (current()) pending.current = false; }
  };
  const start = async (targetId: string): Promise<boolean> => {
    if (canOperate?.() === false || pending.current || !check.canAdjustNow(targetId) || !check.report || !weather.state.response) return false;
    const base = schedule.capture();
    return base ? generate(base, targetId, check.canAdjustNow, "weather", null) : false;
  };
  // A chat tool has already selected one current optional POI. Reuse the same
  // isolated preparation/commit path without a second intent-model request.
  // This is an explicit user adjustment, so it has no weather prerequisite.
  const startExplicit = async (targetId: string): Promise<boolean> => {
    if (canOperate?.() === false || pending.current) return false;
    const base = schedule.capture();
    if (!base || !base.original.days.some(day => day.items.some(item => item.kind === "visit" && item.place_id === targetId)) ||
        !base.original.request.optional_places?.some(place => place.id === targetId)) return false;
    const authorize = (id: string) => id === targetId && schedule.readVersion() === base.version && schedule.capture() !== null;
    return generate(base, targetId, authorize, "natural", null);
  };
  const setInstruction = (text: string) => {
    instructionRef.current = text; setInstructionState(text);
    if (stateRef.current.source === "natural") { stop(); publish(idle(version.current, "调整要求已修改，请主动生成新预览。")); }
  };
  const startNatural = async (): Promise<boolean> => {
    if (canOperate?.() === false || pending.current) return false;
    const base = schedule.capture(), text = instructionRef.current.trim();
    if (!base || !text || text.length > 400) return false;
    stop(); const generation = version.current, requestId = crypto.randomUUID(), controller = new AbortController();
    parseController.current = controller; pending.current = true;
    const current = () => version.current === generation && !controller.signal.aborted && schedule.readVersion() === base.version;
    publish({ ...idle(generation), source: "natural", status: "parsing", message: "正在理解调整要求，原方案保持不变…" });
    try {
      const result = await queryAdjustmentIntent(base, text, requestId, controller.signal);
      if (!current() || canOperate?.() === false) return false;
      if (!isIntentResponse(result, base, requestId)) throw new Error("调整目标未通过当前草案核对。");
      if (result.selection.decision !== "execute") {
        publish({ ...idle(generation), source: "natural", status: result.selection.decision, message: intentMessages[result.selection.reason_code] }); return false;
      }
      const targetId = result.selection.target_id!;
      const authorize = (id: string) => id === targetId && schedule.readVersion() === base.version && schedule.capture() !== null;
      // Same preview builder and synchronous commit path as weather adjustments.
      // No weather prerequisites: this is an explicit user request, not a weather conclusion.
      return await generate(base, targetId, authorize, "natural", requestId);
    } catch (error) {
      if (!current()) return false;
      publish({ ...idle(generation), source: "natural", status: "failed", message: error instanceof Error ? error.message : "调整解析失败，原方案未改变。" }); return false;
    } finally { if (current()) { pending.current = false; parseController.current = null; } }
  };
  const cancel = () => {
    if (stateRef.current !== state) return false; // An old cancel must not erase a newer preview.
    stop(); publish(idle(version.current, "已取消行程调整，原方案保持不变。你主动填写的费用估算仍在本次结果中保留。")); return true;
  };
  const cancelPending = () => {
    if (!pending.current) return;
    stop(); publish(idle(version.current, "已取消行程调整，原方案保持不变。"));
  };
  const issue = state.input && state.response ? adoptionIssue(state.input.base, state.input.targetId, state.input.proposed.request, state.response) : null;
  // Event-time guard for preview-only UI edits. Reuse session authorization;
  // cost edits have no revision and do not change adoption eligibility.
  const canUsePreview = () => {
    const session = active.current;
    return canOperate?.() !== false && stateRef.current === state && state.status === "ready" && !issue && !!session && session.id === state.id &&
      session.exclusion.isCurrent() && session.authorize(session.input.targetId);
  };
  const adopt = () => {
    const session = active.current;
    if (canOperate?.() === false || stateRef.current !== state || state.status !== "ready" || !session || session.id !== state.id || !state.response || !state.input) return false;
    if (!session.authorize(session.input.targetId) || !session.exclusion.isCurrent()) { expire(); return false; }
    if (adoptionIssue(session.input.base, session.input.targetId, session.input.proposed.request, state.response)) return false;
    // Consume this identity BEFORE the ordinary candidate publish invalidates
    // the check. Everything below is one synchronous operation, never an effect.
    stop();
    const accepted = schedule.adoptAdjustment(session.input.base, session.input.targetId, session.exclusion, session.input.proposed, state.response, state.drawing);
    if (!accepted) { publish({ ...state, status: "expired", message: "当前输入已变化，未采用此预览。" }); return false; }
    publish(idle(version.current, `已采用调整方案，并在全行程候选中排除“${session.input.targetName}”。请重新检查天气；可在候选区恢复该地点。`));
    return true;
  };
  return { state, readState: () => stateRef.current, start, startExplicit, startNatural, instruction, setInstruction, cancel, cancelPending, adopt, canUsePreview, adoptionIssue: issue, canAdopt: state.status === "ready" && !issue, loading: state.status === "loading" || state.status === "parsing" };
}
