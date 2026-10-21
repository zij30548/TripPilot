"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import PhotoPlacePicker, { type PhotoClueSearch } from "@/components/places/photo-place-picker";
import VideoFramePicker from "@/components/places/video-frame-picker";
import { destination, type DestinationId } from "@/lib/destinations";
import type { DialoguePlacesController } from "@/lib/dialogue-places-controller";

const button = "min-h-11 rounded-lg border border-stone-300 px-3 py-2 text-sm disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-offset-2";
type Search = { context: object; source: PhotoClueSearch; batchId: string; message: string };

/** Attachment-only modal. POI authority and confirmation remain in the shared chat controller. */
export default function DialogueMedia({ mode, sessionKey, destinationId, places, onClose, onResult }: {
  mode: "photo" | "video"; sessionKey: string; destinationId: DestinationId;
  places: DialoguePlacesController; onClose: () => void; onResult: (text: string) => void;
}) {
  const context = useMemo(() => ({ sessionKey, destinationId, mode, controller: new AbortController() }), [sessionKey, destinationId, mode]);
  const latest = useRef(context), dialog = useRef<HTMLDivElement>(null), activeSearch = useRef<PhotoClueSearch | null>(null);
  const [search, setSearch] = useState<Search | null>(null), [message, setMessage] = useState("");
  const workspace = places.state.workspace;
  const ids = workspace?.required_places.map(p => p.id) ?? [];
  const batch = search?.context === context && search.source.isCurrent() && !search.source.signal.aborted && workspace?.batch?.id === search.batchId ? workspace.batch : null;
  useEffect(() => {
    const previous = document.activeElement;
    latest.current = context; dialog.current?.focus();
    return () => { context.controller.abort(); if (previous instanceof HTMLElement && previous.isConnected) previous.focus(); };
  }, [context]);
  const valid = () => latest.current === context && !context.controller.signal.aborted;
  function close() { if (!valid()) return; context.controller.abort(); onClose(); }
  async function searchClue(source: PhotoClueSearch) {
    if (!valid() || source.destinationId !== destinationId || !source.isCurrent() || source.signal.aborted || ids.length >= 6 ||
      !places.canUse() || workspace?.city_id !== destination(destinationId).adcode) return;
    activeSearch.current = source; setSearch(null); setMessage("正在查询真实地点…");
    source.signal.addEventListener("abort", () => {
      if (valid() && activeSearch.current === source) { activeSearch.current = null; setSearch(null); setMessage(""); }
    }, { once: true });
    const result = await places.search(source.keyword, "required", source);
    if (!valid() || activeSearch.current !== source || !source.isCurrent() || source.signal.aborted) return;
    setMessage(result.text);
    if (result.workspace?.batch) setSearch({ context, source, batchId: result.workspace.batch.id, message: result.text });
  }
  async function confirm(index: number) {
    if (!valid() || !batch || !search || !search.source.isCurrent() || search.source.signal.aborted) return;
    const expected = batch.places[index - 1];
    if (!expected || ids.length >= 6 || ids.includes(expected.id)) return;
    const result = await places.confirm(index, places.state.preview?.token);
    if (!valid() || !search.source.isCurrent() || search.source.signal.aborted) return;
    setMessage(result.text);
    if (result.workspace?.required_places.some(p => p.id === expected.id) && result.workspace.last_operation?.state === "succeeded") {
      onResult(result.text); close();
    }
  }
  return <div className="fixed inset-0 z-50 bg-black/25 p-3 sm:p-8" onClick={event => { if (event.target === event.currentTarget) close(); }}>
    <div ref={dialog} role="dialog" aria-modal="true" aria-label={mode === "photo" ? "照片地点线索" : "视频画面地点线索"} tabIndex={-1}
      className="mx-auto max-h-full w-full max-w-2xl overflow-y-auto rounded-2xl bg-white p-4 shadow-xl sm:p-6"
      onKeyDown={event => {
        if (event.key === "Escape") { event.preventDefault(); close(); }
        if (event.key !== "Tab") return;
        const focusable = [...event.currentTarget.querySelectorAll<HTMLElement>("button:not(:disabled),input:not(:disabled),textarea:not(:disabled),select:not(:disabled),a[href],video[controls],[tabindex='0']")];
        const first = focusable[0], last = focusable.at(-1);
        if (event.shiftKey && (document.activeElement === first || document.activeElement === event.currentTarget)) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }}>
      {mode === "photo" ? <PhotoPlacePicker key={`${sessionKey}:${destinationId}`} destinationId={destinationId} sessionSignal={context.controller.signal}
        excludedIds={ids} capacityAvailable={ids.length < 6} onCancel={close} onConfirm={() => undefined} onSearchClue={source => void searchClue(source)} /> :
        <VideoFramePicker key={`${sessionKey}:${destinationId}`} destinationId={destinationId} sessionSignal={context.controller.signal}
          excludedIds={ids} onClose={close} onSearchClue={source => void searchClue(source)} />}
      <p className="text-xs leading-6 text-stone-500">图片只提供线索。高德同名结果不证明画面身份；核对地址后明确确认，才加入当前需求。媒体不会写入行程存档。</p>
      {message && <p role="status" className="mt-3 whitespace-pre-wrap break-words text-sm leading-6">{message}</p>}
      {batch && <ol className="mt-3 divide-y divide-stone-200">{batch.places.map((place, index) => <li key={place.id} className="py-3 text-sm">
        <p>{index + 1}. {place.name}</p><p className="mt-1 text-stone-600">{place.address ?? "上游未提供地址"}</p>
        <div className="mt-2 flex flex-wrap gap-2"><button type="button" className={button} disabled={places.state.pending} onClick={() => {
          if (!valid() || !search?.source.isCurrent() || search.source.signal.aborted) return;
          setMessage(places.preview(index + 1).text);
        }}>预览地点{index + 1}</button>
          {places.state.preview?.batchId === batch.id && places.state.preview.placeId === place.id && <button type="button" className={button}
            disabled={places.state.pending || ids.length >= 6 || ids.includes(place.id)} onClick={() => void confirm(index + 1)}>确认加入必去</button>}</div>
      </li>)}</ol>}
    </div>
  </div>;
}
