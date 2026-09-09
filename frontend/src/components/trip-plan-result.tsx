import type { TripPlan } from "@/types/trip";

export default function TripPlanResult({ plan }: { plan: TripPlan }) {
  return (
    <section
      aria-labelledby="trip-result-title"
      className="mt-8 rounded-3xl border border-[#18201d]/10 bg-white p-5 sm:p-8"
    >
      <p role="status" className="text-sm font-medium text-[#315f51]">
        已收到行程 · Mock 示例
      </p>
      <h2 id="trip-result-title" className="mt-2 text-2xl font-semibold">
        {plan.destination} · {plan.days.length} 日行程
      </h2>
      <p className="mt-3 text-sm">
        预估总费用（Mock）：
        <span className="font-semibold">
          {new Intl.NumberFormat("zh-CN", {
            style: "currency",
            currency: plan.currency,
          }).format(plan.estimated_cost)}
        </span>
      </p>
      <p className="mt-3 rounded-xl bg-amber-50 p-3 text-sm leading-6 text-amber-900">
        {plan.notice}
      </p>
      <div className="mt-6 space-y-5">
        {plan.days.map((day) => (
          <article key={day.day} className="rounded-2xl border border-[#18201d]/10 p-4 sm:p-5">
            <h3 className="font-semibold text-[#18392f]">
              第 {day.day} 天 · {day.title}
            </h3>
            <ol className="mt-4 space-y-4">
              {day.activities.map((activity, index) => (
                <li key={`${day.day}-${index}`} className="border-l-2 border-[#d8e7df] pl-4">
                  <p className="text-sm text-[#56605c]">
                    <time dateTime={activity.start_time}>{activity.start_time.slice(0, 5)}</time>
                    {" – "}
                    <time dateTime={activity.end_time}>{activity.end_time.slice(0, 5)}</time>
                  </p>
                  <h4 className="mt-1 font-medium">{activity.name}</h4>
                  <p className="mt-1 text-sm leading-6 text-[#56605c]">{activity.description}</p>
                </li>
              ))}
            </ol>
          </article>
        ))}
      </div>
    </section>
  );
}
