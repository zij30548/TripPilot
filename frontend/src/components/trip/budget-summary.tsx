import type { TripPlan } from "@/types/trip";

export default function BudgetSummary({ plan }: { plan: TripPlan }) {
  const remaining = plan.request.budget - plan.estimated_cost;
  const labels = { transport: "交通", food: "餐饮", tickets: "门票", other: "其他" };
  return (
    <section aria-labelledby="budget-title" className="rounded-2xl border border-[#18201d]/10 bg-white p-5 sm:p-6">
      <h2 id="budget-title" className="text-lg font-semibold">预算一览</h2>
      <p className="mt-1 text-xs leading-5 text-[#68726c]">全部旅客 · Mock 示例费用</p>
      <dl className="mt-5 space-y-3 text-sm">
        <div className="flex flex-wrap justify-between gap-2"><dt>总预算</dt><dd className="break-all font-semibold">¥{plan.request.budget.toFixed(2)}</dd></div>
        <div className="flex flex-wrap justify-between gap-2"><dt>预计总花费</dt><dd className="break-all font-semibold">¥{plan.estimated_cost.toFixed(2)}</dd></div>
        {(Object.keys(labels) as Array<keyof typeof labels>).map((key) => <div key={key} className="flex justify-between gap-3 text-[#56605c]"><dt>{labels[key]}</dt><dd>¥{plan.budget_breakdown[key].toFixed(2)}</dd></div>)}
        <div className={`flex flex-wrap justify-between gap-2 border-t border-[#18201d]/10 pt-4 font-semibold ${remaining < 0 ? "text-[#b3422d]" : "text-[#315f51]"}`}><dt>{remaining < 0 ? "超出预算" : "剩余预算"}</dt><dd className="break-all">¥{Math.abs(remaining).toFixed(2)}</dd></div>
      </dl>
      <p className="mt-4 text-xs leading-5 text-[#68726c]">按演示明细汇总，不代表真实报价或实际旅行支出。</p>
    </section>
  );
}
