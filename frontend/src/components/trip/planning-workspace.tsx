"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import type { TripRequest } from "@/types/trip";
import type { WorkspaceInputs } from "@/lib/restore-trip";
import { restoredEstimateRows } from "@/lib/restore-trip";
import { useCandidatePool } from "@/lib/use-candidate-pool";
import { useSchedulePreview } from "@/lib/use-schedule-preview";
import { useScheduleExplanation } from "@/lib/use-schedule-explanation";
import { useWeatherForecast } from "@/lib/use-weather-forecast";
import { useWeatherCheckRevision, useWeatherScheduleCheck } from "@/lib/use-weather-schedule-check";
import { useScheduleAdjustment } from "@/lib/use-schedule-adjustment";
import { emptyCostInputs, updateCostInput, type UserCost } from "@/lib/schedule-costs";
import ConfirmedPlaces from "./confirmed-places";
import CandidatePreparation from "./candidate-preparation";
import SchedulePreview from "./schedule-preview";
import ScheduleExplanation from "./schedule-explanation";
import NaturalAdjustment from "./natural-adjustment";
import SaveTrip from "./save-trip";
import ScheduleBudget from "./schedule-budget";
import WeatherForecast from "./weather-forecast";
import WeatherScheduleCheck from "./weather-schedule-check";
import ScheduleAdjustment from "./schedule-adjustment";
import CostEstimateInput from "./cost-estimate-input";

export type WorkspaceEdit = { read: () => WorkspaceInputs; invalidate: () => void; isCurrent: () => boolean };

// Shared real workflow, independent from TripPlan and its Mock/map example.
// Initial inputs are installed atomically by lazy hook initialization, never effects.
export default function PlanningWorkspace({ request, initial, onEdit, onDirty, children }: {
  request: TripRequest; initial?: WorkspaceInputs; onEdit: (context: WorkspaceEdit) => void; onDirty?: () => void; children?: ReactNode;
}) {
  const [costInputs, setCostInputs] = useState(() => structuredClone(initial?.costs ?? emptyCostInputs()));
  const costRef = useRef(costInputs), alive = useRef(true), result = useRef<HTMLDivElement>(null);
  const checkRevision = useWeatherCheckRevision();
  const invalidateCheck = checkRevision.invalidate;
  const changed = useCallback(() => { invalidateCheck(); if (alive.current) onDirty?.(); }, [invalidateCheck, onDirty]);
  const schedule = useSchedulePreview(request, changed, initial);
  const explanation = useScheduleExplanation(schedule);
  const candidates = useCandidatePool(request, schedule.updateCandidates, initial?.candidates);
  const weatherRequest = { start_date: request.start_date, end_date: request.end_date };
  const weather = useWeatherForecast(weatherRequest, checkRevision.invalidate);
  const weatherCheck = useWeatherScheduleCheck({ schedule, weather, candidates }, checkRevision);
  const adjustment = useScheduleAdjustment({ schedule, candidates, weather, check: weatherCheck }, checkRevision);
  useEffect(() => {
    alive.current = true;
    result.current?.focus(); result.current?.scrollIntoView({ block: "start" });
    return () => { alive.current = false; };
  }, []);
  const editCost = (row: UserCost, value: string) => {
    if (!alive.current) return;
    costRef.current = updateCostInput(costRef.current, row, value);
    setCostInputs(costRef.current); onDirty?.();
  };
  const invalidate = () => { weatherCheck.clear(); weather.invalidate(); candidates.invalidate(); schedule.invalidate(); };
  const editRequest = () => {
    if (!alive.current) return;
    if (!initial) invalidate(); // Normal new-trip flow keeps its original edit/reset semantics.
    onEdit({ isCurrent: () => alive.current,
      read: () => structuredClone({ request, ...schedule.readInputs(), candidates: candidates.readSnapshot(), costs: costRef.current }), invalidate });
  };
  return <div ref={result} tabIndex={-1} aria-label="旅行结果" className="scroll-mt-6 outline-none">
    <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
      <p role="status" className="text-sm font-medium text-[#315f51]">{initial ? schedule.state.status === "success" ? "本副本已生成新的正式草案，可另存新记录。" : "基于历史创建的工作副本，当前尚无有效的新正式草案。" : "旅行需求已确认"}</p>
      <div className="flex flex-wrap gap-3">
        <button type="button" onClick={editRequest} className="min-h-11 rounded-full border border-[#315f51] bg-white px-5 py-2 text-sm font-semibold text-[#315f51]">修改旅行需求</button>
        <button type="button" disabled className="min-h-11 rounded-full border border-[#18201d]/10 px-5 py-2 text-sm text-[#68726c]">重新规划 · 暂未开放</button>
      </div>
    </div>
    <ConfirmedPlaces request={request} restored={!!initial} />
    <WeatherForecast request={weatherRequest} weather={weather} />
    <CandidatePreparation request={request} pool={candidates} restored={!!initial} />
    <SchedulePreview request={request} preview={schedule} restored={!!initial} />
    <SaveTrip schedule={schedule} requirements={request} getCosts={() => costRef.current} />
    <ScheduleExplanation explanation={explanation} />
    <NaturalAdjustment adjustment={adjustment} response={schedule.state.status === "success" ? schedule.state.response : null} />
    {initial && <p className="mt-5 text-sm text-[#56605c]">带入的费用仅为历史用户原估算，不是新报价。日期或人数确认变化后全部清空；没有新正式草案前只编辑估算，不作预算结论。</p>}
    {initial && schedule.state.status !== "success" && <section aria-label="工作副本费用输入" className="mt-4 rounded-2xl border border-[#315f51]/20 bg-white p-5">
      <h2 className="text-xl font-semibold">用户费用估算 · 尚未核算</h2>
      <p className="mt-2 text-sm">单位：人民币元，全部旅客合计。空白为未知，明确填 0 才计零；非法输入不会改成零。仅已安排的地点最终计入正式费用。</p>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">{restoredEstimateRows(request, [...request.must_visit_places ?? [], ...schedule.optionalPlaces], costInputs).map(row => <CostEstimateInput key={row.key} row={row} onChange={editCost} />)}</div>
    </section>}
    <ScheduleBudget request={request} response={schedule.state.status === "success" ? schedule.state.response : null} inputs={costInputs} onEdit={editCost} />
    <WeatherScheduleCheck check={weatherCheck} onEdit={editRequest} onPreview={adjustment.start} previewLoading={adjustment.loading} />
    <ScheduleAdjustment adjustment={adjustment} annotations={weatherCheck.annotations} costInputs={costInputs} onEditCost={editCost} />
    {children}
  </div>;
}
