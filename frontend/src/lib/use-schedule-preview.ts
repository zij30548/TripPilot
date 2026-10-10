"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { querySchedulePreview, ScheduleError } from "@/lib/schedule-api";
import { isConfirmedPlace, type Place } from "@/types/place";
import type { CandidatePoolSnapshot } from "@/lib/use-candidate-pool";
import { isScheduleRequest, scheduleClock, scheduleDates, scheduleMinutes, type ScheduleRequest, type ScheduleResponse } from "@/types/schedule";
import type { TripRequest } from "@/types/trip";

export type StayDraft = { value: string; source: "default" | "user" };
export type ScheduleSettings = { transportMode: "walking" | "transit"; stays: Record<string, StayDraft>; lunch: { enabled: boolean; start: string; end: string } };
export type SchedulePreviewState = { status: "idle" | "loading" | "success" | "failed"; response: ScheduleResponse | null; message: string | null };
const idle = (): SchedulePreviewState => ({ status: "idle", response: null, message: null });

function eligibleOptional(request: TripRequest, candidates: CandidatePoolSnapshot): Place[] {
  const excluded = new Set([request.accommodation_place?.id, ...(request.must_visit_places ?? []).map((place) => place.id), ...candidates.excludedIds]);
  const selected: Place[] = [];
  for (const candidate of candidates.response?.candidates ?? []) {
    if (candidate.role !== "optional" || !isConfirmedPlace(candidate.place) || excluded.has(candidate.place.id)) continue;
    excluded.add(candidate.place.id); selected.push(candidate.place);
  }
  return selected;
}
function buildRequest(request: TripRequest, settings: ScheduleSettings, optionalPlaces: Place[]): ScheduleRequest | null {
  const value: unknown = {
    transport_mode: settings.transportMode,
    start_date: request.start_date, end_date: request.end_date,
    daily_start_time: scheduleClock(request.daily_start_time), daily_end_time: scheduleClock(request.daily_end_time),
    accommodation_place: request.accommodation_place,
    must_visit_places: request.must_visit_places ?? [],
    optional_places: optionalPlaces,
    duration_settings: [...(request.must_visit_places ?? []), ...optionalPlaces].map((place) => ({
      place_id: place.id, minutes: /^\d+$/.test(settings.stays[place.id]?.value ?? "") ? Number(settings.stays[place.id].value) : NaN,
      source: settings.stays[place.id]?.source,
    })),
    // Disabled lunch never participates. Preserve its UI draft for re-enabling,
    // but send harmless valid clocks even if that draft is currently blank/invalid.
    lunch: { enabled: settings.lunch.enabled, start_time: settings.lunch.enabled ? settings.lunch.start : "12:00", end_time: settings.lunch.enabled ? settings.lunch.end : "13:00" },
  };
  // Freeze this generation's complete input independently of later pool/settings edits.
  return isScheduleRequest(value) ? structuredClone(value) : null;
}
function validation(request: TripRequest, settings: ScheduleSettings, optionalPlaces: Place[], candidateLoading: boolean): string | null {
  if (candidateLoading) return "候选地点正在更新，请等待获取结束后再生成草案。";
  if (!isConfirmedPlace(request.accommodation_place)) return "请先返回修改旅行需求，确认住宿参考点。";
  const required = request.must_visit_places ?? [];
  if (required.length > 6) return "本轮最多支持 6 个已确认必去地点，请返回修改旅行需求调整。";
  if (!required.length && !optionalPlaces.length) return "请先确认必去地点，或主动获取并保留至少一个可选地点。";
  if (!required.every(isConfirmedPlace) || new Set(required.map((place) => place.id)).size !== required.length) return "已确认地点信息无效，请返回修改旅行需求重新确认。";
  if (!scheduleDates(request.start_date, request.end_date)) return "本轮支持 1～3 天，请返回修改旅行需求调整日期。";
  const start = scheduleMinutes(scheduleClock(request.daily_start_time)); const end = scheduleMinutes(scheduleClock(request.daily_end_time));
  if (start === null || end === null || start >= end) return "每日出发与结束时间需要精确到分钟，且结束晚于出发，请返回修改旅行需求。";
  if ([...required, ...optionalPlaces].some((place) => !/^\d+$/.test(settings.stays[place.id]?.value ?? "") || Number(settings.stays[place.id].value) < 15 || Number(settings.stays[place.id].value) > 480)) return "每个地点的停留时间请输入 15～480 的整数分钟。";
  if (settings.lunch.enabled) {
    const lunchStart = scheduleMinutes(settings.lunch.start); const lunchEnd = scheduleMinutes(settings.lunch.end);
    if (lunchStart === null || lunchEnd === null || lunchStart >= lunchEnd || lunchStart < start || lunchEnd > end) return "午餐开始与结束时间需完整位于每日出发和结束时间内，请调整或关闭午餐。";
  }
  return buildRequest(request, settings, optionalPlaces) ? null : "请检查地点与时间设置后再生成草案。";
}

