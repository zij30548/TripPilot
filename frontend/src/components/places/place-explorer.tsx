"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { searchPlaces } from "@/lib/places-api";
import type { Place } from "@/types/place";
import PlaceMap from "./place-map";
import PlaceSearch from "./place-search";

type SearchState =
  | { status: "idle" | "loading" | "success" }
  | { status: "error"; message: string };

type SearchSession = {
  context: string;
  places: Place[];
  selectedPlaceId: string | null;
  selectionVersion: number;
  state: SearchState;
};

export type PlaceBindingTarget = {
  key: string;
  label: string;
  keyword: string;
  onConfirm: (place: Place) => void;
  onCancel: () => void;
};

export type ItineraryPlaces = {
  places: Place[];
  activityCount: number;
  selectedPlaceId: string | null;
  selectionVersion: number;
  onSelect: (id: string) => void;
};

const emptyPlaces: Place[] = [];
function emptySession(context: string): SearchSession {
  return { context, places: emptyPlaces, selectedPlaceId: null, selectionVersion: 0, state: { status: "idle" } };
}

export default function PlaceExplorer({ bindingTarget, itinerary, view = "search", onViewChange }: {
  bindingTarget?: PlaceBindingTarget;
  itinerary?: ItineraryPlaces;
  view?: "search" | "itinerary";
  onViewChange?: (view: "search" | "itinerary") => void;
}) {
  const context = bindingTarget?.key ?? "browse";
  const [storedSession, setSession] = useState<SearchSession>(() => emptySession(context));
  // A response or selection from another activity is never eligible for binding.
  const session = storedSession.context === context ? storedSession : emptySession(context);
  const { places, selectedPlaceId, selectionVersion, state: searchState } = session;
  const requestId = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const cards = useRef(new Map<string, HTMLButtonElement>());
  const showingItinerary = view === "itinerary" && itinerary !== undefined;
  const selectedPlace = places.find((place) => place.id === selectedPlaceId);

  useEffect(() => () => {
    requestId.current += 1;
    controller.current?.abort();
  }, [context]);

  async function search(keyword: string) {
    const currentId = ++requestId.current;
    controller.current?.abort();
    const currentController = new AbortController();
    controller.current = currentController;
    setSession({ ...emptySession(context), state: { status: "loading" } });
    try {
      const results = await searchPlaces(keyword, currentController.signal);
      if (requestId.current !== currentId || currentController.signal.aborted) return;
      setSession({ ...emptySession(context), places: results, state: { status: "success" } });
    } catch (error) {
      if (requestId.current !== currentId || currentController.signal.aborted) return;
      setSession({ ...emptySession(context), state: { status: "error", message: error instanceof Error ? error.message : "地点搜索失败，请重试。" } });
    } finally {
      if (requestId.current === currentId) controller.current = null;
    }
  }

  const selectPlace = useCallback((id: string) => {
    setSession((current) => current.context === context ? {
      ...current, selectedPlaceId: id, selectionVersion: current.selectionVersion + 1,
    } : current);
    cards.current.get(id)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [context]);

  return (
    <section aria-labelledby="places-title" className="overflow-hidden rounded-2xl border border-[#18201d]/10 bg-white">
      <div className="border-b border-[#18201d]/10 p-5 sm:p-6">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id="places-title" className="text-lg font-semibold">上海地点搜索</h2>
          <span className="rounded-full bg-[#edf3ef] px-2.5 py-1 text-xs text-[#315f51]">高德真实 POI</span>
        </div>
        <p className="mt-2 text-xs leading-5 text-[#68726c]">搜索并查看候选地点。为活动选择地点后，点击“确认绑定”确认；只选中卡片或标记不会自动绑定。</p>
        {itinerary && <p className="mt-1 text-[11px] leading-5 text-[#68726c]">绑定仅保留在本次结果页；修改需求或刷新后清除。</p>}
      </div>
      <div className="space-y-4 p-5 sm:p-6">
        {itinerary && <div className="flex flex-wrap gap-2" aria-label="选择地图内容">
          <button type="button" aria-pressed={!showingItinerary} onClick={() => onViewChange?.("search")}
            className={`min-h-10 rounded-lg border px-3 py-2 text-xs font-semibold ${!showingItinerary ? "border-[#315f51] bg-[#edf3ef] text-[#315f51]" : "border-[#18201d]/15 text-[#56605c]"}`}>搜索候选地点</button>
          <button type="button" aria-pressed={showingItinerary} onClick={() => onViewChange?.("itinerary")}
            className={`min-h-10 rounded-lg border px-3 py-2 text-xs font-semibold ${showingItinerary ? "border-[#315f51] bg-[#edf3ef] text-[#315f51]" : "border-[#18201d]/15 text-[#56605c]"}`}>当天已绑定地点</button>
        </div>}
        {bindingTarget && <div className="rounded-xl border border-[#315f51]/30 bg-[#edf3ef] p-3">
          <p className="text-xs text-[#315f51]">正在为活动选择地点</p>
          <p className="mt-1 break-words text-sm font-semibold">{bindingTarget.label}</p>
          <button type="button" onClick={bindingTarget.onCancel} className="mt-2 min-h-10 rounded-lg border border-[#315f51]/30 bg-white px-3 py-2 text-xs text-[#315f51]">取消绑定</button>
        </div>}
        {!showingItinerary && <PlaceSearch key={context} initialKeyword={bindingTarget?.keyword}
          loading={searchState.status === "loading"} onSearch={search} />}
        <PlaceMap places={showingItinerary ? itinerary.places : places}
          selectedPlaceId={showingItinerary ? itinerary.selectedPlaceId : selectedPlaceId}
          selectionVersion={showingItinerary ? itinerary.selectionVersion : selectionVersion}
          onSelect={showingItinerary ? itinerary.onSelect : selectPlace}
          fitAllLabel={showingItinerary ? "查看当天全部地点" : "查看全部搜索结果"}
          footnote={showingItinerary ? "编号对应当天已绑定地点；同一地点可用于多个活动，不代表路线。" : undefined} />
        {showingItinerary ? <p aria-live="polite" className="text-xs leading-5 text-[#56605c]">
          {itinerary.activityCount ? `当天 ${itinerary.activityCount} 个活动已绑定，共 ${itinerary.places.length} 个地图地点。点击活动的“在地图查看”或地图标记联动。` : "当天尚未绑定地点，请在活动卡片上点击“绑定地点”。"}
        </p> : <>
          {bindingTarget && <div className="rounded-xl border border-[#18201d]/15 p-3">
            <p className="break-words text-xs leading-5 text-[#56605c]">{selectedPlace ? `已选择：${selectedPlace.name}` : "请先搜索，并选中一个候选地点。"}</p>
            <button type="button" disabled={!selectedPlace || searchState.status !== "success"}
              onClick={() => { if (selectedPlace && searchState.status === "success") bindingTarget.onConfirm(selectedPlace); }}
              className="mt-2 min-h-11 w-full rounded-lg bg-[#18392f] px-3 py-2 text-sm font-semibold text-white focus-visible:outline-2 focus-visible:outline-offset-2 disabled:cursor-not-allowed disabled:opacity-45">确认绑定</button>
          </div>}
          <div aria-live="polite" aria-atomic="true" className="text-sm leading-6 text-[#56605c]">
            {searchState.status === "idle" && <p>尚未搜索地点。</p>}
            {searchState.status === "loading" && <p role="status">正在搜索上海地点…</p>}
            {searchState.status === "error" && <p role="alert" className="text-[#a63d2d]">{searchState.message}</p>}
            {searchState.status === "success" && <p>{places.length ? `找到 ${places.length} 个候选地点，点击卡片或地图标记查看。` : "没有找到匹配地点，请更换关键词。"}</p>}
          </div>
          {places.length > 0 && <ul aria-label="地点搜索结果" className="max-h-[28rem] space-y-3 overflow-y-auto pr-1">
            {places.map((place, index) => (
              <li key={place.id}>
                <button type="button" ref={(element) => {
                  if (element) cards.current.set(place.id, element);
                  else cards.current.delete(place.id);
                }} onClick={() => selectPlace(place.id)} aria-pressed={selectedPlaceId === place.id}
                  className={`w-full rounded-xl border p-4 text-left transition focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#315f51] ${selectedPlaceId === place.id ? "border-[#315f51] bg-[#edf3ef] ring-1 ring-[#315f51]" : "border-[#18201d]/15 bg-white hover:border-[#315f51]/60"}`}>
                  <span className="flex items-start gap-2 text-sm font-semibold"><span className="text-[#315f51]">{index + 1}.</span><span className="min-w-0 break-words">{place.name}</span></span>
                  <span className="mt-2 block break-words text-xs leading-5 text-[#56605c]">地址：{place.address?.trim() || "暂无地址"}</span>
                  <span className="mt-1 block break-words text-xs leading-5 text-[#68726c]">类别：{place.category?.trim() || "暂无类别"}</span>
                  <span className="mt-2 block text-[11px] text-[#68726c]">来源：高德地图</span>
                </button>
              </li>
            ))}
          </ul>}
        </>}
      </div>
    </section>
  );
}
