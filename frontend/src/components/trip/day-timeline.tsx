import type { Ref } from "react";
import type { DayPlan } from "@/types/trip";
import { activityPlaceKey, type ActivityPlaceBindings } from "@/lib/activity-places";
import { walkingSegmentKey, type WalkingRouteState } from "@/lib/use-walking-route";
import ActivityCard from "./activity-card";
import TransportSegment from "./transport-segment";
import WalkingRouteSegment from "./walking-route-segment";

export default function DayTimeline({ day, bindings = {}, selectedActivityKey, bindingTargetKey,
  activityRef, onChoosePlace, onShowPlace, onRemovePlace, walkingRouteState, onQueryWalkingRoute, onShowWalkingRoute }: {
  day: DayPlan;
  bindings?: ActivityPlaceBindings;
  selectedActivityKey?: string | null;
  bindingTargetKey?: string | null;
  activityRef?: (key: string) => Ref<HTMLElement>;
  onChoosePlace?: (key: string) => void;
  onShowPlace?: (key: string) => void;
  onRemovePlace?: (key: string) => void;
  walkingRouteState?: WalkingRouteState;
  onQueryWalkingRoute?: (fromKey: string, toKey: string) => void;
  onShowWalkingRoute?: () => void;
}) {
  return (
    <section aria-labelledby="timeline-title" className="min-w-0">
      <div className="mb-5"><p className="text-xs font-semibold tracking-widest text-[#bd4c35]">DAILY TIMELINE · MOCK</p><h2 id="timeline-title" className="mt-2 text-2xl font-semibold">Day {day.day} · {day.title}</h2><p className="mt-2 text-sm text-[#56605c]"><time dateTime={day.date}>{day.date}</time> · {day.activities.length} 个活动</p></div>
      <ol>
        {day.activities.map((activity, index) => {
          const next = day.activities[index + 1];
          const segment = next && day.transports.find((item) => item.from_activity_id === activity.id && item.to_activity_id === next.id);
          const key = activityPlaceKey(day, activity);
          // Use the original activity order, including unbound activities.
          const nextKey = next && activityPlaceKey(day, next);
          const routeState = nextKey && walkingRouteState?.status !== "idle" && walkingRouteState?.selection.key === walkingSegmentKey(key, nextKey)
            ? walkingRouteState : undefined;
          return <li key={key}><ActivityCard activity={activity} order={index + 1} place={bindings[key]}
            selected={selectedActivityKey === key} choosingPlace={bindingTargetKey === key} cardRef={activityRef?.(key)}
            onChoosePlace={onChoosePlace && (() => onChoosePlace(key))}
            onShowPlace={onShowPlace && (() => onShowPlace(key))}
            onRemovePlace={onRemovePlace && (() => onRemovePlace(key))} />{segment && <TransportSegment segment={segment} />}
            {next && nextKey && <WalkingRouteSegment fromActivity={activity} toActivity={next}
              origin={bindings[key]} destination={bindings[nextKey]} state={routeState}
              onQuery={onQueryWalkingRoute && (() => onQueryWalkingRoute(key, nextKey))}
              onShowRoute={onShowWalkingRoute} />}</li>;
        })}
      </ol>
      <p className="mt-5 text-center text-xs text-[#68726c]">当天示例行程结束 · 为自己留一点自由时间</p>
    </section>
  );
}
