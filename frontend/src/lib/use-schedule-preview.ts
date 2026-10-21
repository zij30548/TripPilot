"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { querySchedulePreview, ScheduleError } from "@/lib/schedule-api";
import type { CandidatePoolSnapshot } from "@/lib/use-candidate-pool";
import { isScheduleResponse, type ScheduleResponse } from "@/types/schedule";
import { buildRequest, defaultScheduleSettings, eligibleOptional, validateScheduleInput, withOptionalDefaults, type ScheduleSettings } from "@/lib/schedule-input";
import { prepareAdjustmentInput, adoptionIssue, type ScheduleCapture, type PreparedAdjustment } from "@/lib/schedule-adjustment";
import type { CandidateExclusion } from "@/lib/use-candidate-pool";
import type { TripRequest } from "@/types/trip";
import { emptyCandidateSnapshot } from "./candidate-pool-state";
import { unpackSchedule, type DrawingState } from "./schedule-drawing";

export type { StayDraft, ScheduleSettings } from "@/lib/schedule-input";
export type SchedulePreviewState = { status: "idle" | "loading" | "success" | "failed"; response: ScheduleResponse | null; message: string | null; drawing?: DrawingState };
const idle = (): SchedulePreviewState => ({ status: "idle", response: null, message: null });

export type ScheduleInitialization = { settings: ScheduleSettings; candidates: CandidatePoolSnapshot };
export function useSchedulePreview(request: TripRequest, onInvalidate?: () => void, initial?: ScheduleInitialization, canOperate?: () => boolean) {
  const [settings, setSettings] = useState<ScheduleSettings>(() => structuredClone(initial?.settings ?? defaultScheduleSettings(request)));
  const alive = useRef(true);
  const settingsRef = useRef(settings);
  const [candidateInput, setCandidateInput] = useState<CandidatePoolSnapshot>(() => structuredClone(initial?.candidates ?? emptyCandidateSnapshot()));
  const candidateRef = useRef(candidateInput);
  const [state, setState] = useState<SchedulePreviewState>(idle);
  const stateRef = useRef(state);
  const publish = useCallback((next: SchedulePreviewState) => { stateRef.current = next; setState(next); }, []);
  const invalidationListeners = useRef(new Set<() => void>());
  const subscribeInvalidation = useCallback((listener: () => void) => {
    invalidationListeners.current.add(listener);
    return () => { invalidationListeners.current.delete(listener); };
  }, []);
  const version = useRef(0); const controller = useRef<AbortController | null>(null); const pending = useRef(false);
  const readVersion = useCallback(() => version.current, []);
  const cancel = useCallback(() => {
    version.current++; stateRef.current = idle(); controller.current?.abort(); controller.current = null; pending.current = false;
    // Only official-draft changes publish here. Weather/check revisions are not
    // subscribed back into this channel; fee/map/preview renders are independent.
    invalidationListeners.current.forEach((listener) => listener());
    onInvalidate?.();
  }, [onInvalidate]);
  const invalidate = useCallback(() => { cancel(); publish(idle()); }, [cancel, publish]);
  useEffect(() => { alive.current = true; return () => { alive.current = false; cancel(); }; }, [cancel]);
  const updateCandidates = (input: CandidatePoolSnapshot) => {
    if (!alive.current) return;
    cancel();
    const snapshot: CandidatePoolSnapshot = structuredClone(input);
    candidateRef.current = snapshot; setCandidateInput(snapshot);
    const next = withOptionalDefaults(settingsRef.current, eligibleOptional(request, snapshot).slice(0, 3)); settingsRef.current = next; setSettings(next);
    publish({ status: "idle", response: null, message: input.loading ? "候选地点正在更新，原草案已清除。" : "候选地点或排除选择已更新，请重新生成草案。" });
  };
  const update = (change: (current: ScheduleSettings) => ScheduleSettings) => {
    if (!alive.current || canOperate?.() === false) return;
    cancel();
    const next = change(settingsRef.current); settingsRef.current = next; setSettings(next);
    publish({ status: "idle", response: null, message: "设置已更新，请重新生成草案。" });
  };
  const setStayMinutes = (id: string, value: string) => {
    if (![...(request.must_visit_places ?? []), ...eligibleOptional(request, candidateRef.current).slice(0, 3)].some((place) => place.id === id)) return;
    update((current) => ({ ...current, stays: { ...current.stays, [id]: { value, source: "user" } } }));
  };
  const setLunchEnabled = (enabled: boolean) => update((current) => ({ ...current, lunch: { ...current.lunch, enabled } }));
  const setLunchStart = (start: string) => update((current) => ({ ...current, lunch: { ...current.lunch, start } }));
  const setLunchEnd = (end: string) => update((current) => ({ ...current, lunch: { ...current.lunch, end } }));
  const setTransportMode = (transportMode: ScheduleSettings["transportMode"]) => {
    if (transportMode === settingsRef.current.transportMode) return;
    // Like other inputs, the mode lives in the synchronous settings ref. Even an
    // older query closure in this same event must read the new mode, not its render.
    update((current) => ({ ...current, transportMode }));
  };
  const query = async (): Promise<boolean> => {
    if (!alive.current || canOperate?.() === false || pending.current) return false;
    const optionalPlaces = eligibleOptional(request, candidateRef.current).slice(0, 3);
    const issue = validateScheduleInput(request, settingsRef.current, optionalPlaces, candidateRef.current.loading);
    const body = issue ? null : buildRequest(request, settingsRef.current, optionalPlaces);
    if (!body) { cancel(); publish({ status: "failed", response: null, message: issue }); return false; }
    cancel(); const generation = version.current; const active = new AbortController(); controller.current = active; pending.current = true;
    publish({ status: "loading", response: null, message: null });
    try {
      const { schedule: response, drawing } = unpackSchedule(await querySchedulePreview(body, active.signal, true));
      if (version.current !== generation || active.signal.aborted || canOperate?.() === false) return false;
      publish({ status: "success", response, drawing, message: null }); return true;
    } catch (error) {
      if (version.current !== generation || active.signal.aborted) return false;
      publish({ status: "failed", response: null, message: error instanceof ScheduleError ? error.message : "生成行程草案失败，请主动重试。" }); return false;
    } finally { if (version.current === generation) {
      controller.current = null; pending.current = false;
      if (canOperate?.() === false && stateRef.current.status === "loading") publish({ status: "failed", response: null, message: "地点准备尚待核对，未应用本次结果；请核对后主动生成。" });
    } }
  };

  // Capture only a validated current success. Refs include historical stays that
  // are not currently in the first three, without adding them to the request.
  const capture = (): ScheduleCapture | null => {
    if (!alive.current || canOperate?.() === false) return null;
    const input = buildRequest(request, settingsRef.current, eligibleOptional(request, candidateRef.current).slice(0, 3));
    const current = stateRef.current;
    if (pending.current || candidateRef.current.loading || current.status !== "success" || !input || !isScheduleResponse(current.response, input)) return null;
    return structuredClone({ version: version.current, original: current.response, drawing: current.drawing, request, settings: settingsRef.current, candidates: candidateRef.current });
  };
  const adoptAdjustment = (base: ScheduleCapture, targetId: string, exclusion: CandidateExclusion, proposed: PreparedAdjustment, response: ScheduleResponse, drawing?: DrawingState): boolean => {
    if (!alive.current || canOperate?.() === false || version.current !== base.version || pending.current || !exclusion.isCurrent()) return false;
    const prepared = prepareAdjustmentInput(base, targetId, exclusion.snapshot);
    if (!prepared.value || JSON.stringify(prepared.value.request) !== JSON.stringify(proposed.request) ||
        adoptionIssue(base, targetId, prepared.value.request, response)) return false;
    // Every check/clone precedes the commit. No await/effect, no network, and no
    // response-only replacement leaving stale settingsRef/candidateRef behind.
    const next = structuredClone(prepared.value), accepted = structuredClone(response), acceptedDrawing = structuredClone(drawing);
    if (!exclusion.commit()) return false; // publishes the normal synchronous invalidation
    cancel();
    candidateRef.current = next.candidates; settingsRef.current = next.settings;
    setCandidateInput(next.candidates); setSettings(next.settings);
    publish({ status: "success", response: accepted, drawing: acceptedDrawing, message: null });
    return true;
  };
  const eligible = eligibleOptional(request, candidateInput);
  const optionalPlaces = eligible.slice(0, 3);
  const validationMessage = validateScheduleInput(request, settings, optionalPlaces, candidateInput.loading);
  const readInputs = (): ScheduleInitialization => structuredClone({ settings: settingsRef.current, candidates: candidateRef.current });
  return { transportMode: settings.transportMode, setTransportMode, stays: settings.stays, lunch: settings.lunch, setStayMinutes, setLunchEnabled, setLunchStart, setLunchEnd,
    capture, readInputs, readVersion, subscribeInvalidation, adoptAdjustment, state, canQuery: validationMessage === null, validationMessage, query, invalidate, updateCandidates,
    optionalPlaces, eligibleCount: eligible.length, notSelectedCount: Math.max(eligible.length - 3, 0),
    candidateLoading: candidateInput.loading, candidateQueriedAt: candidateInput.response?.queried_at ?? null,
    candidateStatus: candidateInput.response?.status ?? null, showingPreviousCandidates: candidateInput.showingPrevious,
    historicalCandidates: !!candidateInput.history,
    candidateMessage: candidateInput.message };
}
