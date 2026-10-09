"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { TripPlan } from "@/types/trip";
import type { Place } from "@/types/place";
import { activityPlaceKey, type ActivityPlaceBindings } from "@/lib/activity-places";
import { useWalkingRoute, walkingSegmentKey } from "@/lib/use-walking-route";
import { useTransitRoute } from "@/lib/use-transit-route";
import { useCandidatePool } from "@/lib/use-candidate-pool";
import { useSchedulePreview } from "@/lib/use-schedule-preview";
import { useWeatherForecast } from "@/lib/use-weather-forecast";
import TripOverview from "./trip/trip-overview";
import ConfirmedPlaces from "./trip/confirmed-places";
import CandidatePreparation from "./trip/candidate-preparation";
import SchedulePreview from "./trip/schedule-preview";
import DayTimeline from "./trip/day-timeline";
import BudgetSummary from "./trip/budget-summary";
import WeatherSummary from "./trip/weather-summary";
import WeatherForecast from "./trip/weather-forecast";
import PlaceExplorer from "./places/place-explorer";

export default function TripPlanResult({ plan, onEdit }: { plan: TripPlan; onEdit: () => void }) {
  const [selectedDay, setSelectedDay] = useState(0);
  const [bindings, setBindings] = useState<ActivityPlaceBindings>({});
  const [selectedActivityKey, setSelectedActivityKey] = useState<string | null>(null);
  const [bindingTargetKey, setBindingTargetKey] = useState<string | null>(null);
  const [bindingSessionVersion, setBindingSessionVersion] = useState(0);
  const [mapView, setMapView] = useState<"search" | "itinerary">("search");
  const [selectionVersion, setSelectionVersion] = useState(0);
  const [bindingMessage, setBindingMessage] = useState("");
  const [routeSelectionVersion, setRouteSelectionVersion] = useState(0);
  const [routeMode, setRouteMode] = useState<"walking" | "transit">("walking");
  const walking = useWalkingRoute();
  const transit = useTransitRoute();
  const schedule = useSchedulePreview(plan.request);
  const candidates = useCandidatePool(plan.request, schedule.updateCandidates);
  const weatherRequest = { start_date: plan.request.start_date, end_date: plan.request.end_date };
  const weather = useWeatherForecast(weatherRequest);
  const result = useRef<HTMLDivElement>(null);
  const explorer = useRef<HTMLDivElement>(null);
  const activityCards = useRef(new Map<string, HTMLElement>());
  const day = plan.days[selectedDay];
  const target = day.activities.find((activity) => activityPlaceKey(day, activity) === bindingTargetKey);
  const selectedPlaceId = selectedActivityKey ? bindings[selectedActivityKey]?.id ?? null : null;
  const boundActivities = useMemo(() => day.activities.flatMap((activity) => {
    const key = activityPlaceKey(day, activity);
    const place = bindings[key];
    return place ? [{ key, activity, place }] : [];
  }), [day, bindings]);
  const itineraryPlaces = useMemo(() => Array.from(new Map(boundActivities.map(({ place }) => [place.id, place])).values()), [boundActivities]);

  useEffect(() => {
    result.current?.focus();
    result.current?.scrollIntoView({ block: "start" });
  }, []);

  function choosePlace(key: string) {
    setBindingTargetKey(key);
    setBindingSessionVersion((version) => version + 1);
    setSelectedActivityKey(key);
    setBindingMessage("");
    setMapView("search");
    explorer.current?.scrollIntoView({ block: "start", behavior: "smooth" });
  }

  function bindPlace(place: Place) {
    if (!target || !bindingTargetKey) return;
    // Invalidate before state updates; a late response cannot revive replaced endpoints.
    walking.invalidateActivity(bindingTargetKey);
    transit.invalidateActivity(bindingTargetKey);
    // Binding is a local user decision; the backend Mock TripPlan stays immutable.
    setBindings((current) => ({ ...current, [bindingTargetKey]: { ...place } }));
    setSelectedActivityKey(bindingTargetKey);
    setBindingTargetKey(null);
    setSelectionVersion((version) => version + 1);
    setMapView("itinerary");
    setBindingMessage(`已将“${place.name}”绑定到 Day ${day.day} · ${target.name}。`);
  }

  function showPlace(key: string) {
    if (!bindings[key]) return;
    setSelectedActivityKey(key);
    setSelectionVersion((version) => version + 1);
    setMapView("itinerary");
    explorer.current?.scrollIntoView({ block: "start", behavior: "smooth" });
  }

  function removePlace(key: string) {
    walking.invalidateActivity(key);
    transit.invalidateActivity(key);
    setBindings((current) => {
      const next = { ...current };
      delete next[key];
      return next;
    });
    if (selectedActivityKey === key) setSelectedActivityKey(null);
    if (bindingTargetKey === key) setBindingTargetKey(null);
    setBindingMessage("已解除此活动的地点绑定。");
  }

  function selectItineraryPlace(id: string) {
    // Several activities may intentionally visit the same POI; share one marker.
    const matches = boundActivities.filter(({ place }) => place.id === id);
    const selected = matches.find(({ key }) => key === selectedActivityKey) ?? matches[0];
    if (!selected) return;
    setSelectedActivityKey(selected.key);
    setSelectionVersion((version) => version + 1);
    const card = activityCards.current.get(selected.key);
    card?.focus({ preventScroll: true });
    card?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  function switchDay(index: number) {
    if (index === selectedDay) return;
    walking.invalidate();
    transit.invalidate();
    setSelectedDay(index);
    setSelectedActivityKey(null);
    setBindingTargetKey(null);
    setBindingMessage("");
  }

  function switchRouteMode(mode: "walking" | "transit") {
    if (mode === routeMode) return;
    // Invalidate both generations synchronously, before changing the visible mode.
    // Walking → transit → walking cannot re-enable an earlier response or error.
    walking.invalidate();
    transit.invalidate();
    setRouteMode(mode);
  }

  function queryRoute(fromKey: string, toKey: string) {
    const index = day.activities.findIndex((activity) => activityPlaceKey(day, activity) === fromKey);
    const next = day.activities[index + 1];
    if (index < 0 || !next || activityPlaceKey(day, next) !== toKey || !bindings[fromKey] || !bindings[toKey]) return;
    setMapView("itinerary");
    setRouteSelectionVersion((version) => version + 1);
    // Only this click chooses the map view. A late response never steals a newer search view.
    const query = routeMode === "walking" ? walking.query : transit.query;
    void query({ key: walkingSegmentKey(fromKey, toKey), fromActivityKey: fromKey, toActivityKey: toKey,
      origin: { ...bindings[fromKey] }, destination: { ...bindings[toKey] } });
  }

  function showRoute() {
    if ((routeMode === "walking" ? walking.state : transit.state).status !== "success") return;
    setMapView("itinerary");
    setRouteSelectionVersion((version) => version + 1);
    explorer.current?.scrollIntoView({ block: "start", behavior: "smooth" });
  }

  function editRequest() {
    weather.invalidate();
    candidates.invalidate();
    schedule.invalidate();
    walking.invalidate();
    transit.invalidate();
    onEdit();
  }

  return (
    <div ref={result} tabIndex={-1} aria-label="旅行结果" className="scroll-mt-6 outline-none">
      <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <p role="status" className="text-sm font-medium text-[#315f51]">旅行需求已确认</p>
        <div className="flex flex-wrap gap-3">
          <button type="button" onClick={editRequest} className="min-h-11 rounded-full border border-[#315f51] bg-white px-5 py-2 text-sm font-semibold text-[#315f51] focus-visible:outline-2 focus-visible:outline-offset-2">修改旅行需求</button>
          <button type="button" disabled className="min-h-11 cursor-not-allowed rounded-full border border-[#18201d]/10 px-5 py-2 text-sm text-[#68726c]">重新规划 · 暂未开放</button>
        </div>
      </div>
      <ConfirmedPlaces request={plan.request} />
      <WeatherForecast request={weatherRequest} weather={weather} />
      <CandidatePreparation request={plan.request} pool={candidates} />
      <SchedulePreview request={plan.request} preview={schedule} />
      <section aria-label="旧 Mock 行程示例" className="mt-9 border-t-2 border-dashed border-[#18201d]/20 pt-7">
      <h2 className="mb-2 text-xl font-semibold text-[#18392f]">旧 Mock 行程示例</h2>
      <p className="mb-5 text-sm leading-6 text-[#68726c]">以下固定示例与上方行程草案相互独立；预算、天气、时间和原交通仍是 Mock。已有活动绑定和单段路线查询只影响这个示例区，不会修改草案；这里的交通方式也与草案单独设置。</p>
      <TripOverview plan={plan} />
      <p aria-live="polite" aria-atomic="true" className="mt-3 text-sm leading-6 text-[#315f51]">{bindingMessage}</p>
      <div aria-label="选择行程日期" className="my-7 flex flex-wrap gap-3">
        {plan.days.map((item, index) => <button key={item.day} type="button" aria-pressed={selectedDay === index} onClick={() => switchDay(index)} className={`min-h-12 rounded-xl border px-5 py-3 text-sm font-semibold transition focus-visible:outline-2 focus-visible:outline-offset-2 ${selectedDay === index ? "border-[#18392f] bg-[#18392f] text-white" : "border-[#18201d]/15 bg-white text-[#56605c] hover:border-[#315f51]"}`}>Day {item.day} <span className="ml-2 text-xs font-normal">{item.date.slice(5)}</span></button>)}
      </div>
      <div className="grid items-start gap-7 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <div aria-live="polite"><DayTimeline day={day} bindings={bindings} selectedActivityKey={selectedActivityKey}
          bindingTargetKey={bindingTargetKey} onChoosePlace={choosePlace} onShowPlace={showPlace} onRemovePlace={removePlace}
          walkingRouteState={walking.state} onQueryWalkingRoute={queryRoute} onShowWalkingRoute={showRoute}
          routeMode={routeMode} onRouteModeChange={switchRouteMode} transitRouteState={transit.state}
          onQueryTransitRoute={queryRoute} onShowTransitRoute={showRoute}
          activityRef={(key) => (element) => {
            if (element) activityCards.current.set(key, element);
            else activityCards.current.delete(key);
          }} /></div>
        <aside className="min-w-0 space-y-5" aria-label="旅行摘要">
          <BudgetSummary plan={plan} />
          <WeatherSummary weather={day.weather} />
          <div ref={explorer} className="scroll-mt-6">
            <PlaceExplorer key={day.date} view={mapView} onViewChange={setMapView}
              walkingRoute={routeMode === "walking" && walking.state.status === "success" ? walking.state.response : null}
              transitRoute={routeMode === "transit" && transit.state.status === "success" ? transit.state.response : null}
              routeSelectionVersion={routeSelectionVersion}
              bindingTarget={target && bindingTargetKey ? {
                key: JSON.stringify([bindingTargetKey, bindingSessionVersion]),
                label: `Day ${day.day} · ${target.name}`, keyword: target.name,
                onConfirm: bindPlace, onCancel: () => setBindingTargetKey(null),
              } : undefined}
              itinerary={{ places: itineraryPlaces, activityCount: boundActivities.length, selectedPlaceId,
                selectionVersion, onSelect: selectItineraryPlace }} />
          </div>
        </aside>
      </div>
      </section>
    </div>
  );
}
