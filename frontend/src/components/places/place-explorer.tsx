"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { searchPlaces } from "@/lib/places-api";
import type { Place } from "@/types/place";
import PlaceMap from "./place-map";
import PlaceSearch from "./place-search";

type SearchState =
  | { status: "idle" | "loading" | "success" }
  | { status: "error"; message: string };

export default function PlaceExplorer() {
  const [places, setPlaces] = useState<Place[]>([]);
  const [selectedPlaceId, setSelectedPlaceId] = useState<string | null>(null);
  const [selectionVersion, setSelectionVersion] = useState(0);
  const [searchState, setSearchState] = useState<SearchState>({ status: "idle" });
  const requestId = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const cards = useRef(new Map<string, HTMLButtonElement>());

  useEffect(() => () => {
    requestId.current += 1;
    controller.current?.abort();
  }, []);

  async function search(keyword: string) {
    const currentId = ++requestId.current;
    controller.current?.abort();
    const currentController = new AbortController();
    controller.current = currentController;
    setPlaces([]);
    setSelectedPlaceId(null);
    setSearchState({ status: "loading" });
    try {
      const results = await searchPlaces(keyword, currentController.signal);
      if (requestId.current !== currentId || currentController.signal.aborted) return;
      setPlaces(results);
      setSearchState({ status: "success" });
    } catch (error) {
      if (requestId.current !== currentId || currentController.signal.aborted) return;
      setSearchState({ status: "error", message: error instanceof Error ? error.message : "地点搜索失败，请重试。" });
    } finally {
      if (requestId.current === currentId) controller.current = null;
    }
  }

  const selectPlace = useCallback((id: string) => {
    setSelectedPlaceId(id);
    setSelectionVersion((version) => version + 1);
    cards.current.get(id)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, []);

  return (
    <section aria-labelledby="places-title" className="overflow-hidden rounded-2xl border border-[#18201d]/10 bg-white">
      <div className="border-b border-[#18201d]/10 p-5 sm:p-6">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id="places-title" className="text-lg font-semibold">上海地点搜索</h2>
          <span className="rounded-full bg-[#edf3ef] px-2.5 py-1 text-xs text-[#315f51]">高德真实 POI</span>
        </div>
        <p className="mt-2 text-xs leading-5 text-[#68726c]">搜索候选地点并查看地图。选中地点仅用于查看，不会加入或修改当前 Mock 行程。</p>
      </div>
      <div className="space-y-4 p-5 sm:p-6">
        <PlaceSearch loading={searchState.status === "loading"} onSearch={search} />
        <PlaceMap places={places} selectedPlaceId={selectedPlaceId} selectionVersion={selectionVersion} onSelect={selectPlace} />
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
      </div>
    </section>
  );
}
