import type { Activity } from "@/types/trip";
import type { Place } from "@/types/place";
import type { TransitRouteState } from "@/lib/use-transit-route";
import { isSameWalkingPlace } from "@/lib/use-walking-route";

function distance(meters: number): string {
  return meters < 1000 ? `${Math.round(meters)} 米` : `${(meters / 1000).toFixed(2)} 公里`;
}

export default function TransitRouteSegment({ fromActivity, toActivity, origin, destination, state, onQuery, onShowRoute }: {
  fromActivity: Activity;
  toActivity: Activity;
  origin?: Place;
  destination?: Place;
  state?: TransitRouteState;
  onQuery?: () => void;
  onShowRoute?: () => void;
}) {
  const missingEndpoint = !origin || !destination;
  const samePlace = !!origin && !!destination && isSameWalkingPlace(origin, destination);
  const loading = state?.status === "loading";
  const queriedResponse = state && "response" in state ? state.response : null;
  const route = state?.status === "success" && state.response.status === "ok" ? state.response.route : null;

  return <section aria-label={`真实公交／地铁路线：${fromActivity.name} → ${toActivity.name}`}
    className="mb-5 ml-3 rounded-xl border border-[#315f51]/25 bg-[#f4f8f5] p-4 sm:ml-6">
    <p className="text-xs font-semibold text-[#315f51]">相邻活动 · 上海公交／地铁参考方案</p>
    <p className="mt-2 break-words text-sm font-medium">{origin?.name ?? fromActivity.name} → {destination?.name ?? toActivity.name}</p>
    <p className="mt-1 text-xs leading-5 text-[#68726c]">本次按接口默认条件查询，尚未按旅行日期校验运营时刻，不保证班次。不会改写 Mock 交通、活动时间、费用或预算。</p>
    {missingEndpoint && <p className="mt-2 text-xs leading-5 text-[#56605c]">请先为前后两个相邻活动确认绑定地点。</p>}
    {!missingEndpoint && samePlace && <p className="mt-2 text-xs leading-5 text-[#315f51]">这两个活动绑定的是同一地点，无需查询公交／地铁。</p>}
    <div className="mt-3 flex flex-wrap gap-2">
      <button type="button" disabled={missingEndpoint || samePlace || loading || !onQuery} onClick={onQuery}
        className="min-h-11 rounded-lg bg-[#315f51] px-3 py-2 text-xs font-semibold text-white focus-visible:outline-2 focus-visible:outline-offset-2 disabled:cursor-not-allowed disabled:opacity-45">
        {loading ? "正在查询公交／地铁路线…" : "查询公交／地铁路线"}
      </button>
      {route && <button type="button" onClick={onShowRoute}
        className="min-h-11 rounded-lg border border-[#315f51]/40 bg-white px-3 py-2 text-xs font-semibold text-[#315f51] focus-visible:outline-2 focus-visible:outline-offset-2">在地图查看方案</button>}
    </div>
    <div aria-live="polite" aria-atomic="true" className="mt-2 text-xs leading-5">
      {loading && <p role="status">正在向高德查询真实公交／地铁方案…</p>}
      {route && <>
        <p className="font-semibold text-[#315f51]">预计 {Math.ceil(route.duration_seconds / 60)} 分钟 · 接驳步行 {distance(route.walking_distance_meters)}</p>
        <p className="font-semibold text-[#315f51]">参考票价：{route.fare_cny === null ? "未知" : `¥${route.fare_cny.toFixed(2)}`}</p>
        <p className="mt-1 text-[#68726c]">总耗时使用上游方案估计，不另加接驳时间；票价不乘旅行人数，不计入 Mock 预算。</p>
        <ol aria-label="公交／地铁出行步骤" className="mt-3 space-y-3">
          {route.legs.map((leg, index) => <li key={index} className="break-words rounded-lg border border-[#315f51]/15 bg-white p-3">
            <p className="font-semibold text-[#315f51]">{index + 1}. {leg.mode === "walking" ? "步行接驳" : leg.mode === "subway" ? "地铁" : "公交"}{leg.line_name ? ` · ${leg.line_name}` : ""}</p>
            {leg.mode !== "walking" && <p className="mt-1">上车：{leg.departure_stop}<br />下车：{leg.arrival_stop}</p>}
            {leg.instruction && <p className="mt-1">{leg.instruction}</p>}
            {(leg.distance_meters !== null || leg.duration_seconds !== null) && <p className="mt-1 text-[#68726c]">
              {leg.distance_meters !== null && distance(leg.distance_meters)}
              {leg.distance_meters !== null && leg.duration_seconds !== null && " · "}
              {leg.duration_seconds !== null && `预计 ${Math.ceil(leg.duration_seconds / 60)} 分钟`}
            </p>}
            {!leg.geometry_complete && <p className="mt-1 text-[#9a5c20]">此段地图几何未完整提供。</p>}
          </li>)}
        </ol>
        {!route.geometry_complete && <p className="mt-2 text-[#9a5c20]">地图不完整：仅展示已提供的分段几何，不用直线补齐缺口。</p>}
        <p className="mt-2 text-[#68726c]">按高德返回顺序选首条信息完整且支持的方案；同段备选线路也按顺序选首条有效线路，不代表最快或最优。</p>
      </>}
      {state?.status === "no_route" && <p role="status" className="text-[#56605c]">未找到公交／地铁方案，请更换地点后重试。</p>}
      {state?.status === "unsupported" && <p role="status" className="text-[#56605c]">没有可展示的受支持方案；当前仅支持信息完整的城市公交和地铁，不展示纯步行、出租车或长途铁路方案。</p>}
      {state?.status === "same_place" && !samePlace && <p role="status" className="text-[#315f51]">两个端点位于同一地点，无需公交／地铁方案。</p>}
      {queriedResponse && <p className="mt-2 text-[#56605c]">来源：高德公交／地铁 · 查询时间：<time dateTime={queriedResponse.queried_at}>{new Date(queriedResponse.queried_at).toLocaleString("zh-CN", { hour12: false })}</time></p>}
      {(state?.status === "timeout" || state?.status === "error") && <p role="alert" className="text-[#a63d2d]">{state.message}</p>}
    </div>
  </section>;
}
