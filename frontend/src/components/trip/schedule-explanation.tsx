"use client";

import type { useScheduleExplanation } from "@/lib/use-schedule-explanation";
import type { ExplanationCode } from "@/lib/explanation-api";

const titles: Record<ExplanationCode, string> = {
  arrangement: "安排概览", visits: "访问与停留", movement: "交通参考", reserved_time: "午餐与等待",
  return_to_stay: "返回住宿", not_arranged: "未安排的必去", optional_attempts: "可选地点的尝试", empty_day: "尚未安排的日期",
};
export default function ScheduleExplanation({ explanation }: { explanation: ReturnType<typeof useScheduleExplanation> }) {
  const { state } = explanation;
  return <section aria-label="AI 行程解读" className="mt-7 rounded-2xl border border-[#cbdcd2] bg-white p-5 sm:p-7">
    <h2 className="text-xl font-semibold text-[#18392f]">AI 行程解读</h2>
    <p className="mt-2 text-sm leading-6 text-[#56605c]">只解读当前正式草案，不改变行程。千问选择解释重点，名称、时间、原因与必要限制由程序核对后展示；不解读天气或预算。</p>
    {!explanation.available && <p className="mt-3 text-sm">请先生成正式行程草案，再主动请求解读。</p>}
    <div className="mt-4 flex flex-wrap gap-3">
      <button type="button" disabled={!explanation.available || explanation.loading} onClick={() => void explanation.explain()} className="min-h-11 rounded-xl bg-[#18392f] px-5 py-2 text-sm font-semibold text-white disabled:opacity-40">{explanation.loading ? "正在解读…" : state.status === "failed" ? "重试行程解读" : "解释当前行程"}</button>
      {explanation.loading && <button type="button" onClick={explanation.cancel} className="min-h-11 rounded-xl border px-4 py-2 text-sm">取消解读</button>}
    </div>
    <p aria-live="polite" className="mt-3 text-sm text-[#56605c]">{state.message ?? (explanation.loading ? "正在等待千问；取消等待不保证上游停止计费。" : "")}</p>
    {state.response && <div className="mt-4 space-y-4">
      <p className="break-words text-xs leading-6 text-[#68726c]">千问 · {state.response.model} · 对应草案 {state.response.schedule_generated_at} · 基于当前提交快照</p>
      <ol className="space-y-3">{state.response.highlights.map((highlight, index) => <li key={index} className="rounded-xl bg-[#f5f8f4] p-4">
        <h3 className="font-semibold">{titles[highlight.message_code]}</h3>
        <p className="mt-2 whitespace-pre-line break-words text-sm leading-7">{highlight.text}</p>
      </li>)}</ol>
      <aside className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm leading-7"><h3 className="font-semibold">仍需留意</h3><ul>{state.response.limitations.map((notice) => <li key={notice}>{notice}</li>)}</ul></aside>
    </div>}
  </section>;
}
