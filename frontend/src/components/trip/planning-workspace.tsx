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
export default function PlanningWorkspace({ request, initial, origin = "new", requirementsEdited = false, onEdit, onDirty, children }: {
  request: TripRequest; initial?: WorkspaceInputs; origin?: "new" | "history" | "demo"; requirementsEdited?: boolean; onEdit: (context: WorkspaceEdit) => void; onDirty?: () => void; children?: ReactNode;
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
    if (origin === "demo") invalidate(); // Only the explicitly isolated legacy example retains its reset behavior.
    onEdit({ isCurrent: () => alive.current,
      read: () => structuredClone({ request, ...schedule.readInputs(), candidates: candidates.readSnapshot(), costs: costRef.current }), invalidate });
  };
  const go = (id: string) => {
    const section = result.current?.querySelector<HTMLDetailsElement>(`[data-step="${id}"]`);
    if (!alive.current || !section) return;
    section.open = true;
    section.querySelector<HTMLElement>("summary")?.focus();
    section.scrollIntoView({ block: "start", behavior: "smooth" });
  };
  return <div ref={result} tabIndex={-1} aria-label="旅行结果" className="scroll-mt-6 outline-none">
    {/* Derive guidance from the same version-guarded state as the official draft. */}
    {requirementsEdited && schedule.state.status !== "success" && <p role="status" aria-label="需求修改后的草案状态" className="mb-5 rounded-xl bg-white p-4 text-sm leading-6">
      {schedule.state.status === "loading" ? "需求已应用，正在生成新的正式草案。" : "需求已应用，当前尚无有效正式草案；请主动重新生成。"}
    </p>}
    <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
      <p role="status" className="text-sm font-medium text-[#315f51]">{schedule.state.status === "success" ? `已有有效正式草案${schedule.state.response?.status === "complete" ? "" : "（请核对部分／未安排原因）"}，可核对费用并保存${origin === "history" ? "为新记录" : ""}。` : schedule.state.status === "loading" ? "正在生成正式草案…" : "旅行需求已确认，当前尚无有效正式草案。"}</p>
      <div className="flex flex-wrap gap-3">
        <button type="button" onClick={editRequest} className="min-h-11 rounded-full border border-[#315f51] bg-white px-5 py-2 text-sm font-semibold text-[#315f51]">修改旅行需求</button>
      </div>
    </div>
    <p className="mb-4 text-sm leading-6">{request.must_visit_places?.length ? "已有必去地点，可直接设置并生成草案，无需先获取可选候选。" : "尚无必去地点：请先主动获取并保留至少一个可选候选，或修改需求添加必去。"} 天气、AI 与调整均为按需使用，不是生成或保存的前置条件。</p>
    <nav aria-label="规划步骤" className="mb-5 flex flex-wrap gap-2">{[["prepare", "1 · 需求与地点准备"], ["draft", "2 · 生成与查看草案"], ["review", "3 · 核对、调整与保存"]].map(([id, label]) => <button type="button" key={id} onClick={() => go(id)} className="min-h-11 rounded-xl border border-[#315f51]/30 bg-white px-4 py-2 text-sm text-[#315f51]">{label}</button>)}</nav>
    <WorkflowSection step="prepare" title="需求与地点准备">
    <ConfirmedPlaces request={request} restored={origin === "history"} demo={origin === "demo"} />
    <CandidatePreparation request={request} pool={candidates} restored={origin === "history"} />
    </WorkflowSection>
    <WorkflowSection step="draft" title="生成与查看草案">
    <SchedulePreview request={request} preview={schedule} restored={origin === "history"} />
    </WorkflowSection>
    <WorkflowSection step="review" title="核对、调整与保存">
    <p className="text-sm leading-6">费用仅作核对，不影响排程或采用资格。天气检查和 AI 调整可跳过，已有有效草案即可保存；调整预览须明确采用才改变正式行程。</p>
    {origin === "history" && <p className="mt-5 text-sm text-[#56605c]">带入的费用仅为历史用户原估算，不是新报价。日期或人数确认变化后全部清空；没有新正式草案前只编辑估算，不作预算结论。</p>}
    {initial && schedule.state.status !== "success" && <section aria-label="工作副本费用输入" className="mt-4 rounded-2xl border border-[#315f51]/20 bg-white p-5">
      <h2 className="text-xl font-semibold">用户费用估算 · 尚未核算</h2>
      <p className="mt-2 text-sm">单位：人民币元，全部旅客合计。空白为未知，明确填 0 才计零；非法输入不会改成零。仅已安排的地点最终计入正式费用。</p>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">{restoredEstimateRows(request, [...request.must_visit_places ?? [], ...schedule.optionalPlaces], costInputs).map(row => <CostEstimateInput key={row.key} row={row} onChange={editCost} />)}</div>
    </section>}
    <ScheduleBudget request={request} response={schedule.state.status === "success" ? schedule.state.response : null} inputs={costInputs} onEdit={editCost} />
    <WeatherForecast request={weatherRequest} weather={weather} />
    <ScheduleExplanation explanation={explanation} />
    <NaturalAdjustment adjustment={adjustment} response={schedule.state.status === "success" ? schedule.state.response : null} />
    <WeatherScheduleCheck check={weatherCheck} onEdit={editRequest} onPreview={adjustment.start} previewLoading={adjustment.loading} />
    <ScheduleAdjustment adjustment={adjustment} annotations={weatherCheck.annotations} costInputs={costInputs} onEditCost={editCost} />
    <SaveTrip schedule={schedule} requirements={request} getCosts={() => costRef.current} />
    </WorkflowSection>
    {children}
  </div>;
}

// Native disclosure only changes presentation. Children (especially SaveTrip's
// frozen uncertain-write operation) remain mounted, even while collapsed.
function WorkflowSection({ step, title, children }: { step: string; title: string; children: ReactNode }) {
  return <details open data-step={step} className="mb-6 min-w-0 scroll-mt-5 rounded-2xl border border-[#315f51]/20 p-3 sm:p-5">
    <summary className="min-h-11 cursor-pointer text-lg font-semibold text-[#18392f]">{title}</summary>
    <div className="min-w-0 pt-3">{children}</div>
  </details>;
}
