"use client";

import { useEffect, useRef, useState } from "react";
import { searchPlaces } from "@/lib/places-api";
import type { Place } from "@/types/place";
import PlaceDetails from "./place-details";
import PlaceSearch from "./place-search";

type SearchState =
  | { status: "idle" | "loading" | "success" }
  | { status: "error"; message: string };

export default function RequirementPlacePicker({ title, initialKeyword = "", excludedIds = [], sessionSignal, onConfirm, onCancel }: {
  title: "住宿参考点" | "必去地点";
  initialKeyword?: string;
  excludedIds?: readonly string[];
  sessionSignal: AbortSignal;
  onConfirm: (place: Place) => void;
  onCancel: () => void;
}) {
  const [places, setPlaces] = useState<Place[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [state, setState] = useState<SearchState>({ status: "idle" });
  const region = useRef<HTMLElement>(null);
  const controller = useRef<AbortController | null>(null);
  const requestVersion = useRef(0);
  const selectedPlace = places.find((place) => place.id === selectedId);
  const duplicate = selectedPlace !== undefined && excludedIds.includes(selectedPlace.id);

  useEffect(() => {
    const invalidate = () => {
      requestVersion.current += 1;
      controller.current?.abort();
      controller.current = null;
    };
    sessionSignal.addEventListener("abort", invalidate, { once: true });
    region.current?.scrollIntoView({ block: "start", behavior: "smooth" });
    region.current?.querySelector("input")?.focus({ preventScroll: true });
    return () => {
      sessionSignal.removeEventListener("abort", invalidate);
      invalidate();
    };
  }, [sessionSignal]);

  async function search(keyword: string) {
    if (sessionSignal.aborted) return;
    controller.current?.abort();
    const currentController = new AbortController();
    controller.current = currentController;
    const version = ++requestVersion.current;
    setPlaces([]);
    setSelectedId(null);
    setState({ status: "loading" });
    try {
      const results = await searchPlaces(keyword, currentController.signal);
      if (sessionSignal.aborted || currentController.signal.aborted || version !== requestVersion.current) return;
      setPlaces(results);
      setState({ status: "success" });
    } catch (error) {
      if (sessionSignal.aborted || currentController.signal.aborted || version !== requestVersion.current) return;
      setState({ status: "error", message: error instanceof Error ? error.message : "地点搜索失败，请重试。" });
    } finally {
      if (version === requestVersion.current) controller.current = null;
    }
  }

  return (
    <section ref={region} aria-label={`${title}选择器`} className="mb-8 scroll-mt-5 rounded-2xl border border-[#315f51]/30 bg-[#f4f7f4] p-4 sm:p-5">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-lg font-semibold text-[#18392f]">选择{title}</h3>
          <p className="mt-1 text-xs leading-5 text-[#56605c]">点击候选仅预览，点击“确认{title}”才保存。取消或搜索失败不会更改原确认项。</p>
        </div>
        <button type="button" onClick={onCancel} className="min-h-10 rounded-lg border border-[#315f51]/30 bg-white px-3 py-2 text-sm text-[#315f51]">取消选择</button>
      </div>
      <PlaceSearch initialKeyword={initialKeyword} loading={state.status === "loading"} onSearch={search} />
      <div aria-live="polite" className="my-4 text-sm leading-6 text-[#56605c]">
        {state.status === "idle" && <p>尚未搜索地点。请主动搜索后选择候选。</p>}
        {state.status === "loading" && <p role="status">正在搜索上海地点…</p>}
        {state.status === "error" && <p role="alert" className="text-[#a63d2d]">{state.message}</p>}
        {state.status === "success" && <p>{places.length ? `找到 ${places.length} 个候选地点，请根据名称和地址区分。` : "没有找到匹配地点，请更换关键词。"}</p>}
      </div>
      {places.length > 0 && <ul aria-label="地点搜索结果" className="max-h-80 space-y-3 overflow-y-auto pr-1">
        {places.map((place, index) => <li key={place.id}>
          <button type="button" aria-pressed={selectedId === place.id} onClick={() => setSelectedId(place.id)}
            className={`w-full rounded-xl border bg-white p-4 text-left focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#315f51] ${selectedId === place.id ? "border-[#315f51] ring-1 ring-[#315f51]" : "border-[#18201d]/15"}`}>
            <span className="mb-1 block text-xs text-[#315f51]">候选 {index + 1}</span>
            <PlaceDetails place={place} />
            {excludedIds.includes(place.id) && <span className="mt-2 block text-xs text-[#a63d2d]">已在必去地点中，不可重复添加</span>}
          </button>
        </li>)}
      </ul>}
      <div className="mt-4 rounded-xl border border-[#315f51]/20 bg-white p-3">
        <p className="break-words text-sm leading-6 text-[#56605c]">{selectedPlace ? `预览：${selectedPlace.name}（尚未保存）` : "请先搜索并选中一个候选地点。"}</p>
        {duplicate && <p role="alert" className="mt-1 text-sm text-[#a63d2d]">该 POI 已在必去地点中，请选择其他地点。</p>}
        <button type="button" disabled={!selectedPlace || duplicate || state.status !== "success"}
          onClick={() => {
            if (selectedPlace && !duplicate && state.status === "success" && !sessionSignal.aborted) onConfirm(selectedPlace);
          }} className="mt-3 min-h-11 w-full rounded-xl bg-[#18392f] px-4 py-3 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-45">确认{title}</button>
      </div>
    </section>
  );
}
