"use client";

import type { useScheduleAdjustment } from "@/lib/use-schedule-adjustment";
import { compareSchedules } from "@/lib/schedule-adjustment";
import { exposureLabels, type VisitExposure } from "@/lib/weather-schedule-check";
import { ScheduleResult } from "./schedule-preview";
import type { CostInputs, UserCost } from "@/lib/schedule-costs";
import ScheduleCostComparison from "./schedule-cost-comparison";

const button = "min-h-11 rounded-xl border border-[#315f51]/30 px-4 py-2 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50";
const time = (value: string) => new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false });
const mode = (value: string) => value === "walking" ? "步行" : "公交／地铁参考";
export default function ScheduleAdjustment({ adjustment, annotations, costInputs, onEditCost }: { adjustment: ReturnType<typeof useScheduleAdjustment>; annotations: ReadonlyMap<string, VisitExposure>; costInputs: CostInputs; onEditCost: (row: UserCost, value: string) => void }) {
  const { state, loading, canAdopt, adoptionIssue } = adjustment;
  if (state.status === "idle") return state.message ? <p role="status" className="mt-4 rounded-xl bg-[#edf3ef] p-4 text-sm text-[#315f51]">{state.message}</p> : null;
  const input = state.input, response = state.response;
  const differences = input && response ? compareSchedules(input.base.original, response) : null;
  return <section aria-label="调整预览" className="mt-6 min-w-0 rounded-3xl border-2 border-amber-300 bg-amber-50/40 p-5 sm:p-8">
    <h2 className="text-xl font-semibold text-[#18392f]">调整预览，尚未采用</h2>
    {input && <p className="mt-2 break-words text-sm">本次拟排除：{input.targetName}。采用后将从全行程候选排除，可在候选区恢复。</p>}
    <p className="mt-2 text-sm leading-6 text-[#56605c]">预览和取消不会修改原方案。两份方案来自不同查询批次的参考估时，时间变化不一定全部由排除地点造成；新地点不是自动选出的室内替代，也不代表已解决天气影响。</p>
    {loading && <p role="status" className="mt-4 text-sm">正在生成调整预览…原方案和正式候选保持不变。</p>}
    {state.message && <p role={state.status === "failed" ? "alert" : "status"} className="mt-4 text-sm text-[#835718]">{state.message}</p>}
    {response && input && differences && <>
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <p className="rounded-xl bg-white p-3 text-sm">原方案：{time(input.base.original.generated_at)} · {mode(input.base.original.request.transport_mode)}</p>
        <p className="rounded-xl bg-white p-3 text-sm">调整预览：{time(response.generated_at)} · {mode(response.request.transport_mode)}</p>
      </div>
      <p className="mt-3 text-sm">{response.request.must_visit_places.length ? `必去安排：原 ${input.base.original.request.must_visit_places.length - input.base.original.unscheduled.length}/${input.base.original.request.must_visit_places.length} 个 → 新 ${response.request.must_visit_places.length - response.unscheduled.length}/${response.request.must_visit_places.length} 个。` : "本次未设置必去地点。"} 未安排地点及逐日原因可在下方展开查看。</p>
      <section aria-label="新旧地点对比" className="mt-4 space-y-3 text-sm">
        <h3 className="font-semibold">实际安排地点的变化</h3>
        <p>新增 {differences.added.length} 个 · 移除 {differences.removed.length} 个 · 保留 {differences.retained.length} 个</p>
        {!differences.added.length && <p>本次没有补入新的已安排可选地点；这并不阻止采用已完整安排必去的有效方案。</p>}
        {differences.removed.map((v) => <p key={`removed-${v.place.id}`} className="break-words rounded-xl bg-white p-3">移除：{v.place.name} · {v.date} {v.start}—{v.end}<span className="mt-1 block text-xs">{v.place.address ?? "地址未知"}</span></p>)}
        {differences.added.map((v) => <p key={`added-${v.place.id}`} className="break-words rounded-xl bg-white p-3">新增：{v.place.name} · {v.date} {v.start}—{v.end}<span className="mt-1 block text-xs">{v.place.address ?? "地址未知"} · 本次游览：{exposureLabels[annotations.get(v.place.id) ?? "unknown"]}（你的设置）</span></p>)}
        {differences.retained.map(({ before, after, changed }) => <div key={before.place.id} className="break-words rounded-xl bg-white p-3">
          <p className="font-medium">保留：{before.place.name} · {before.role === "must_visit" ? "必去" : "可选"} · {changed ? "日期／时刻有变化" : "日期与时刻不变"}</p>
          <p className="mt-1 text-xs">{before.place.address ?? "地址未知"}</p><p>原：{before.date} {before.start}—{before.end}</p><p>新：{after.date} {after.start}—{after.end}</p>
        </div>)}
      </section>
      <section aria-label="住宿返程对比" className="mt-4 text-sm"><h3 className="font-semibold">每天预计返回住宿</h3>
        {differences.returns.map((d) => <p key={d.date} className="mt-2 rounded-xl bg-white p-3">{d.date} · 原：{d.before ?? "无返回时刻"} → 新：{d.after ?? "无返回时刻"}</p>)}
      </section>
      {adoptionIssue && <p role="alert" className="mt-4 text-sm text-[#835718]">{adoptionIssue}</p>}
      {state.status === "ready" && canAdopt ? <ScheduleCostComparison original={input.base.original} proposed={response} request={input.base.request} inputs={costInputs}
        onEdit={(row, value) => { if (adjustment.canUsePreview()) onEditCost(row, value); }} />
        : <p className="mt-4 text-sm text-[#835718]">当前预览不可用于费用比较；上方历史方案仍可查看，费用输入已关闭。</p>}
      <div className="mt-4 space-y-3">
        <details className="min-w-0 rounded-xl border border-[#315f51]/20 bg-white p-4"><summary className="cursor-pointer font-medium">查看原方案完整时间轴与安排情况</summary><ScheduleResult response={input.base.original} /></details>
        <details className="min-w-0 rounded-xl border border-[#315f51]/20 bg-white p-4"><summary className="cursor-pointer font-medium">查看调整预览完整时间轴与未安排原因</summary><ScheduleResult response={response} /></details>
      </div>
      <p className="mt-4 text-xs leading-6 text-[#68726c]">采用不会再次查询路线，将保存你看到的这份预览。采用后天气检查失效，需重新检查；不保证营业、预约、预算或实际交通可行性。</p>
    </>}
    <div className="mt-5 flex flex-wrap gap-3">
      {response && <button type="button" disabled={!canAdopt} className={`${button} bg-[#18392f] text-white`} onClick={adjustment.adopt}>采用此方案</button>}
      <button type="button" className={`${button} bg-white text-[#315f51]`} onClick={adjustment.cancel}>取消预览</button>
    </div>
  </section>;
}
