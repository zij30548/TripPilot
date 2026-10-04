import type { Activity } from "@/types/trip";
import type { Place } from "@/types/place";
import { isSameWalkingPlace, type WalkingRouteState } from "@/lib/use-walking-route";

export default function WalkingRouteSegment({ fromActivity, toActivity, origin, destination, state, onQuery, onShowRoute }: {
  fromActivity: Activity;
  toActivity: Activity;
  origin?: Place;
  destination?: Place;
  state?: WalkingRouteState;
  onQuery?: () => void;
  onShowRoute?: () => void;
}) {
  const missingEndpoint = !origin || !destination;
  const samePlace = !!origin && !!destination && isSameWalkingPlace(origin, destination);
  const loading = state?.status === "loading";
  const queriedResponse = state && "response" in state ? state.response : null;
  const response = state?.status === "success" && state.response.status === "ok" ? state.response : null;
  const distance = response && (response.route.distance_meters < 1000 ?
    `${Math.round(response.route.distance_meters)} 米` : `${(response.route.distance_meters / 1000).toFixed(2)} 公里`);

  return <section aria-label={`真实步行路线：${fromActivity.name} → ${toActivity.name}`}
    className="mb-5 ml-3 rounded-xl border border-[#315f51]/25 bg-[#f4f8f5] p-4 sm:ml-6">
    <p className="text-xs font-semibold text-[#315f51]">相邻活动 · 真实步行路线参考</p>
    <p className="mt-2 break-words text-sm font-medium">{origin?.name ?? fromActivity.name} → {destination?.name ?? toActivity.name}</p>
    <p className="mt-1 text-xs leading-5 text-[#68726c]">仅查询已确认地点之间的步行路线；不会改写上方 Mock 交通、活动时间、费用或预算。</p>
    {missingEndpoint && <p className="mt-2 text-xs leading-5 text-[#56605c]">请先为前后两个相邻活动确认绑定地点。</p>}
    {!missingEndpoint && samePlace && <p className="mt-2 text-xs leading-5 text-[#315f51]">这两个活动绑定的是同一地点，无需查询步行路线。</p>}
    <div className="mt-3 flex flex-wrap gap-2">
      <button type="button" disabled={missingEndpoint || samePlace || loading || !onQuery} onClick={onQuery}
        className="min-h-11 rounded-lg bg-[#315f51] px-3 py-2 text-xs font-semibold text-white focus-visible:outline-2 focus-visible:outline-offset-2 disabled:cursor-not-allowed disabled:opacity-45">
        {loading ? "正在查询步行路线…" : "查询步行路线"}
      </button>
      {response && <button type="button" onClick={onShowRoute}
        className="min-h-11 rounded-lg border border-[#315f51]/40 bg-white px-3 py-2 text-xs font-semibold text-[#315f51] focus-visible:outline-2 focus-visible:outline-offset-2">在地图查看路线</button>}
    </div>
    <div aria-live="polite" aria-atomic="true" className="mt-2 text-xs leading-5">
      {loading && <p role="status">正在向高德查询真实步行路线…</p>}
      {response && <>
        <p className="font-semibold text-[#315f51]">{distance} · 预计 {Math.ceil(response.route.duration_seconds / 60)} 分钟</p>
        <p className="text-[#68726c]">耗时为路线服务估计，不包含游览或停留时间。</p>
      </>}
      {state?.status === "no_route" && <p role="status" className="text-[#56605c]">未找到可用步行路线，请更换地点后重试。</p>}
      {state?.status === "same_place" && !samePlace && <p role="status" className="text-[#315f51]">两个端点位于同一地点，无需步行路线。</p>}
      {queriedResponse && <p className="mt-1 text-[#56605c]">来源：高德步行路线 · 查询时间：<time dateTime={queriedResponse.queried_at}>{new Date(queriedResponse.queried_at).toLocaleString("zh-CN", { hour12: false })}</time></p>}
      {(state?.status === "timeout" || state?.status === "error") && <p role="alert" className="text-[#a63d2d]">{state.message}</p>}
    </div>
  </section>;
}
