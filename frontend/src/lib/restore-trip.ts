import { isSavedDetail, canonical, type SavedDetail } from "./saved-trips";
import { isTripRequest } from "./api";
import { emptyCandidateSnapshot, candidateItems, type CandidatePoolSnapshot } from "./candidate-pool-state";
import { emptyCostInputs, parseCostInput, type CostInputs, type UserCost } from "./schedule-costs";
import { scheduleClock, scheduleDates, scheduleMinutes } from "@/types/schedule";
import { isConfirmedPlace, type Place } from "@/types/place";
import type { TripRequest } from "@/types/trip";
import type { ScheduleSettings } from "./schedule-input";

export type WorkspaceInputs = { request: TripRequest; settings: ScheduleSettings; candidates: CandidatePoolSnapshot; costs: CostInputs };
export type RestoredTrip = { sessionId: string; source: SavedDetail; inputs: WorkspaceInputs };

// No response/success state in working inputs. Source is an independent read-only reference.
export function restoreSavedTrip(value: unknown, sessionId: string): RestoredTrip {
  if (!sessionId || !isSavedDetail(value)) throw new Error("历史快照无效或版本不支持，未创建工作副本。");
  const { requirements, schedule, costs } = value.snapshot;
  return structuredClone({ sessionId, source: structuredClone(value), inputs: {
    request: structuredClone(requirements), costs: structuredClone(costs),
    settings: { transportMode: schedule.request.transport_mode,
      stays: Object.fromEntries(schedule.request.duration_settings.map(s => [s.place_id, { value: String(s.minutes), source: s.source }])),
      lunch: { enabled: schedule.request.lunch.enabled, start: schedule.request.lunch.start_time, end: schedule.request.lunch.end_time } },
    candidates: { ...emptyCandidateSnapshot(), history: { kind: "history", recordId: value.id, name: value.name,
      savedAt: value.saved_at, places: structuredClone(schedule.request.optional_places ?? []) } },
  } });
}

export function validEditedRequirements(value: unknown): value is TripRequest {
  if (!isTripRequest(value) || !isConfirmedPlace(value.accommodation_place) || !scheduleDates(value.start_date, value.end_date) ||
      !Number.isSafeInteger(value.travelers) || value.travelers > 1000) return false;
  const start = scheduleMinutes(scheduleClock(value.daily_start_time)), end = scheduleMinutes(scheduleClock(value.daily_end_time));
  return start !== null && end !== null && start < end;
}

export function applyEditedRequirements(current: WorkspaceInputs, request: TripRequest): { inputs: WorkspaceInputs; notices: string[] } {
  if (!validEditedRequirements(request)) throw new Error("请检查旅行日期、人数、地点和每日时间窗口。");
  const before = current.request;
  const homeChanged = canonical(before.accommodation_place) !== canonical(request.accommodation_place);
  const resetCandidates = homeChanged || canonical(before.must_visit_places) !== canonical(request.must_visit_places) ||
    canonical([...before.interests].sort()) !== canonical([...request.interests].sort());
  const resetCosts = before.start_date !== request.start_date || before.end_date !== request.end_date || before.travelers !== request.travelers;
  const candidates = resetCandidates ? emptyCandidateSnapshot() : structuredClone({ ...current.candidates, loading: false });
  const places = [...(request.must_visit_places ?? []), ...candidateItems(candidates).filter(c => c.role === "optional").map(c => c.place)];
  const ids = new Set(places.map(p => p.id));
  const stays = Object.fromEntries(places.map(p => [p.id, structuredClone(current.settings.stays[p.id] ?? { value: "60", source: "default" as const })]));
  const costs = resetCosts ? emptyCostInputs() : {
    tickets: Object.fromEntries(Object.entries(current.costs.tickets).filter(([id]) => ids.has(id))),
    meals: Object.fromEntries(Object.entries(current.costs.meals).filter(([date]) => scheduleDates(request.start_date, request.end_date)!.includes(date))),
    accommodation: homeChanged ? "" : current.costs.accommodation, other: current.costs.other,
  };
  const notices = ["需求已应用，旧正式草案及相关解读、检查和预览已清除；请主动重新生成。"];
  if (resetCandidates) notices.push("住宿、必去或兴趣已变化，可选清单及本副本排除选择已清空，请主动重新获取候选。");
  if (resetCosts) notices.push("日期或人数已变化，全部用户费用估算已清空；没有自动缩放或搬移旧餐饮费。");
  else if (homeChanged) notices.push("住宿参考点已变化，住宿估算已清空；其他仍相关的原估算保留。");
  return { inputs: structuredClone({ request, settings: { ...current.settings, stays }, candidates, costs }), notices };
}

// Input rows only; no budget/totals without a freshly generated official result.
export function restoredEstimateRows(request: TripRequest, places: Place[], costs: CostInputs): UserCost[] {
  const row = (v: Omit<UserCost, "money">): UserCost => ({ ...v, money: parseCostInput(v.value) });
  return [
    ...Array.from(new Map(places.map(p => [p.id, p])).values()).map(p => row({ key: `ticket-${p.id}`, category: "ticket", placeId: p.id,
      label: `${p.name} · 门票／入场费`, detail: `${p.address ?? "地址未知"} · POI ${p.id}`, value: costs.tickets[p.id] ?? "" })),
    ...scheduleDates(request.start_date, request.end_date)!.map(date => row({ key: `meal-${date}`, category: "meal", date,
      label: `${date} · 全天餐饮费`, detail: "全部旅客全天合计，包括没有安排景点的日期。", value: costs.meals[date] ?? "" })),
    row({ key: "accommodation", category: "accommodation", label: "全程住宿总额", detail: "全部旅客合计，不推算住宿晚数。", value: costs.accommodation }),
    row({ key: "other", category: "other", label: "全程其他费用", detail: "全程合计，不再乘人数。", value: costs.other }),
  ];
}
