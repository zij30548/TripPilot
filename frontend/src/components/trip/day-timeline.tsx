import type { Ref } from "react";
import type { DayPlan } from "@/types/trip";
import { activityPlaceKey, type ActivityPlaceBindings } from "@/lib/activity-places";
import { walkingSegmentKey, type WalkingRouteState } from "@/lib/use-walking-route";
import type { TransitRouteState } from "@/lib/use-transit-route";
import ActivityCard from "./activity-card";
import TransportSegment from "./transport-segment";
import WalkingRouteSegment from "./walking-route-segment";
import TransitRouteSegment from "./transit-route-segment";

export default function DayTimeline({ day, bindings = {}, selectedActivityKey, bindingTargetKey,
  activityRef, onChoosePlace, onShowPlace, onRemovePlace, walkingRouteState, onQueryWalkingRoute, onShowWalkingRoute,
  routeMode = "walking", onRouteModeChange, transitRouteState, onQueryTransitRoute, onShowTransitRoute }: {
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
  routeMode?: "walking" | "transit";
  onRouteModeChange?: (mode: "walking" | "transit") => void;
  transitRouteState?: TransitRouteState;
  onQueryTransitRoute?: (fromKey: string, toKey: string) => void;
  onShowTransitRoute?: () => void;
}) {
  return (
    <section aria-labelledby="timeline-title" className="min-w-0">
      <div className="mb-5"><p className="text-xs font-semibold tracking-widest text-[#bd4c35]">DAILY TIMELINE · MOCK</p><h2 id="timeline-title" className="mt-2 text-2xl font-semibold">Day {day.day} · {day.title}</h2><p className="mt-2 text-sm text-[#56605c]"><time dateTime={day.date}>{day.date}</time> · {day.activities.length} 个活动</p></div>
      {onRouteModeChange && <div className="mb-5 rounded-xl border border-[#315f51]/20 bg-white p-4">
        <div role="group" aria-label="选择真实交通方式" className="flex flex-wrap gap-2">
          {([['walking', '步行'], ['transit', '公交／地铁']] as const).map(([mode, label]) => <button key={mode}
            type="button" aria-pressed={routeMode === mode} onClick={() => onRouteModeChange(mode)}
            className={`min-h-11 rounded-lg border px-4 py-2 text-sm font-semibold focus-visible:outline-2 focus-visible:outline-offset-2 ${routeMode === mode ? "border-[#315f51] bg-[#315f51] text-white" : "border-[#315f51]/30 text-[#315f51]"}`}>{label}</button>)}
        </div>
        <p className="mt-2 text-xs leading-5 text-[#68726c]">切换方式会清除上次结果，不自动查询。请在相邻活动间点击查询，一次查看一条真实路线参考。</p>
      </div>}
      <ol>
        {day.activities.map((activity, index) => {
          const next = day.activities[index + 1];
          const segment = next && day.transports.find((item) => item.from_activity_id === activity.id && item.to_activity_id === next.id);
          const key = activityPlaceKey(day, activity);
          // Use the original activity order, including unbound activities.
          const nextKey = next && activityPlaceKey(day, next);
          const routeState = nextKey && walkingRouteState?.status !== "idle" && walkingRouteState?.selection.key === walkingSegmentKey(key, nextKey)
            ? walkingRouteState : undefined;
          const transitState = nextKey && transitRouteState?.status !== "idle" && transitRouteState?.selection.key === walkingSegmentKey(key, nextKey)
            ? transitRouteState : undefined;
          return <li key={key}><ActivityCard activity={activity} order={index + 1} place={bindings[key]}
            selected={selectedActivityKey === key} choosingPlace={bindingTargetKey === key} cardRef={activityRef?.(key)}
            onChoosePlace={onChoosePlace && (() => onChoosePlace(key))}
            onShowPlace={onShowPlace && (() => onShowPlace(key))}
            onRemovePlace={onRemovePlace && (() => onRemovePlace(key))} />{segment && <TransportSegment segment={segment} />}
            {next && nextKey && (routeMode === "walking" ? <WalkingRouteSegment fromActivity={activity} toActivity={next}
              origin={bindings[key]} destination={bindings[nextKey]} state={routeState}
              onQuery={onQueryWalkingRoute && (() => onQueryWalkingRoute(key, nextKey))}
              onShowRoute={onShowWalkingRoute} /> : <TransitRouteSegment fromActivity={activity} toActivity={next}
              origin={bindings[key]} destination={bindings[nextKey]} state={transitState}
              onQuery={onQueryTransitRoute && (() => onQueryTransitRoute(key, nextKey))}
              onShowRoute={onShowTransitRoute} />)}</li>;
        })}
      </ol>
      <p className="mt-5 text-center text-xs text-[#68726c]">当天示例行程结束 · 为自己留一点自由时间</p>
    </section>
  );
}
