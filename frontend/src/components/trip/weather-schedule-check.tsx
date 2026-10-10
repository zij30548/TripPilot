"use client";

import type { useWeatherScheduleCheck } from "@/lib/use-weather-schedule-check";
import { exposureLabels, type VisitExposure } from "@/lib/weather-schedule-check";

type Controller = ReturnType<typeof useWeatherScheduleCheck>;
const time = (value: string | number) => new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false });
const button = "min-h-11 rounded-xl border border-[#315f51]/30 px-4 py-2 text-sm font-medium text-[#315f51] disabled:cursor-not-allowed disabled:opacity-50";

export default function WeatherScheduleCheck({ check, onEdit }: { check: Controller; onEdit: () => void }) {
  return <section aria-label="天气与行程检查" className="mt-6 min-w-0 rounded-3xl border border-[#34657b]/20 bg-[#f1f7fa] p-5 sm:p-8">
    <h2 className="text-xl font-semibold text-[#18392f]">天气与行程检查</h2>
    <p className="mt-2 text-sm leading-6 text-[#56605c]">先标注本次游览方式，再主动检查同日预报。标注是你的规划设置，不是高德确认的地点属性；修改标注不会改变草案。</p>
    {check.visits.length === 0 ? <p className="mt-4 text-sm">请先生成包含已安排地点的真实行程草案。</p> : <div className="mt-4 grid gap-3 sm:grid-cols-2">
      {check.visits.map((visit) => <label key={`${visit.date}:${visit.place.id}`} className="min-w-0 rounded-xl bg-white p-4 text-sm">
        <span className="block break-words font-semibold">{visit.place.name} · {visit.role === "must_visit" ? "必去" : "可选"}</span>
        <span className="my-2 block break-words text-xs text-[#68726c]">{visit.date} · {visit.start}—{visit.end} · {visit.place.address ?? "地址未知"}</span>
        <select aria-label={`${visit.date} ${visit.place.name} 游览方式`} value={check.annotations.get(visit.place.id) ?? "unknown"} onChange={(event) => check.annotate(visit.place.id, event.target.value as VisitExposure)} className="min-h-11 w-full rounded-lg border border-[#315f51]/25 bg-white px-3">
          {(["unknown", "outdoor", "indoor"] as const).map((mode) => <option key={mode} value={mode}>{exposureLabels[mode]}</option>)}
        </select>
      </label>)}
    </div>}
    <button type="button" className={`${button} mt-4 bg-white`} disabled={!check.canCheck} onClick={() => check.check()}>检查天气对行程的影响</button>
    {!check.canCheck && check.visits.length > 0 && <p className="mt-2 text-sm text-[#68726c]">请先取得天气，并等待天气、候选及草案查询结束后检查。</p>}
    <p role="status" className="mt-3 text-sm leading-6 text-[#315f51]">{check.notice}</p>
    {check.report && !check.valid && <p role="status" className="mt-3 text-sm text-[#8a5b19]">条件已变化，上次检查已失效。请准备好天气和草案后重新检查。</p>}
    {check.report && <div aria-label="天气检查报告" className={`mt-4 space-y-4 ${check.valid ? "" : "opacity-60"}`}>
      <p className="text-sm font-medium">{check.valid ? "本次检查" : "上次检查 · 已失效"} · {time(check.report.checkedAt)}</p>
      {check.report.showingPrevious && <p className="text-sm text-[#8a5b19]">基于上次成功查询的天气。{check.report.refreshError}</p>}
      <details className="rounded-xl bg-white p-3 text-xs leading-6 text-[#56605c]"><summary className="cursor-pointer text-sm">查看天气来源与检查范围</summary>
        <p>高德天气 · 上海市 · 上海时间</p><p>成功查询：{time(check.report.queriedAt)}</p><p>来源发布：{check.report.reportedAt ? time(check.report.reportedAt) : "未知"}</p>
        <p>只关联同日昼夜预报，未确认活动具体时段的天气，不划分精确日夜小时。未评估交通路段、营业、预约或安全适宜性。</p>
      </details>
      {check.report.days.map((day) => <section key={day.date} aria-label={`${day.date} 天气检查`} className="space-y-3">
        <h3 className="font-semibold">{day.date}</h3>
        {day.visits.length === 0 && <p className="text-sm text-[#68726c]">尚无已安排的游览地点，不检查未安排地点。</p>}
        {day.visits.map((visit) => <article key={visit.place.id} aria-label={`${day.date} ${visit.place.name} 检查结果`} className="min-w-0 rounded-xl border border-[#34657b]/15 bg-white p-4">
          <h4 className="break-words font-semibold">{visit.place.name} · {visit.role === "must_visit" ? "必去" : "可选"}</h4>
          <p className="mt-1 break-words text-xs text-[#68726c]">{visit.place.address ?? "地址未知"}</p>
          <p className="mt-2 text-sm">本次游览：{exposureLabels[visit.exposure]}（你的设置）</p>
          <p className="mt-2 text-sm leading-6">{!check.valid ? "上次依据已失效，当前未评估；请重新检查。" : visit.attention ? "需要关注：你标注为户外为主，同日部分时段预报有雨雪。可准备防雨、防滑用品，结合下方依据自行决定是否调整。" : visit.exposure === "indoor" ? "你设置为室内为主，但不能据此认定往返交通不受天气影响。" : visit.exposure === "unknown" ? "尚不能判断本次游览的天气暴露情况，请自行确认游览方式。" : "没有可据以提出雨雪调整建议的结论，请分别查看昼夜依据。"}</p>
          <div className="mt-3 grid gap-2 sm:grid-cols-2">{visit.periods.map((period) => <p key={period.period} className="rounded-lg bg-[#f4f7f5] p-2 text-sm leading-6">{period.period}：{!check.valid || period.status === "unassessed" ? "未评估" : period.status === "precipitation" ? "有雨雪预报" : "未触发雨雪规则"}</p>)}</div>
          <details className="mt-3 text-sm leading-6"><summary className="cursor-pointer">{check.valid ? "查看昼夜依据" : "查看上次昼夜依据（已失效）"}</summary>{visit.periods.map((period) => <p key={period.period}>{period.period}原文：{period.description ?? "未知"}。{period.explanation}</p>)}</details>
          {visit.role === "must_visit" ? <p className="mt-3 text-sm">必去要求不会在此排除；如需调整，请<button type="button" onClick={onEdit} className="min-h-11 px-1 underline">修改旅行需求</button>。</p> : <div className="mt-3">
            {visit.canAdjust ? <><p className="mb-2 text-xs leading-5 text-[#68726c]">是否调整由你选择，不是天气自动要求。将从本次全行程候选排除该地点，清除当前草案；随后需主动重新生成，可在候选区恢复。新进入的候选不是自动选择的室内替代。</p><button type="button" className={button} disabled={!check.valid} onClick={() => check.exclude(visit.place.id)}>排除此可选地点</button></> : <p className="text-xs leading-5 text-[#68726c]">天气依据不足，此处不提供天气调整操作；普通手动选择仍可在候选区进行。</p>}
          </div>}
        </article>)}
      </section>)}
    </div>}
  </section>;
}
