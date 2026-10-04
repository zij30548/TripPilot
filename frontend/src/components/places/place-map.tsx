"use client";

import { useEffect, useRef, useState } from "react";
import { getShanghaiCenter, loadAMap, type AMapSDK } from "@/lib/amap-loader";
import { hasValidCoordinates, type Place } from "@/types/place";
import type { WalkingRouteResponse } from "@/types/route";

type MapStatus = { status: "loading" } | { status: "ready" } | { status: "error"; message: string };
type MarkerEntry = { marker: AMap.Marker; element: HTMLDivElement; onClick: () => void };

export default function PlaceMap({ places, selectedPlaceId, selectionVersion = 0, onSelect,
  fitAllLabel = "查看全部搜索结果", footnote = "编号对应搜索候选地点，不代表行程顺序或路线。",
  walkingRoute, routeSelectionVersion = 0 }: {
  places: Place[];
  selectedPlaceId: string | null;
  selectionVersion?: number;
  onSelect: (id: string) => void;
  fitAllLabel?: string;
  footnote?: string;
  walkingRoute?: WalkingRouteResponse | null;
  routeSelectionVersion?: number;
}) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<AMap.Map | null>(null);
  const sdk = useRef<AMapSDK | null>(null);
  const markers = useRef(new Map<string, MarkerEntry>());
  const routeLine = useRef<AMap.Polyline | null>(null);
  const onSelectRef = useRef(onSelect);
  const [mapStatus, setMapStatus] = useState<MapStatus>({ status: "loading" });
  const invalidCount = places.filter((place) => !hasValidCoordinates(place)).length;
  const route = walkingRoute?.status === "ok" ? walkingRoute.route : null;
  const validRoute = route && route.segments.length > 0 && route.segments.every((segment) =>
    segment.length >= 2 && segment.every(([longitude, latitude]) => hasValidCoordinates({ longitude, latitude })),
  );

  useEffect(() => {
    onSelectRef.current = onSelect;
  }, [onSelect]);

  useEffect(() => {
    const controller = new AbortController();
    const markerEntries = markers.current;
    const timeout = setTimeout(() => {
      controller.abort();
      setMapStatus({ status: "error", message: "地图加载超时，请检查网络或刷新后重试。" });
    }, 20_000);

    async function initialize() {
      try {
        const amap = await loadAMap();
        if (controller.signal.aborted) return;
        const center = await getShanghaiCenter(amap, controller.signal);
        if (controller.signal.aborted || !container.current) return;
        // Passing a verified center prevents the SDK from using IP location.
        map.current = new amap.Map(container.current, { center, zoom: 11, viewMode: "2D" });
        sdk.current = amap;
        clearTimeout(timeout);
        setMapStatus({ status: "ready" });
      } catch (error) {
        if (controller.signal.aborted) return;
        clearTimeout(timeout);
        setMapStatus({ status: "error", message: error instanceof Error ? error.message : "地图加载失败，请刷新后重试。" });
      }
    }
    void initialize();

    return () => {
      clearTimeout(timeout);
      controller.abort();
      markerEntries.forEach(({ marker, onClick }) => {
        marker.off("click", onClick);
        marker.setMap(null);
      });
      markerEntries.clear();
      routeLine.current?.setMap(null);
      routeLine.current = null;
      map.current?.destroy();
      map.current = null;
      sdk.current = null;
    };
  }, []);

  useEffect(() => {
    if (mapStatus.status !== "ready" || !map.current || !sdk.current) return;
    const currentMap = map.current;
    const markerEntries = markers.current;
    markerEntries.forEach(({ marker, onClick }) => {
      marker.off("click", onClick);
      marker.setMap(null);
    });
    markerEntries.clear();

    places.forEach((place, index) => {
      if (!hasValidCoordinates(place)) return;
      const element = document.createElement("div");
      element.textContent = String(index + 1);
      element.style.cssText = "display:grid;place-items:center;width:30px;height:30px;border-radius:50%;border:2px solid white;background:#315f51;color:white;font:600 13px sans-serif;box-shadow:0 2px 6px #0004;cursor:pointer";
      const marker = new sdk.current!.Marker({ position: [place.longitude, place.latitude], title: place.name, content: element, anchor: "center", map: currentMap });
      const onClick = () => onSelectRef.current(place.id);
      marker.on("click", onClick);
      markerEntries.set(place.id, { marker, element, onClick });
    });
    if (markerEntries.size > 0) currentMap.setFitView(Array.from(markerEntries.values(), ({ marker }) => marker), false, [40, 40, 40, 40], 16);

    return () => {
      markerEntries.forEach(({ marker, onClick }) => {
        marker.off("click", onClick);
        marker.setMap(null);
      });
      markerEntries.clear();
    };
  }, [places, mapStatus.status]);

  useEffect(() => {
    if (mapStatus.status !== "ready") return;
    markers.current.forEach(({ marker, element }, id) => {
      const selected = id === selectedPlaceId;
      element.style.background = selected ? "#bd4c35" : "#315f51";
      element.style.transform = selected ? "scale(1.2)" : "scale(1)";
      marker.setzIndex(selected ? 200 : 100);
    });
    const selected = places.find((place) => place.id === selectedPlaceId && hasValidCoordinates(place));
    if (selected) map.current?.setZoomAndCenter(16, [selected.longitude, selected.latitude]);
  }, [selectedPlaceId, selectionVersion, places, mapStatus.status]);

  useEffect(() => {
    if (mapStatus.status !== "ready" || !map.current || !sdk.current || !route || !validRoute) return;
    // Keep upstream steps separate: never bridge gaps with an invented line.
    const line = new sdk.current.Polyline({
      path: route.segments,
      strokeColor: "#2563eb",
      strokeWeight: 6,
      strokeOpacity: 0.9,
      isOutline: true,
      outlineColor: "#ffffff",
      borderWeight: 2,
      lineJoin: "round",
      lineCap: "round",
      zIndex: 50,
    });
    line.setMap(map.current);
    routeLine.current = line;
    return () => {
      line.setMap(null);
      if (routeLine.current === line) routeLine.current = null;
    };
  }, [route, validRoute, mapStatus.status]);

  useEffect(() => {
    if (routeLine.current && map.current) {
      map.current.setFitView([routeLine.current], false, [48, 48, 48, 48], 17);
    }
  }, [route, routeSelectionVersion, mapStatus.status]);

  function fitAll() {
    if (!map.current || markers.current.size === 0) return;
    map.current.setFitView(Array.from(markers.current.values(), ({ marker }) => marker), false, [40, 40, 40, 40], 16);
  }

  function fitRoute() {
    if (map.current && routeLine.current) {
      map.current.setFitView([routeLine.current], false, [48, 48, 48, 48], 17);
    }
  }

  return (
    <div className="overflow-hidden rounded-xl border border-[#18201d]/15 bg-[#f3f2ec]">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#18201d]/10 bg-white px-3 py-2">
        <p className="text-xs text-[#56605c]">上海地图 · 高德地图</p>
        <button type="button" onClick={fitAll} disabled={mapStatus.status !== "ready" || places.length === invalidCount}
          className="rounded-lg border border-[#315f51]/30 px-3 py-1.5 text-xs font-medium text-[#315f51] focus-visible:outline-2 focus-visible:outline-offset-2 disabled:cursor-not-allowed disabled:opacity-45">{fitAllLabel}</button>
      </div>
      {route && <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#18201d]/10 bg-blue-50 px-3 py-2">
        <p className="text-xs text-blue-800">蓝线：当前查询的高德步行路线 · 仅供参考</p>
        <button type="button" onClick={fitRoute} disabled={mapStatus.status !== "ready" || !validRoute}
          className="min-h-10 rounded-lg border border-blue-300 bg-white px-3 py-2 text-xs font-medium text-blue-800 focus-visible:outline-2 focus-visible:outline-offset-2 disabled:cursor-not-allowed disabled:opacity-45">查看完整步行路线</button>
      </div>}
      <div className="relative">
        <div ref={container} aria-label="上海地点地图" className="h-80 w-full sm:h-96" />
        {mapStatus.status !== "ready" && <div className="absolute inset-0 grid place-items-center bg-[#f3f2ec] px-5 text-center text-sm leading-6 text-[#56605c]">
          {mapStatus.status === "loading" ? <p role="status">正在加载上海地图…</p> : <p role="alert">{mapStatus.message}</p>}
        </div>}
      </div>
      {invalidCount > 0 && <p role="alert" className="border-t border-[#18201d]/10 px-3 py-2 text-xs leading-5 text-[#a63d2d]">{invalidCount} 个地点坐标无效，未在地图上显示。</p>}
      {route && !validRoute && <p role="alert" className="border-t border-[#18201d]/10 px-3 py-2 text-xs text-[#a63d2d]">步行路线坐标无效，未绘制路线。</p>}
      <p className="border-t border-[#18201d]/10 px-3 py-2 text-[11px] leading-5 text-[#68726c]">{footnote}</p>
    </div>
  );
}