export function useSchedulePreview(request: TripRequest, onInvalidate?: () => void) {
  const [settings, setSettings] = useState<ScheduleSettings>(() => ({
    transportMode: "walking",
    stays: Object.fromEntries((request.must_visit_places ?? []).map((place) => [place.id, { value: "60", source: "default" }])),
    lunch: { enabled: true, start: "12:00", end: "13:00" },
  }));
  const settingsRef = useRef(settings);
  const [candidateInput, setCandidateInput] = useState<CandidatePoolSnapshot>(() => ({ response: null, lastAttempt: null, excludedIds: new Set(), loading: false, showingPrevious: false, message: null }));
  const candidateRef = useRef(candidateInput);
  const [state, setState] = useState<SchedulePreviewState>(idle);
  const version = useRef(0); const controller = useRef<AbortController | null>(null); const pending = useRef(false);
  const cancel = useCallback(() => { onInvalidate?.(); version.current++; controller.current?.abort(); controller.current = null; pending.current = false; }, [onInvalidate]);
  const invalidate = useCallback(() => { cancel(); setState(idle()); }, [cancel]);
  useEffect(() => cancel, [cancel]);
  const updateCandidates = (input: CandidatePoolSnapshot) => {
    cancel();
    const snapshot: CandidatePoolSnapshot = { ...input, response: input.response ? structuredClone(input.response) : null,
      lastAttempt: input.lastAttempt ? structuredClone(input.lastAttempt) : null, excludedIds: new Set(input.excludedIds) };
    candidateRef.current = snapshot; setCandidateInput(snapshot);
    const stays = { ...settingsRef.current.stays };
    for (const place of eligibleOptional(request, snapshot).slice(0, 3)) {
      if (!Object.hasOwn(stays, place.id)) stays[place.id] = { value: "60", source: "default" };
    }
    const next = { ...settingsRef.current, stays }; settingsRef.current = next; setSettings(next);
    setState({ status: "idle", response: null, message: input.loading ? "候选地点正在更新，原草案已清除。" : "候选地点或排除选择已更新，请重新生成草案。" });
  };
  const update = (change: (current: ScheduleSettings) => ScheduleSettings) => {
    cancel();
    const next = change(settingsRef.current); settingsRef.current = next; setSettings(next);
    setState({ status: "idle", response: null, message: "设置已更新，请重新生成草案。" });
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
    if (pending.current) return false;
    const optionalPlaces = eligibleOptional(request, candidateRef.current).slice(0, 3);
    const issue = validation(request, settingsRef.current, optionalPlaces, candidateRef.current.loading);
    const body = issue ? null : buildRequest(request, settingsRef.current, optionalPlaces);
    if (!body) { setState({ status: "failed", response: null, message: issue }); return false; }
    cancel(); const generation = version.current; const active = new AbortController(); controller.current = active; pending.current = true;
    setState({ status: "loading", response: null, message: null });
    try {
      const response = await querySchedulePreview(body, active.signal);
      if (version.current !== generation || active.signal.aborted) return false;
      setState({ status: "success", response, message: null }); return true;
    } catch (error) {
      if (version.current !== generation || active.signal.aborted) return false;
      setState({ status: "failed", response: null, message: error instanceof ScheduleError ? error.message : "生成行程草案失败，请主动重试。" }); return false;
    } finally { if (version.current === generation) { controller.current = null; pending.current = false; } }
  };
  const eligible = eligibleOptional(request, candidateInput);
  const optionalPlaces = eligible.slice(0, 3);
  const validationMessage = validation(request, settings, optionalPlaces, candidateInput.loading);
  return { transportMode: settings.transportMode, setTransportMode, stays: settings.stays, lunch: settings.lunch, setStayMinutes, setLunchEnabled, setLunchStart, setLunchEnd,
    state, canQuery: validationMessage === null, validationMessage, query, invalidate, updateCandidates,
    optionalPlaces, eligibleCount: eligible.length, notSelectedCount: Math.max(eligible.length - 3, 0),
    candidateLoading: candidateInput.loading, candidateQueriedAt: candidateInput.response?.queried_at ?? null,
    candidateStatus: candidateInput.response?.status ?? null, showingPreviousCandidates: candidateInput.showingPrevious,
    candidateMessage: candidateInput.message };
}
