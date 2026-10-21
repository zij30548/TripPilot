"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useSchedulePreview } from "./use-schedule-preview";
import { useCandidatePool } from "./use-candidate-pool";
import { useWeatherForecast } from "./use-weather-forecast";
import { useWeatherCheckRevision, useWeatherScheduleCheck } from "./use-weather-schedule-check";
import { useScheduleAdjustment } from "./use-schedule-adjustment";
import { useScheduleExplanation } from "./use-schedule-explanation";
import { useItineraryQuery } from "./use-itinerary-query";
import { useSaveTrip, useSavedHistory } from "./use-saved-trips";
import { applyEditedRequirements, restoreSavedTrip, type WorkspaceInputs, type RestoredTrip } from "./restore-trip";
import { calculateScheduleCosts, updateCostInput, type UserCost } from "./schedule-costs";
import { candidateItems } from "./candidate-pool-state";
import { scheduleClock, scheduleDates, scheduleMinutes } from "@/types/schedule";
import { scheduledVisits } from "./weather-schedule-check";
import { adoptionIssue } from "./schedule-adjustment";
import { isDialogueAction } from "./dialogue-action";
import { adjustmentDialogueText, costsDialogueText, historyDialogueText, scheduleDialogueText, weatherCheckDialogueText, weatherDialogueText } from "./dialogue-travel-text";

export type PlanningAction = { name: string; arguments: Record<string, unknown> };
export type PlanningReply = { text: string; changed?: boolean; view?: "map"; date?: string; edgeId?: string; replacement?: WorkspaceInputs; restored?: RestoredTrip };

// Exact IDs, names, or the ordinal in the displayed collection. A same-name
// collision is a clarification, never a first-match place selection.
export function resolveDialogueSelector<T extends {id: string; name: string}>(items: T[], selector: unknown): T | null {
  if (typeof selector !== "string" || !selector.trim()) return null;
  const text = selector.trim(), byId = items.filter(item => item.id === text);
  if (byId.length === 1) return byId[0];
  if (/^[1-9]\d*$/.test(text)) return items[Number(text) - 1] ?? null;
  const byName = items.filter(item => item.name === text);
  return byName.length === 1 ? byName[0] : null;
}

/** Mount only after validated inputs exist, keyed by a new workspace session.
 * No invented empty TripRequest and no effect publishes a historical response.
 * Hooks are the same owners used by the retained legacy planning workspace.
 */
