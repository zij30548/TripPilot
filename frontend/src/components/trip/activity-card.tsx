import type { Activity } from "@/types/trip";

const categories = { sightseeing: "城市漫步", food: "餐饮", museum: "文化参观", shopping: "购物" };

export default function ActivityCard({ activity, order }: { activity: Activity; order: number }) {
  return (
    <article className="rounded-2xl border border-[#dde3dd] bg-white p-4 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="font-mono text-sm font-semibold text-[#315f51]"><time dateTime={activity.start_time}>{activity.start_time.slice(0, 5)}</time> — <time dateTime={activity.end_time}>{activity.end_time.slice(0, 5)}</time></p>
        <span className="rounded-full bg-[#edf3ef] px-3 py-1 text-xs text-[#315f51]">{categories[activity.category]}</span>
      </div>
      <div className="mt-4 flex items-start gap-3">
        <span className="grid size-7 shrink-0 place-items-center rounded-full bg-[#18392f] text-xs font-semibold text-white" aria-hidden="true">{order}</span>
        <div className="min-w-0"><h3 className="break-words text-lg font-semibold">{activity.name}</h3><p className="mt-2 text-sm leading-6 text-[#56605c]">{activity.description}</p></div>
      </div>
      <p className="mt-4 border-t border-[#18201d]/8 pt-3 text-xs text-[#56605c]">预计费用 · Mock <span className="float-right text-sm font-semibold text-[#18392f]">¥{activity.estimated_cost.toFixed(2)}</span></p>
    </article>
  );
}
