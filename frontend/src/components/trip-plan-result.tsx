"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { TripPlan } from "@/types/trip";
import type { Place } from "@/types/place";
import { activityPlaceKey, type ActivityPlaceBindings } from "@/lib/activity-places";
import TripOverview from "./trip/trip-overview";
import DayTimeline from "./trip/day-timeline";
import BudgetSummary from "./trip/budget-summary";
import WeatherSummary from "./trip/weather-summary";
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
    setSelectedDay(index);
    setSelectedActivityKey(null);
    setBindingTargetKey(null);
    setBindingMessage("");
  }

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
      <p aria-live="polite" aria-atomic="true" className="mt-3 text-sm leading-6 text-[#315f51]">{bindingMessage}</p>
      <div aria-label="选择行程日期" className="my-7 flex flex-wrap gap-3">
        {plan.days.map((item, index) => <button key={item.day} type="button" aria-pressed={selectedDay === index} onClick={() => switchDay(index)} className={`min-h-12 rounded-xl border px-5 py-3 text-sm font-semibold transition focus-visible:outline-2 focus-visible:outline-offset-2 ${selectedDay === index ? "border-[#18392f] bg-[#18392f] text-white" : "border-[#18201d]/15 bg-white text-[#56605c] hover:border-[#315f51]"}`}>Day {item.day} <span className="ml-2 text-xs font-normal">{item.date.slice(5)}</span></button>)}
      </div>
      <div className="grid items-start gap-7 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <div aria-live="polite"><DayTimeline day={day} bindings={bindings} selectedActivityKey={selectedActivityKey}
          bindingTargetKey={bindingTargetKey} onChoosePlace={choosePlace} onShowPlace={showPlace} onRemovePlace={removePlace}
          activityRef={(key) => (element) => {
            if (element) activityCards.current.set(key, element);
            else activityCards.current.delete(key);
          }} /></div>
        <aside className="min-w-0 space-y-5" aria-label="旅行摘要">
          <BudgetSummary plan={plan} />
          <WeatherSummary weather={day.weather} />
          <div ref={explorer} className="scroll-mt-6">
            <PlaceExplorer key={day.date} view={mapView} onViewChange={setMapView}
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
    </div>
  );
}
