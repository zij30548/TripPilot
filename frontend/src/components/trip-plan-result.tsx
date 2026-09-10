"use client";

import { useEffect, useRef, useState } from "react";
import type { TripPlan } from "@/types/trip";
import TripOverview from "./trip/trip-overview";
import DayTimeline from "./trip/day-timeline";
import BudgetSummary from "./trip/budget-summary";
import WeatherSummary from "./trip/weather-summary";
import MapPlaceholder from "./trip/map-placeholder";

export default function TripPlanResult({ plan, onEdit }: { plan: TripPlan; onEdit: () => void }) {
  const [selectedDay, setSelectedDay] = useState(0);
  const result = useRef<HTMLDivElement>(null);
  const day = plan.days[selectedDay];

  useEffect(() => {
    result.current?.focus();
    result.current?.scrollIntoView({ block: "start" });
  }, []);

  return (
    <div ref={result} tabIndex={-1} aria-label="旅行结果" className="scroll-mt-6 outline-none">
      <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <p role="status" className="text-sm font-medium text-[#315f51]">行程已就绪 · Mock 示例</p>
        <div className="flex flex-wrap gap-3">
          <button type="button" onClick={onEdit} className="min-h-11 rounded-full border border-[#315f51] bg-white px-5 py-2 text-sm font-semibold text-[#315f51] focus-visible:outline-2 focus-visible:outline-offset-2">修改旅行需求</button>
          <button type="button" disabled className="min-h-11 cursor-not-allowed rounded-full border border-[#18201d]/10 px-5 py-2 text-sm text-[#68726c]">重新规划 · 暂未开放</button>
        </div>
      </div>
      <TripOverview plan={plan} />
      <div aria-label="选择行程日期" className="my-7 flex flex-wrap gap-3">
        {plan.days.map((item, index) => <button key={item.day} type="button" aria-pressed={selectedDay === index} onClick={() => setSelectedDay(index)} className={`min-h-12 rounded-xl border px-5 py-3 text-sm font-semibold transition focus-visible:outline-2 focus-visible:outline-offset-2 ${selectedDay === index ? "border-[#18392f] bg-[#18392f] text-white" : "border-[#18201d]/15 bg-white text-[#56605c] hover:border-[#315f51]"}`}>Day {item.day} <span className="ml-2 text-xs font-normal">{item.date.slice(5)}</span></button>)}
      </div>
      <div className="grid items-start gap-7 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <div aria-live="polite"><DayTimeline day={day} /></div>
        <aside className="min-w-0 space-y-5" aria-label="旅行摘要">
          <BudgetSummary plan={plan} />
          <WeatherSummary weather={day.weather} />
          <MapPlaceholder day={day} />
        </aside>
      </div>
    </div>
  );
}
