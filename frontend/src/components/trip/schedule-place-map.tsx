"use client";

import PlaceMap from "@/components/places/place-map";
import type { useScheduleMap } from "@/lib/use-schedule-map";

export default function SchedulePlaceMap({ map }: { map: ReturnType<typeof useScheduleMap> }) {
  const { data, mapRegion, home, opened, initialized, selection, selectionVersion, toggle, locateHome, selectMarker, isCurrent, route, routeVersion, clearRoute } = map;
  return <section ref={mapRegion} tabIndex={-1} aria-label="正式草案当天地点地图" className="mt-4 min-w-0 scroll-mt-5 rounded-xl border border-[#315f51]/25 bg-white p-3 outline-none sm:p-4">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="font-semibold text-[#18392f]">{data.date} · 当天地点地图</h3>
      <button type="button" aria-expanded={opened} onClick={toggle} className="min-h-11 rounded-lg border border-[#315f51]/30 px-3 text-sm text-[#315f51]">{opened ? "收起当天地图" : "查看当天地图"}</button>
    </div>
    <p className="mt-2 text-xs leading-5 text-[#56605c]">显示当前正式草案的住宿与当天已安排地点。数字表示访问顺序，不是全天道路路线。点击交通条目可查看本次查询对应的一段参考路线，不是实时导航或旅行当天运营保证。</p>
    {!data.visits.length && <p role="status" className="mt-3 text-sm text-[#68726c]">当天尚未安排景点，地图没有地点标记。</p>}
    <div ref={home} tabIndex={-1} data-map-selected={selection?.target === "home"} className={`mt-3 rounded-lg p-3 text-sm outline-none ${selection?.target === "home" ? "bg-amber-50 ring-2 ring-[#bd4c35]" : "bg-[#f4f8f5]"}`}>
      <p className="font-medium">住 · 住宿参考点：{data.home.name}</p>
      <p className="mt-1 text-xs">{data.home.address || "暂未提供地址"} · 来源：高德地点快照</p>
      <p className="mt-1 text-xs">出发与返回共用此标记，不代表已预订住宿。</p>
      {data.visits.filter(v => v.placeId === data.home.id).map(v => <p key={v.itemIndex} className="mt-1 text-xs">也安排为第 {v.number} 个游览地点，停留仍保留在时间轴。</p>)}
      {data.visits.length > 0 && <button type="button" onClick={locateHome} className="mt-2 min-h-11 text-sm font-medium text-[#315f51] underline">在地图查看住宿参考点</button>}
    </div>
    {data.invalidCount > 0 && <p role="alert" className="mt-3 text-sm text-[#a63d2d]">部分地点缺少有效坐标，无法定位；时间轴和其他功能仍可使用。</p>}
    {/* Keep the instance through collapse. SDK observes its container, not workspace state. */}
    <div hidden={!opened} className="mt-3">
      {route && <div aria-label="当前交通路线" className="mb-3 rounded-lg bg-[#edf3ef] p-3 text-sm">
        <p className="break-words font-semibold">{route.origin} → {route.destination}</p>
        <p className="mt-1 text-xs">{route.message}</p>
        <p className="mt-1 text-xs">来源：{route.walking || route.transit ? "高德路线" : "本次草案交通记录"} · 查询／判定时间：{new Date(route.queriedAt).toLocaleString("zh-CN", { hour12: false })}</p>
        <button type="button" onClick={clearRoute} className="mt-2 min-h-11 rounded-lg border border-[#315f51]/30 px-3">清除路线</button>
      </div>}
      {initialized && <PlaceMap places={data.places} markerLabels={data.labels} selectedPlaceId={selection?.placeId ?? null}
        selectionVersion={selectionVersion} onSelect={selectMarker} isActive={isCurrent}
        walkingRoute={route?.walking} transitRoute={route?.transit} routeSelectionVersion={routeVersion} fitRouteLabel="查看这段路线全貌"
        fitAllLabel="查看当天全部地点" footnote="住：住宿参考点；数字：当天实际访问顺序。一次只展示选中的交通，不拼接全天路线。" />}
    </div>
  </section>;
}