export function useDialoguePlanning(initial: WorkspaceInputs, canOperate: () => boolean, canRetryFrozenSave: () => boolean = canOperate) {
  const [seed] = useState(() => structuredClone(initial));
  const [costs, setCosts] = useState(() => structuredClone(seed.costs));
  const costRef = useRef(costs), alive = useRef(true), version = useRef(0);
  const operation = useRef<{id: number; cancel: () => void} | null>(null), operationVersion = useRef(0);
  const allowed = useCallback(() => alive.current && canOperate(), [canOperate]);
  const checkRevision = useWeatherCheckRevision();
  const invalidateCheck = checkRevision.invalidate;
  const changed = useCallback(() => { version.current++; invalidateCheck(); }, [invalidateCheck]);
  const schedule = useSchedulePreview(seed.request, changed, seed, allowed);
  const candidates = useCandidatePool(seed.request, schedule.updateCandidates, seed.candidates, allowed);
  const weather = useWeatherForecast({ start_date: seed.request.start_date, end_date: seed.request.end_date,
    ...(seed.request.destination_id ? {destination_id:seed.request.destination_id} : {}) }, checkRevision.invalidate, allowed);
  const check = useWeatherScheduleCheck({schedule, candidates, weather}, checkRevision);
  const adjustment = useScheduleAdjustment({schedule,candidates,weather,check},checkRevision,allowed);
  const explanation = useScheduleExplanation(schedule);
  const getCosts = useCallback(() => costRef.current, []);
  const queryAssistant = useItineraryQuery(schedule, getCosts);
  const retryAllowed = useCallback(() => alive.current && canRetryFrozenSave(), [canRetryFrozenSave]);
  const saving = useSaveTrip(schedule, seed.request, getCosts, allowed, retryAllowed);
  const history = useSavedHistory();
  const cancelOperation = useCallback(() => {
    ++operationVersion.current;
    const active = operation.current; operation.current = null; active?.cancel();
  }, []);
  useEffect(() => { alive.current = true; return () => { alive.current = false; cancelOperation(); }; }, [cancelOperation]);
  const invalidate = () => {
    cancelOperation();
    check.clear(); weather.invalidate(); candidates.invalidate(); schedule.invalidate();
  };
  const readInputs = (): WorkspaceInputs => structuredClone({request:seed.request,...schedule.readInputs(),candidates:candidates.readSnapshot(),costs:costRef.current});
  const readVersion = () => version.current;
  const budget = () => calculateScheduleCosts(schedule.capture()?.original ?? null, seed.request, costRef.current);
  const editCost = (row: UserCost, value: string): boolean => {
    if (!allowed() || value.length > 256) return false;
    version.current++; queryAssistant.costsChanged();
    costRef.current = updateCostInput(costRef.current, row, value); setCosts(costRef.current); return true;
  };
  const places = () => [...(seed.request.must_visit_places ?? []), ...candidateItems(candidates.readSnapshot()).filter(item => item.role === "optional").map(item=>item.place)];
  const previewText = () => {
    const current = adjustment.readState();
    if (current.status !== "ready" || !current.input || !current.response) return current.message ?? "当前没有可用调整预览。";
    const issue = adoptionIssue(current.input.base,current.input.targetId,current.input.proposed.request,current.response);
    return adjustmentDialogueText(current.input.base.original,current.response,current.input.targetName,seed.request,costRef.current) +
      `\n${issue ?? "原正式方案尚未变化；看过预览后，可明确采用或取消。"}`;
  };
  const candidatesText = () => {
    const snapshot = candidates.readSnapshot(), rows = candidateItems(snapshot).filter(item=>item.role==="optional");
    return [snapshot.message ?? (snapshot.history ? "候选来自历史存档，未重新检索。" : "本次候选仅是地点准备，尚未采用新的行程。"),
      ...rows.map((item,index)=>`${index+1}. ${item.place.name}（${item.place.id}） · ${item.place.address ?? "地址未知"}${snapshot.excludedIds.has(item.place.id) ? " · 已排除" : ""}`),
      ...(rows.length ? ["排程只使用当前前 3 个有效未排除可选地点；是否安排以生成结果为准。"] : ["暂无可用可选候选，已确认的必去要求仍保留。"])].join("\n");
  };
  const execute = async (action: PlanningAction, signal?: AbortSignal): Promise<PlanningReply> => {
    // An uncertain save owns immutable inputs. Resolving that exact operation
    // needs the original account authority, not a still-valid editable draft.
    const frozenRetry = ["save_trip", "retry_save"].includes(action.name) && saving.readState().status === "uncertain";
    const operationAllowed = () => frozenRetry ? retryAllowed() : allowed();
    if (!operationAllowed() || signal?.aborted) return {text:"当前工作区已失效，请先确认当前需求和地点。"};
    if (action.name === "cancel_operation") { cancelOperation(); return {text:"已停止等待；已发送的上游调用或存档写入可能已经处理，不会自动重试。"}; }
    if (operation.current) return {text:"上一项操作仍在进行，请等待或明确取消。"};
    const id = ++operationVersion.current;
    const slot = {id,cancel:()=>{}}; operation.current = slot;
    const onAbort = () => { if (operation.current === slot) cancelOperation(); };
    signal?.addEventListener("abort",onAbort,{once:true});
    const current = () => operationAllowed() && operation.current === slot && operationVersion.current === id && !signal?.aborted;
    const cancelled = (): PlanningReply => ({text:"这次操作已取消或上下文已变化，未将旧结果用于当前对话。"});
    const a = action.arguments;
    try {
      if (action.name === "generate_schedule") {
        slot.cancel = schedule.invalidate;
        const ok = await schedule.query(); if (!current()) return cancelled();
        const capture = schedule.capture();
        return {text: ok && capture ? scheduleDialogueText(capture.original) : schedule.readState().message ?? schedule.validationMessage ?? "本次未生成有效草案，请查看当前输入与服务错误后主动重试。",changed:true};
      }
      if (action.name === "read_schedule" || action.name === "get_day_schedule") {
        const capture = schedule.capture();
        if (a.date != null && a.day != null) return {text:"请只指定完整日期或第几天中的一种。"};
        const dates = scheduleDates(seed.request.start_date,seed.request.end_date)!;
        const date = typeof a.date === "string" ? a.date : typeof a.day === "number" && Number.isInteger(a.day) ? dates[a.day-1] : undefined;
        if ((a.date != null || a.day != null) && (!date || !dates.includes(date))) return {text:"指定日期不在当前旅行范围内，请核对完整日期或第几天。"};
        return {text:scheduleDialogueText(capture?.original ?? null,date)};
      }
      if (action.name === "read_budget" || action.name === "get_budget_summary") return {text:costsDialogueText(budget())};
      if (action.name === "prepare_candidates") {
        slot.cancel = candidates.cancelQuery;
        await candidates.query(); if (!current()) return cancelled();
        return {text:candidatesText(),changed:true};
      }
      if (action.name === "exclude_candidate" || action.name === "restore_candidate") {
        const rows = candidateItems(candidates.readSnapshot()).filter(item=>item.role==="optional").map(item=>item.place);
        const place = resolveDialogueSelector(rows,a.selector); if (!place) return {text:"可选地点不明确，请使用当前候选的编号、完整名称或 POI ID。必去地点不能从这里排除。"};
        if (action.name === "exclude_candidate") candidates.exclude(place.id); else candidates.restore(place.id);
        return {text:`已${action.name === "exclude_candidate" ? "排除" : "恢复"}“${place.name}”。原正式草案已失效，需主动重新生成。\n${candidatesText()}`,changed:true};
      }
      if (action.name === "set_stay") {
        const selected = [...(seed.request.must_visit_places ?? []), ...schedule.optionalPlaces];
        const place = resolveDialogueSelector(selected,a.selector);
        if (!place || !Number.isInteger(a.minutes) || Number(a.minutes)<15 || Number(a.minutes)>480) return {text:"请指定当前必去或本次可选地点，并提供 15～480 的整数停留分钟。"};
        schedule.setStayMinutes(place.id,String(a.minutes)); return {text:`已将“${place.name}”停留设置为 ${a.minutes} 分钟（用户设置），请主动重新生成。`,changed:true};
      }
      if (action.name === "set_transport") {
        if (a.mode !== "walking" && a.mode !== "transit") return {text:"当前仅支持步行或公交／地铁参考模式。"};
        schedule.setTransportMode(a.mode); return {text:`交通模式已设为${a.mode === "walking" ? "步行" : "公交／地铁参考"}；未自动生成或查询路线。`,changed:true};
      }
      if (action.name === "set_lunch") {
        const start = scheduleMinutes(a.start),end=scheduleMinutes(a.end);
        if (typeof a.enabled !== "boolean" || start===null || end===null || start>=end ||
            (a.enabled && (start<scheduleMinutes(scheduleClock(seed.request.daily_start_time))! || end>scheduleMinutes(scheduleClock(seed.request.daily_end_time))!))) return {text:"午餐开始、结束需为有效时刻，启用时须完整位于每日时间窗口内。"};
        schedule.setLunchStart(String(a.start)); schedule.setLunchEnd(String(a.end)); schedule.setLunchEnabled(a.enabled);
        return {text:`午餐预留已${a.enabled ? `设为 ${a.start}—${a.end}` : "关闭"}，请主动重新生成。`,changed:true};
      }
      if (action.name === "set_daily_window") {
        const start=scheduleMinutes(a.start),end=scheduleMinutes(a.end);
        if(start===null||end===null||start>=end)return {text:"每日结束时间必须晚于开始时间，请使用 HH:mm。"};
        const next=applyEditedRequirements(readInputs(),{...seed.request,daily_start_time:String(a.start),daily_end_time:String(a.end)});
        // The parent installs this complete replacement under a fresh session key.
        return {text:`每日窗口已准备为 ${a.start}—${a.end}。确认应用后原草案失效；午餐不兼容时需主动修正。`,replacement:next.inputs,changed:true};
      }
      if(action.name === "set_cost") {
        if(typeof a.value!=="string" || a.value.length>256)return {text:"费用须为不超过 256 字的原始输入；空白表示未知。"};
        let row: UserCost | null=null;
        if(a.kind==="ticket") {
          const all=new Map(places().map(place=>[place.id,place]));
          const preview=adjustment.readState();
          if(adjustment.canUsePreview() && preview.response) scheduledVisits(preview.response).forEach(visit=>all.set(visit.place.id,visit.place));
          const place=resolveDialogueSelector([...all.values()],a.selector);
          if(place)row={category:"ticket",key:`ticket-${place.id}`,placeId:place.id,label:`${place.name} · 门票／入场费`,detail:"用户估算",value:a.value,money:{status:"unknown"}};
        } else if(a.kind==="meal") {
          const dates=scheduleDates(seed.request.start_date,seed.request.end_date)!;
          const selected=typeof a.selector==="string"?a.selector:"";
          const date=dates.includes(selected)?selected:/^[1-7]$/.test(selected)?dates[Number(selected)-1]:undefined;
          if(date)row={category:"meal",key:`meal-${date}`,date,label:`${date} · 全天餐饮费`,detail:"全部旅客合计",value:a.value,money:{status:"unknown"}};
        } else if(a.kind==="accommodation"||a.kind==="other") row={category:a.kind,key:a.kind,label:a.kind==="accommodation"?"全程住宿总额":"全程其他费用",detail:"全部旅客全程合计",value:a.value,money:{status:"unknown"}};
        if(!row)return {text:"费用对应的地点或日期不明确，请指定当前输入中的地点或完整旅行日期。"};
        if(!editCost(row,a.value))return cancelled();
        return {text:`已保留${row.label}的用户估算输入；金额为空或非法时不会转换成零。\n${costsDialogueText(budget())}`,changed:true};
      }
      if(action.name === "query_weather") {
        slot.cancel=weather.invalidate; await weather.query(); if(!current())return cancelled();
        const state=weather.readState();return {text:[state.message,weatherDialogueText(state.response)].filter(Boolean).join("\n")};
      }
      if(action.name === "read_weather") {const state=weather.readState();return {text:[state.message,weatherDialogueText(state.response)].filter(Boolean).join("\n")};}
      if(action.name === "check_weather") {
        const checked=check.check();
        return {text:weatherCheckDialogueText(check.readReport(),checked),changed:checked};
      }
      if(action.name === "set_visit_mode") {
        const visits=scheduledVisits(schedule.capture()?.original ?? null), place=resolveDialogueSelector(visits.map(visit=>visit.place),a.selector);
        if(!place || !["indoor","outdoor","unknown"].includes(String(a.mode)))return {text:"请指定当前已安排的地点，并明确室内、户外或不确定。"};
        check.annotate(place.id,a.mode==="indoor"||a.mode==="outdoor"?a.mode:"unknown");
        return {text:`已记录“${place.name}”的游览方式。原天气检查和相应预览已失效，请主动重新检查。`,changed:true};
      }
      if(action.name === "preview_exclusion") {
        const visits=scheduledVisits(schedule.capture()?.original ?? null).filter(visit=>visit.role==="optional"), place=resolveDialogueSelector(visits.map(visit=>visit.place),a.selector);
        if(!place)return {text:"只能预览排除当前已安排的一个可选地点。必去、未安排或同名歧义地点不能作为目标。"};
        slot.cancel=adjustment.cancelPending; await adjustment.startExplicit(place.id);if(!current())return cancelled();
        return {text:previewText()};
      }
      if(action.name === "read_preview")return {text:previewText()};
      if(action.name === "cancel_preview")return {text:adjustment.cancel()?"已取消预览，原正式草案和主动填写的费用估算保留。":"预览已经变化，未处理旧取消请求。"};
      if(action.name === "adopt_preview") {
        const ok=adjustment.adopt();return {text:ok?`已采用刚才查看的调整方案，没有新增路线查询。旧天气检查已失效。\n${scheduleDialogueText(schedule.capture()?.original ?? null)}`:"当前预览已过期或不满足采用条件，原正式草案保持不变。",changed:ok};
      }
      if(action.name === "save_trip" || action.name === "retry_save") {
        slot.cancel=saving.cancelPending;
        // Uncertain writes retain their frozen operation even if the user sends
        // the same save instruction again after changing estimates.
        if(saving.readState().status==="uncertain")await saving.retry();
        else if(typeof a.name==="string")await saving.save(a.name);
        else return {text:"请提供 1～80 字的存档名称。"};
        if(!current())return cancelled();return {text:saving.readState().message};
      }
      if(action.name === "list_saved") {
        if(!isDialogueAction(action))return {text:"历史分页参数无效，请回复“查看历史”或“查看第2页历史”。"};
        slot.cancel=history.cancel;await history.load(null,Number(a.offset));if(!current())return cancelled();const state=history.readState();
        return {text:state.list?historyDialogueText(state.list):state.error||"未取得历史列表。"};
      }
      if(action.name === "read_saved" || action.name === "restore_saved") {
        const state=history.readState(), rows=state.list?.items ?? (state.detail?[state.detail]:[]), selected=resolveDialogueSelector(rows,a.selector);
        if(!selected)return {text:"请先列出历史记录，再用完整名称、记录 ID 或列表编号明确选择一份；同名不会自动选择。"};
        slot.cancel=history.cancel;await history.load(selected.id);if(!current())return cancelled();const detail=history.readState().detail;
        if(!detail)return {text:history.readState().error||"未取得有效历史详情。"};
        if(action.name==="restore_saved")return {text:`已准备“${detail.name}”的独立输入副本，原存档不变。历史结果仅供参考，须主动重新生成才可另存；旧 v1 地点缺少归属时须重新搜索确认。`,restored:restoreSavedTrip(detail,crypto.randomUUID()),changed:true};
        return {text:historyDialogueText(detail)};
      }
      if(action.name === "explain_schedule") {
        if(!schedule.capture())return {text:scheduleDialogueText(null)};
        slot.cancel=explanation.cancelPending;await explanation.explain();if(!current())return cancelled();const state=explanation.readState();
        return {text:state.response?[...state.response.highlights.map(row=>row.text),...state.response.limitations].join("\n"):state.message??"未取得可核验的行程解读。"};
      }
      if(action.name === "show_map") {
        const capture=schedule.capture();if(!capture)return {text:scheduleDialogueText(null)};
        const date=typeof a.date==="string"?a.date:capture.original.days[0].date,day=capture.original.days.find(day=>day.date===date);
        if(!day)return {text:"地图日期不属于当前正式草案。"};
        if(a.edge_id!=null && (typeof a.edge_id!=="string"||!day.items.some(item=>item.edge_id===a.edge_id&&(item.kind==="walk"||item.kind==="transit"))))return {text:"这条交通段不属于当天正式时间轴，不能绘制。"};
        return {text:"已打开当前正式草案地图；仅展示当天实际地点及已有路线几何。",view:"map",date,...(typeof a.edge_id==="string"?{edgeId:a.edge_id}:{})};
      }
      return {text:"这项操作不属于当前规划控制器，尚未执行。"};
    } catch (error) {
      return current()?{text:error instanceof Error?error.message:"这项操作未完成，正式输入未被模型改写。"}:cancelled();
    } finally {
      signal?.removeEventListener("abort",onAbort);
      if(operation.current===slot)operation.current=null;
    }
  };
  return {request:seed.request,schedule,candidates,weather,check,adjustment,explanation,queryAssistant,saving,history,costs,getCosts,
    readInputs,readVersion,invalidate,cancelOperation,editCost,budget,candidatesText,previewText,execute};
}

export type DialoguePlanning = ReturnType<typeof useDialoguePlanning>;
