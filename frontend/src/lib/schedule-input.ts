import { isConfirmedPlace, type Place } from "@/types/place";
import type { CandidatePoolSnapshot } from "@/lib/use-candidate-pool";
import { isScheduleRequest, scheduleClock, scheduleDates, scheduleMinutes, type ScheduleRequest } from "@/types/schedule";
import type { TripRequest } from "@/types/trip";

export type StayDraft = { value: string; source: "default" | "user" };
export type ScheduleSettings = { transportMode: "walking" | "transit"; stays: Record<string, StayDraft>; lunch: { enabled: boolean; start: string; end: string } };

export function eligibleOptional(request: TripRequest, candidates: CandidatePoolSnapshot): Place[] {
  const excluded = new Set([request.accommodation_place?.id, ...(request.must_visit_places ?? []).map((place) => place.id), ...candidates.excludedIds]);
  const selected: Place[] = [];
  for (const candidate of candidates.response?.candidates ?? []) {
    if (candidate.role !== "optional" || !isConfirmedPlace(candidate.place) || excluded.has(candidate.place.id)) continue;
    excluded.add(candidate.place.id); selected.push(candidate.place);
  }
  return selected;
}
export function buildRequest(request: TripRequest, settings: ScheduleSettings, optionalPlaces: Place[]): ScheduleRequest | null {
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
export function validateScheduleInput(request: TripRequest, settings: ScheduleSettings, optionalPlaces: Place[], candidateLoading: boolean): string | null {
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


export function withOptionalDefaults(settings: ScheduleSettings, places: Place[]): ScheduleSettings {
  const next = structuredClone(settings);
  for (const place of places) if (!Object.hasOwn(next.stays, place.id)) next.stays[place.id] = { value: "60", source: "default" };
  return next;
}
