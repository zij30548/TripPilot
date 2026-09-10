import type { DayPlan } from "@/types/trip";
import ActivityCard from "./activity-card";
import TransportSegment from "./transport-segment";

export default function DayTimeline({ day }: { day: DayPlan }) {
  return (
    <section aria-labelledby="timeline-title" className="min-w-0">
      <div className="mb-5"><p className="text-xs font-semibold tracking-widest text-[#bd4c35]">DAILY TIMELINE · MOCK</p><h2 id="timeline-title" className="mt-2 text-2xl font-semibold">Day {day.day} · {day.title}</h2><p className="mt-2 text-sm text-[#56605c]"><time dateTime={day.date}>{day.date}</time> · {day.activities.length} 个活动</p></div>
      <ol>
        {day.activities.map((activity, index) => {
          const next = day.activities[index + 1];
          const segment = next && day.transports.find((item) => item.from_activity_id === activity.id && item.to_activity_id === next.id);
          return <li key={activity.id}><ActivityCard activity={activity} order={index + 1} />{segment && <TransportSegment segment={segment} />}</li>;
        })}
      </ol>
      <p className="mt-5 text-center text-xs text-[#68726c]">当天示例行程结束 · 为自己留一点自由时间</p>
    </section>
  );
}
