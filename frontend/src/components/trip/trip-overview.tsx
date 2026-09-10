import type { TripPlan } from "@/types/trip";

const paceLabels = { relaxed: "轻松", balanced: "均衡", packed: "紧凑" };

export default function TripOverview({ plan }: { plan: TripPlan }) {
  const request = plan.request;
  const requestedDays = Math.round(
    (Date.parse(request.end_date) - Date.parse(request.start_date)) / 86_400_000,
  ) + 1;

  return (
    <section aria-labelledby="overview-title" className="overflow-hidden rounded-3xl bg-[#18392f] text-white">
      <div className="p-6 sm:p-9">
        <div className="flex flex-wrap items-center gap-3 text-xs font-semibold tracking-widest text-[#c6d8ce]">
          <span>TRIP OVERVIEW</span>
          <span className="rounded-full border border-white/25 px-3 py-1 tracking-normal">MOCK · 演示行程</span>
        </div>
        <h1 id="overview-title" className="mt-5 text-3xl font-semibold tracking-tight sm:text-5xl">
          {plan.destination}，慢慢发现。
        </h1>
        <p className="mt-3 text-sm leading-6 text-[#d1ded6]">你的需求已收到。以下为固定 {plan.days.length} 日旅行示例。</p>
        <dl className="mt-7 grid grid-cols-2 gap-x-5 gap-y-6 sm:grid-cols-4">
          <div className="col-span-2"><dt className="text-xs text-[#c6d8ce]">旅行日期 · 你的需求</dt><dd className="mt-2 break-words font-medium">{request.start_date} — {request.end_date}</dd></div>
          <div><dt className="text-xs text-[#c6d8ce]">同行人数</dt><dd className="mt-2 font-medium">{request.travelers} 人</dd></div>
          <div><dt className="text-xs text-[#c6d8ce]">旅行节奏</dt><dd className="mt-2 font-medium">{paceLabels[request.pace]}</dd></div>
          <div><dt className="text-xs text-[#c6d8ce]">总预算</dt><dd className="mt-2 break-all text-xl font-semibold">¥{request.budget.toLocaleString("zh-CN")}</dd></div>
          <div><dt className="text-xs text-[#c6d8ce]">预计总花费 · Mock</dt><dd className="mt-2 break-all text-xl font-semibold">¥{plan.estimated_cost.toLocaleString("zh-CN")}</dd></div>
          <div className="col-span-2"><dt className="text-xs text-[#c6d8ce]">兴趣偏好</dt><dd className="mt-2 flex flex-wrap gap-2">{request.interests.length ? request.interests.map((interest, index) => <span key={`${interest}-${index}`} className="max-w-full break-words rounded-full bg-white/10 px-3 py-1 text-sm">{interest}</span>) : <span className="text-sm">暂未选择</span>}</dd></div>
        </dl>
      </div>
      <div className="border-t border-white/15 bg-black/10 px-6 py-4 text-xs leading-6 text-[#d1ded6] sm:px-9">
        <p>{plan.notice}</p>
        {requestedDays !== plan.days.length && <p className="mt-1 font-semibold text-[#ffdbb0]">你选择了 {requestedDays} 天；当前展示 {plan.days.length} 天，示例日期不完全匹配你的旅行日期。</p>}
      </div>
    </section>
  );
}
