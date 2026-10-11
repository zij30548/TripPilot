"use client";

import type { useScheduleAdjustment } from "@/lib/use-schedule-adjustment";
import { scheduledVisits } from "@/lib/weather-schedule-check";
import type { ScheduleResponse } from "@/types/schedule";

export default function NaturalAdjustment({ adjustment, response }: { adjustment: ReturnType<typeof useScheduleAdjustment>; response: ScheduleResponse | null }) {
  const visits = scheduledVisits(response);
  const eligible = visits.filter(v => v.role === "optional");
  const natural = adjustment.state.source === "natural";
  return <section aria-label="自然语言调整" className="mt-6 min-w-0 rounded-2xl border border-[#315f51]/30 bg-white p-5 sm:p-7">
    <h2 className="text-xl font-semibold text-[#18392f]">用一句话调整行程</h2>
    <p className="mt-2 text-sm leading-6 text-[#56605c]">目前只支持排除当前已安排的一个可选地点。必去保留；先生成预览，明确采用后才改变正式方案。不自动选择室内或更便宜的替代地点。</p>
    {!eligible.length && <p className="mt-3 text-sm">请先生成含可选地点的正式草案，其他调整暂不支持。</p>}
    {eligible.length > 0 && <details className="mt-3 text-sm"><summary className="cursor-pointer">可调整的地点（同名请核对 ID）</summary><ul className="mt-2 space-y-2">{eligible.map(v => <li key={v.place.id} className="break-words">{v.place.name} · {v.date}<br />{v.place.address ?? "地址未知"} · ID：{v.place.id}</li>)}</ul></details>}
    <label className="mt-4 block text-sm font-medium" htmlFor="natural-adjustment-text">调整要求</label>
    <textarea id="natural-adjustment-text" value={adjustment.instruction} maxLength={400} rows={3} onChange={e => adjustment.setInstruction(e.target.value)}
      placeholder="例如：不去延中广场公园了，必去地点保留。" className="mt-2 w-full rounded-xl border border-[#315f51]/30 p-3 text-sm" />
    <p className="mt-2 text-xs leading-5 text-[#68726c]">输入不会自动请求。修改这句话会取消旧解析／预览，但不会改动正式行程。一次只处理一个调整，不执行多项请求中的一部分。</p>
    <button type="button" disabled={!eligible.length || adjustment.loading || !adjustment.instruction.trim() || adjustment.instruction.length > 400}
      onClick={() => void adjustment.startNatural()} className="mt-4 min-h-11 rounded-xl bg-[#18392f] px-5 py-2 text-sm font-semibold text-white disabled:opacity-40">
      {natural && adjustment.state.status === "parsing" ? "正在理解要求…" : natural && adjustment.loading ? "正在生成预览…" : "生成调整预览"}</button>
    {natural && <p role="status" className="mt-3 text-sm">{adjustment.state.message ?? (adjustment.state.status === "ready" ? "预览已生成，尚未采用。" : "")}</p>}
    {natural && adjustment.loading && <button type="button" onClick={adjustment.cancel} className="ml-3 min-h-11 rounded-xl border px-4 py-2 text-sm">取消自然语言调整</button>}
    {natural && adjustment.state.status === "ready" && <a href="#schedule-adjustment" className="mt-3 block text-sm underline">查看新旧方案并确认是否采用</a>}
  </section>;
}
