import type { DayPlan } from "@/types/trip";

export default function MapPlaceholder({ day }: { day: DayPlan }) {
  return (
    <section aria-labelledby="map-title" className="overflow-hidden rounded-2xl border border-[#18201d]/10 bg-white">
      <div className="border-b border-[#18201d]/10 p-5 sm:p-6"><h2 id="map-title" className="text-lg font-semibold">当天地点顺序</h2><p className="mt-1 text-xs text-[#68726c]">Day {day.day} · 地图占位</p></div>
      <div className="bg-[#f3f2ec] p-5 sm:p-6">
        <p className="mb-4 text-xs leading-5 text-[#68726c]">真实地图将在后续接入。下方仅表示活动顺序，不表示地理位置或距离。</p>
        <ol className="space-y-4">
          {day.activities.map((activity, index) => <li key={activity.id} className="flex items-center gap-3"><span className="grid size-7 shrink-0 place-items-center rounded-full border border-[#315f51]/30 bg-white text-xs font-semibold text-[#315f51]">{index + 1}</span><span className="min-w-0 break-words text-sm font-medium">{activity.name}</span></li>)}
        </ol>
      </div>
    </section>
  );
}
