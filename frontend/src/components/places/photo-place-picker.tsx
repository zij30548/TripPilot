"use client";

import { useEffect, useRef, useState } from "react";
import { destination, type DestinationId } from "@/lib/destinations";
import { resolvePhotoCity } from "@/lib/photo-city";
import { recognizePhoto, type PhotoResult } from "@/lib/photo-api";
import type { Place } from "@/types/place";
import RequirementPlacePicker from "./requirement-place-picker";

const button = "min-h-11 rounded-lg border px-4 py-2 text-sm disabled:opacity-50";
export type PreparedFrame = { file: File; index: number; targetSeconds: number };
export type PhotoClueSearch = { keyword: string; destinationId: DestinationId; signal: AbortSignal; isCurrent: () => boolean };
export default function PhotoPlacePicker({ destinationId, sessionSignal, excludedIds, onConfirm, onCancel, preparedFrame, capacityAvailable = true, onSearchClue }: {
  destinationId: DestinationId; sessionSignal: AbortSignal; excludedIds: string[];
  onConfirm: (place: Place) => void; onCancel: () => void;
  preparedFrame?: PreparedFrame; capacityAvailable?: boolean;
  /** Chat uses its server-owned candidate batch; legacy callers retain their picker. */
  onSearchClue?: (source: PhotoClueSearch) => void;
}) {
  const [photo, setPhoto] = useState<{ file: File; url: string } | null>(null);
  const current = useRef<typeof photo>(null);
  const [result, setResult] = useState<PhotoResult | null>(null);
  const [error, setError] = useState(""); const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState<{ keyword: string; controller: AbortController; version: number } | null>(null);
  const searchRef = useRef<typeof search>(null);
  const active = useRef<AbortController | null>(null); const version = useRef(0); const alive = useRef(true);
  const renderedVersion = version.current;
  const currentRender = () => alive.current && !sessionSignal.aborted && renderedVersion === version.current;
  const section = useRef<HTMLElement>(null);
  function invalidate() {
    version.current++; active.current?.abort(); active.current = null;
    searchRef.current?.controller.abort(); searchRef.current = null;
  }
  function release() { if (current.current) URL.revokeObjectURL(current.current.url); current.current = null; }
  useEffect(() => {
    alive.current = true;
    const stop = () => { invalidate(); release(); };
    sessionSignal.addEventListener("abort", stop, { once: true });
    if (preparedFrame && !sessionSignal.aborted) {
      current.current = { file: preparedFrame.file, url: URL.createObjectURL(preparedFrame.file) };
      setPhoto(current.current);
    }
    section.current?.focus();
    return () => { alive.current = false; stop(); sessionSignal.removeEventListener("abort", stop); };
  }, [sessionSignal, preparedFrame]);
  function choose(file: File | undefined) {
    if (!alive.current || sessionSignal.aborted) return;
    invalidate(); release(); setSearch(null); setResult(null); setError(""); setLoading(false); setPhoto(null);
    if (!file) return;
    if (!file.size || file.size > 5 * 1024 * 1024 || !["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
      setError("请选择不超过 5 MiB 的 JPEG、PNG 或静态 WebP；服务端还会检查实际内容与像素。"); return;
    }
    current.current = { file, url: URL.createObjectURL(file) }; setPhoto(current.current);
  }
  async function recognize() {
    if (!currentRender() || !capacityAvailable || !current.current || active.current) return;
    invalidate(); const epoch = version.current; const controller = new AbortController(); active.current = controller;
    setSearch(null); setResult(null); setError(""); setLoading(true);
    const valid = () => alive.current && !sessionSignal.aborted && !controller.signal.aborted && version.current === epoch;
    try {
      const data = await recognizePhoto(current.current.file, destinationId, crypto.randomUUID(), controller.signal);
      if (valid()) setResult(data);
    } catch (e) { if (valid()) setError(e instanceof Error ? e.message : "照片识别未完成。"); }
    finally { if (valid()) { active.current = null; setLoading(false); } }
  }
  return <section ref={section} tabIndex={-1} aria-label={preparedFrame ? "选中画面发现必去地点" : "照片发现必去地点"} className="mb-6 space-y-4 rounded-xl border bg-stone-50 p-4">
    <div className="flex flex-wrap justify-between gap-2"><h3 className="font-semibold">{preparedFrame ? "选中画面发现必去地点" : "照片发现必去地点"} · {destination(destinationId).name}</h3>
      <button type="button" className={button} onClick={() => { if (!currentRender()) return; invalidate(); release(); onCancel(); }}>{preparedFrame ? "关闭画面识别" : "关闭照片入口"}</button></div>
    <p className="text-sm leading-6">{preparedFrame ? `画面${preparedFrame.index} · 相对视频起点约${preparedFrame.targetSeconds.toFixed(2)}秒（目标采样时间，非精确帧时间）。点击“识别选中画面”才将这一张图片发送到千问，不发送整段视频。` : "选择仅在本地预览。点击识别后，照片将发送到千问；"}服务器会转正、去除元数据并缩小，不持久保存。请勿上传敏感个人内容或无权使用的照片。</p>
    {!preparedFrame && <label className="block text-sm">选择一张照片（JPEG / PNG / 静态 WebP，≤5 MiB、≤2000万像素）
      <input className="mt-2 block w-full min-w-0 text-sm" type="file" accept="image/jpeg,image/png,image/webp"
        onChange={e => { const file = e.target.files?.[0]; e.target.value = ""; choose(file); }} /></label>}
    {photo && <img src={photo.url} alt="待识别照片的本地预览" className="max-h-72 w-full rounded-lg object-contain" /> /* eslint-disable-line @next/next/no-img-element */}
    {!capacityAvailable && <p role="status" className="text-sm">本阶段最多六个必去地点，请先移除一个再识别。</p>}
    <div className="flex flex-wrap gap-2"><button type="button" className={button} disabled={!photo || loading || !capacityAvailable} onClick={() => void recognize()}>{loading ? "正在识别…" : preparedFrame ? "识别选中画面" : "识别照片"}</button>
      {loading && <button type="button" className={button} onClick={() => { if (!currentRender()) return; invalidate(); setLoading(false); setError("已取消等待；不能保证上游停止处理或计费。"); }}>取消识别</button>}</div>
    {error && <p role="alert" className="text-sm text-red-800">{error}</p>}
    {result && <div className="space-y-3" aria-live="polite"><p className="text-sm">{result.status === "unrecognized" ? "未识别出可用地标，请改用普通手动搜索。" : result.status === "uncertain" ? "无法确定照片中地点的身份，未提供具体候选；请改用普通手动搜索。" : "以下仅是可能地点，不代表照片与地点已核实。"} 仍需核对高德地址并明确确认；识别不会直接加入必去。</p>
      {result.clues.map((clue, index) => {
        const clueCity = resolvePhotoCity(clue.city);
        const matches = clueCity === destinationId;
        return <div className="space-y-2 rounded-lg border bg-white p-3" key={index}>
          <p className="font-medium">可能地点：{clue.name}</p><p className="text-sm">可能城市：{clue.city ?? "不明"}</p>
          {clueCity && <p className="text-sm">城市名称对应：{destination(clueCity).name}（仅统一名称表达，未核实地点）</p>}
          <p className="text-sm text-amber-800">模型线索，未经核实。高德同名结果不代表照片匹配。</p>
          {!matches && <p className="text-sm text-amber-800">{clue.city === null ? "城市不明，不能据此搜索加入；请关闭后手动搜索核对。" : clueCity ? "与当前城市不匹配，不搜索同名地点，也不自动切城。" : "不在当前六城支持范围，不能加入；若城市表述不明确，请走普通手动搜索。"}</p>}
          <button type="button" className={button} disabled={!matches} onClick={() => {
            if (!currentRender() || !matches) return;
            searchRef.current?.controller.abort();
            const next = { keyword: clue.name, controller: new AbortController(), version: ++version.current };
            searchRef.current = next; setSearch(next);
            onSearchClue?.({ keyword: clue.name, destinationId, signal: next.controller.signal,
              isCurrent: () => alive.current && !sessionSignal.aborted && searchRef.current === next && !next.controller.signal.aborted });
          }}>{onSearchClue ? "用此线索搜索高德" : "用此线索准备搜索"}</button>
        </div>;
      })}</div>}
    {search && !onSearchClue && <RequirementPlacePicker key={search.version} destinationId={destinationId} title="必去地点" initialKeyword={search.keyword}
      excludedIds={excludedIds} sessionSignal={search.controller.signal} onCancel={() => {
        if (!alive.current || sessionSignal.aborted || searchRef.current !== search || search.controller.signal.aborted) return;
        searchRef.current?.controller.abort(); searchRef.current = null; setSearch(null);
      }} onConfirm={p => { if (alive.current && capacityAvailable && !excludedIds.includes(p.id) && !sessionSignal.aborted && searchRef.current === search && !search.controller.signal.aborted) onConfirm(p); }} />}
  </section>;
}
