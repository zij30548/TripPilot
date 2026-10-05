import type { Place } from "@/types/place";

export default function PlaceDetails({ place }: { place: Place }) {
  return <span className="block min-w-0 space-y-1 break-words text-sm">
    <span className="block font-semibold">{place.name}</span>
    <span className="block">地址：{place.address?.trim() || "暂未提供地址"}</span>
    {place.category && <span className="block text-xs">类别：{place.category}</span>}
    <span className="block text-xs">来源：高德地图</span>
  </span>;
}
