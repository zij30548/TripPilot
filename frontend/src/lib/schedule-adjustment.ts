import type { TripRequest } from "@/types/trip";
import { isScheduleResponse, type ScheduleRequest, type ScheduleResponse } from "@/types/schedule";
import type { CandidatePoolSnapshot } from "./use-candidate-pool";
import { buildRequest, eligibleOptional, validateScheduleInput, withOptionalDefaults, type ScheduleSettings } from "./schedule-input";
import { scheduledVisits } from "./weather-schedule-check";

export type ScheduleCapture = { version: number; original: ScheduleResponse; request: TripRequest; settings: ScheduleSettings; candidates: CandidatePoolSnapshot };
export type PreparedAdjustment = { request: ScheduleRequest; settings: ScheduleSettings; candidates: CandidatePoolSnapshot };
const snapshotKey = (value: unknown) => JSON.stringify(value, (_key, item) => item instanceof Set ? [...item].sort()
  : item && typeof item === "object" && !Array.isArray(item) ? Object.fromEntries(Object.entries(item).sort(([a], [b]) => a.localeCompare(b))) : item);

// Shared ordinary selection/request rules, operating ONLY on isolated copies.
export function prepareAdjustmentInput(base: ScheduleCapture, targetId: string, candidates: CandidatePoolSnapshot): { value: PreparedAdjustment | null; message: string | null } {
  const expected = { ...base.candidates, excludedIds: new Set([...base.candidates.excludedIds, targetId]) };
  const target = scheduledVisits(base.original).find((v) => v.place.id === targetId && v.role === "optional");
  if (!target || base.candidates.loading || base.candidates.excludedIds.has(targetId) ||
      !base.candidates.response?.candidates.some((c) => c.role === "optional" && c.place.id === targetId) || snapshotKey(expected) !== snapshotKey(candidates)) {
    return { value: null, message: "当前地点或候选已变化，请重新检查后再预览。" };
  }
  const optional = eligibleOptional(base.request, candidates).slice(0, 3);
  if (!(base.request.must_visit_places?.length || optional.length)) return { value: null, message: "排除后没有必去或可选地点，无法生成调整预览；原方案保持不变。" };
  const settings = withOptionalDefaults(base.settings, optional);
  const issue = validateScheduleInput(base.request, settings, optional, candidates.loading);
  const request = issue ? null : buildRequest(base.request, settings, optional);
  return request ? { value: structuredClone({ request, settings, candidates }), message: null }
    : { value: null, message: issue ?? "调整输入无效，原方案保持不变。" };
}
function fixedInput(request: ScheduleRequest) {
  return { start: request.start_date, end: request.end_date, dayStart: request.daily_start_time, dayEnd: request.daily_end_time,
    accommodation: request.accommodation_place, required: request.must_visit_places, mode: request.transport_mode ?? "walking", lunch: request.lunch };
}
export function adoptionIssue(base: ScheduleCapture, targetId: string, request: ScheduleRequest, response: unknown): string | null {
  if (!isScheduleResponse(base.original, base.original.request) || !isScheduleResponse(response, request)) return "预览数据未通过完整校验，不能采用。";
  if (snapshotKey(fixedInput(base.original.request)) !== snapshotKey(fixedInput(request)) ||
      !scheduledVisits(base.original).some((v) => v.role === "optional" && v.place.id === targetId) ||
      [...request.must_visit_places, ...(request.optional_places ?? [])].some((p) => p.id === targetId)) return "预览与原要求不一致，不能采用。";
  const visits = scheduledVisits(response);
  if (response.unscheduled.length || request.must_visit_places.some((p) => !visits.some((v) => v.role === "must_visit" && v.place.id === p.id))) return "预览未完整安排必去地点，本轮不能采用；原方案保持不变。";
  if (!visits.length) return "预览没有实际安排的游览地点，本轮不能采用；原方案保持不变。";
  return null; // A valid partial with all required visits is deliberately allowed.
}

export function compareSchedules(before: ScheduleResponse, after: ScheduleResponse) {
  const original = scheduledVisits(before), proposed = scheduledVisits(after);
  const beforeById = new Map(original.map((v) => [v.place.id, v]));
  const afterById = new Map(proposed.map((v) => [v.place.id, v]));
  return {
    added: proposed.filter((v) => !beforeById.has(v.place.id)), removed: original.filter((v) => !afterById.has(v.place.id)),
    retained: original.flatMap((old) => { const next = afterById.get(old.place.id); return next ? [{ before: old, after: next, changed: old.date !== next.date || old.start !== next.start || old.end !== next.end }] : []; }),
    returns: [...new Set([...before.days.map((d) => d.date), ...after.days.map((d) => d.date)])].sort().map((date) => ({ date, before: before.days.find((d) => d.date === date)?.return_time ?? null, after: after.days.find((d) => d.date === date)?.return_time ?? null })),
  };
}
