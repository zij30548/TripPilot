"use client";

import { useState } from "react";
import type { useSchedulePreview } from "@/lib/use-schedule-preview";
import { scheduleClock, type ScheduleResponse } from "@/types/schedule";
import type { Place } from "@/types/place";
import type { TripRequest } from "@/types/trip";

type ScheduleController = ReturnType<typeof useSchedulePreview>;
type ScheduleItem = ScheduleResponse["days"][number]["items"][number];
type ScheduleEdge = ScheduleResponse["edges"][number];

const inputStyle = "mt-2 block min-h-11 w-full min-w-0 rounded-xl border border-[#c5d4cc] bg-white px-3 py-2 text-sm outline-none focus:border-[#315f51] focus:ring-2 focus:ring-[#315f51]/15";
const itemLabels = { walk: "步行", transit: "公交／地铁参考交通", visit: "停留", wait: "等待", lunch: "午餐预留" };
const edgeLabels = { ok: "已获取", same_place: "同一地点", no_route: "未找到路线", unsupported: "没有可展示的受支持公交方案", timeout: "查询超时", data_error: "路线数据异常", failed: "查询失败", budget_exhausted: "本次查询截止前未开始" };
const attemptLabels = { scheduled: "已安排", time_window: "本日时间窗口未容纳", no_route: "未找到路线", unsupported: "没有可展示的受支持公交方案", timeout: "路线查询超时", data_error: "路线数据异常", failed: "路线查询失败", budget_exhausted: "本次查询截止，未完成试排", day_slot_used: "本日可选名额已使用" };
const transitNotice = "采用本次查询的参考方案估时，尚未验证旅行日期及草案出发时刻的运营班次、等车和换乘可行性。";

function timestamp(value: string) {
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

function EdgeDetails({ edge }: { edge: ScheduleEdge }) {
  const transit = edge.transport_mode === "transit";
  const route = edge.transit_route;
  return <div className="mt-2 space-y-1 text-xs leading-5 text-[#56605c]">
    {edge.status === "ok" && !transit && <>
      <p>高德步行预计 {edge.duration_seconds} 秒 → 排程计入 {edge.duration_minutes} 分钟（向上取整）；距离 {edge.distance_meters} 米。</p>
      <p>来源：高德步行路线 · 查询时间：<time dateTime={edge.queried_at}>{timestamp(edge.queried_at)}</time></p>
    </>}
    {edge.status === "ok" && transit && route && <>
      <p>公交方案总预计 {edge.duration_seconds} 秒 → 排程计入 {edge.duration_minutes} 分钟（向上取整）。已包含方案内接驳步行、乘车与换乘估时，不再累加分段时长。</p>
      <p>接驳步行距离：{route.walking_distance_meters} 米（不是公交总里程）。人民币参考票价：{route.fare_cny === null ? "未知" : `¥${route.fare_cny.toFixed(2)}`}；不乘旅行人数，不计入预算。</p>
      <p>来源：高德公交参考方案 · 查询时间：<time dateTime={edge.queried_at}>{timestamp(edge.queried_at)}</time></p>
      <p>{transitNotice}</p>
      <details className="mt-2 min-w-0 rounded-lg border border-[#315f51]/20 p-3">
        <summary className="cursor-pointer font-medium text-[#315f51]">查看线路与站点（{route.legs.length} 步）</summary>
        <p className="mt-2">按返回顺序采用首条完整、有效且受支持的方案；同段备选线路只采用首条有效且受支持线路，不代表最快或最优。</p>
        <p className="mt-1">以下是方案步骤，不是精确发车、到站或换乘时刻。整段交通按总估时作为一个时间块安排。</p>
        <ol className="mt-2 list-decimal space-y-2 pl-4">{route.legs.map((leg, index) => <li key={index} className="break-words">
          <p className="font-medium">{leg.mode === "walking" ? "接驳步行" : `${leg.mode === "subway" ? "地铁" : "公交"} · ${leg.line_name}`}</p>
          {leg.mode !== "walking" && <p>上车：{leg.departure_stop} → 下车：{leg.arrival_stop}</p>}
          {leg.instruction && <p>{leg.instruction}</p>}
          <p>该步距离：{leg.distance_meters === null ? "未知" : `${leg.distance_meters} 米`}；该步预计耗时：{leg.duration_seconds === null ? "未知" : `${leg.duration_seconds} 秒`}（仅作说明，不重复计时）。</p>
        </li>)}</ol>
        {!route.geometry_complete && <p className="mt-2">部分地图几何未提供；不影响已通过校验的文字参考方案，本区不绘制草案路线地图。</p>}
      </details>
    </>}
    {edge.status === "same_place" && <>
      <p>同一地点，无需交通查询；计入 0 分钟。地点停留时间仍按你的规划设置计算，不生成线路或票价。</p>
      <p>来源：同一地点判定（无高德请求） · 判定时间：<time dateTime={edge.queried_at}>{timestamp(edge.queried_at)}</time></p>
    </>}
    {edge.status !== "ok" && edge.status !== "same_place" && <>
      <p>{edgeLabels[edge.status]}：{edge.message ?? "该路段未获得可用路线。"}</p>
      <p>{edge.status === "budget_exhausted" ? "未发起高德查询、未取得估时 · 记录时间：" : `来源：高德${transit ? "公交参考方案" : "步行路线"} · 查询时间：`}<time dateTime={edge.queried_at}>{timestamp(edge.queried_at)}</time></p>
    </>}
  </div>;
}

function StaySettings({ places, optional = false, preview }: { places: Place[]; optional?: boolean; preview: ScheduleController }) {
  if (!places.length) return null;
  return <fieldset className="mt-5 min-w-0">
    <legend className="text-sm font-semibold text-[#18392f]">{optional ? "本次试排可选地点的停留时间" : "每个必去地点的停留时间"}</legend>
    <p className="mt-1 text-xs leading-5 text-[#68726c]">默认规划设置，可修改；不是高德提供的游玩时长，也未核实营业或预约要求。每项 15～480 分钟。</p>
    <div className="mt-3 grid min-w-0 gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {places.map((place) => <label key={place.id} className="block min-w-0 rounded-xl bg-[#f4f8f5] p-3 text-sm">
        <span className="block break-words font-medium">{optional ? "可选 · " : "必去 · "}{place.name}</span>
        <span className="mt-1 block break-words text-xs text-[#68726c]">{place.address || "暂未提供地址"}</span>
        <input type="number" inputMode="numeric" min={15} max={480} step={1} aria-label={`${optional ? "可选" : ""}停留分钟：${place.name}`}
          className={inputStyle} value={preview.stays[place.id]?.value ?? "60"}
          onChange={(event) => preview.setStayMinutes(place.id, event.target.value)} />
        <span className="mt-2 block text-xs text-[#56605c]">分钟 · {preview.stays[place.id]?.source === "user" ? "你修改的规划设置" : "默认规划设置，可修改"}</span>
      </label>)}
    </div>
  </fieldset>;
}

export function ScheduleResult({ response }: { response: ScheduleResponse }) {
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const request = response.request, required = request.must_visit_places, optionalPlaces = request.optional_places ?? [], transportMode = request.transport_mode;
  const activeDay = response?.days.find((day) => day.date === selectedDate) ?? response?.days[0];
  const places = new Map([request.accommodation_place, ...required, ...optionalPlaces].filter((place) => place != null).map((place) => [place.id, place]));
  const name = (id: string | null) => id === null ? "" : places.get(id)?.name ?? "已确认地点";
  const edges = new Map(response?.edges.map((edge) => [edge.id, edge]) ?? []);
  const optionalIds = new Set(optionalPlaces.map((place) => place.id));
  const visitedIds = new Set(response?.days.flatMap((day) => day.items.filter((item) => item.kind === "visit").map((item) => item.place_id)) ?? []);
  const requiredCount = required.filter((place) => visitedIds.has(place.id)).length;
  const optionalCount = optionalPlaces.filter((place) => visitedIds.has(place.id)).length;

  function itemTitle(item: ScheduleItem) {
    if (item.kind === "walk" || item.kind === "transit") return `${name(item.from_place_id)} → ${name(item.to_place_id)}`;
    if (item.kind === "visit") return name(item.place_id);
    return item.kind === "lunch" ? "午餐时间预留（未选择餐厅）" : "等待下一个可用时间段";
  }

  return <div className="mt-6 border-t border-[#315f51]/20 pt-5">
      <p className="text-sm font-semibold text-[#315f51]">{required.length > 0 ? requiredCount === required.length ? "已安排本次必去地点" : requiredCount > 0 ? "部分必去地点尚未安排" : "本次草案未安排必去地点" : optionalCount > 0 ? "本次草案已安排可选地点" : "本次草案尚未安排可选地点"}</p>
      <p className="mt-1 text-sm text-[#315f51]">必去已安排 {requiredCount}/{required.length} 个 · 可选已安排 {optionalCount}/{optionalPlaces.length} 个。可选未排入不影响已验证的必去安排。</p>
      <p className="mt-1 text-xs leading-5 text-[#68726c]">生成时间：<time dateTime={response.generated_at}>{timestamp(response.generated_at)}</time>。这是本次规则与{transportMode === "walking" ? "步行" : "公交／地铁参考方案"}估算下的草案，不保证未来日期营业、预约或实际可执行。同一请求、同一方式下，可选补充不改变必去时间；切换方式重新生成后，必去时刻可能变化。</p>
      <div role="group" aria-label="选择草案日期" className="mt-4 flex flex-wrap gap-2">
        {response.days.map((day, index) => <button key={day.date} type="button" aria-pressed={activeDay?.date === day.date} onClick={() => setSelectedDate(day.date)}
          className={`min-h-11 max-w-full rounded-xl border px-3 py-2 text-sm ${activeDay?.date === day.date ? "border-[#315f51] bg-[#edf3ef] text-[#18392f]" : "border-[#18201d]/15 text-[#56605c]"}`}>草案第 {index + 1} 天 · {day.date}</button>)}
      </div>
      {activeDay && <section aria-label={`${activeDay.date} 行程草案`} className="mt-4 rounded-xl bg-[#f7f8f4] p-4">
        <h3 className="font-semibold text-[#18392f]">{activeDay.date}</h3>
        {activeDay.items.length === 0 ? <p className="mt-3 text-sm text-[#68726c]">尚未安排景点；没有为这天空造交通或午餐。</p>
          : <ol aria-label="草案时间线" className="mt-4 space-y-3">{activeDay.items.map((item, index) => {
            const edge = item.edge_id ? edges.get(item.edge_id) : undefined;
            return <li key={`${index}-${item.kind}`} className="rounded-xl border border-[#18201d]/10 bg-white p-3">
              <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
                <span className="font-semibold text-[#315f51]">{item.start_time}—{item.end_time} · {itemLabels[item.kind]}</span>
                <span className="text-xs text-[#68726c]">{item.duration_minutes} 分钟</span>
              </div>
              <p className="mt-2 break-words text-sm font-medium">{itemTitle(item)}</p>
              {item.kind === "visit" && <><span className={`mt-2 inline-block rounded-full px-2 py-1 text-xs font-semibold ${optionalIds.has(item.place_id ?? "") ? "bg-amber-50 text-[#835718]" : "bg-[#edf3ef] text-[#315f51]"}`}>{optionalIds.has(item.place_id ?? "") ? "可选地点" : "必去地点"}</span><p className="mt-1 text-xs text-[#68726c]">停留来源：{item.duration_source === "user" ? "你修改的规划设置" : "默认规划设置，可修改"}；非高德游玩时长。</p></>}
              {item.kind === "lunch" && <p className="mt-1 text-xs text-[#68726c]">只占用设定时间，不含餐厅路线、费用或营业保证。</p>}
              {edge && <EdgeDetails edge={edge} />}
            </li>;
          })}</ol>}
        <p className="mt-4 text-sm font-medium text-[#315f51]">{activeDay.return_time ? `预计返回住宿参考点：${activeDay.return_time}` : "本日尚无返回时刻（没有已安排的外出）。"}</p>
      </section>}
      {response.optional_results.length > 0 && <section aria-label="可选地点试排结果" className="mt-5 min-w-0 rounded-xl border border-[#315f51]/20 p-4">
        <h3 className="text-sm font-semibold text-[#18392f]">可选地点试排结果</h3>
        <p className="mt-1 text-xs leading-5 text-[#56605c]">结果逐日记录。某天未安排不代表其他日期也不可行；未尝试的日期不作可行性判断。每个日期最多加入 1 个可选地点，不挪动必去停留。</p>
        <ul className="mt-3 space-y-3">{response.optional_results.map((result) => <li key={result.place_id} className="min-w-0 rounded-lg bg-[#f4f8f5] p-3 text-sm leading-6">
          <p className="break-words font-medium">{name(result.place_id)} · {result.scheduled_date ? `已安排于 ${result.scheduled_date}` : "本次未安排"}</p>
          {result.not_attempted_reason === "must_incomplete" && <p className="text-xs text-[#835718]">必去地点尚未全部安排，本次未尝试此可选地点；不代表该地点不可行。</p>}
          <ul className="mt-1 space-y-1">{result.attempts.map((attempt) => <li key={attempt.date} className="break-words text-xs text-[#56605c]"><time dateTime={attempt.date}>{attempt.date}</time>：{attemptLabels[attempt.outcome]}。{attempt.message}</li>)}</ul>
        </li>)}</ul>
      </section>}
      {response.unscheduled.length > 0 && <section aria-label="未安排的必去地点" className="mt-5 rounded-xl border border-amber-200 bg-amber-50 p-4">
        <h3 className="text-sm font-semibold text-[#835718]">未安排的必去地点</h3>
        <p className="mt-1 text-xs leading-5 text-[#835718]">仅说明本次固定顺序和规则下未排入，不代表换顺序、调整规则或其他交通方式后也不可行。</p>
        <ul className="mt-3 space-y-3">{response.unscheduled.map((item) => <li key={item.place_id} className="text-sm leading-6">
          <p className="break-words font-medium">{name(item.place_id)}</p><p>{item.message}</p>
        </li>)}</ul>
      </section>}
      <details className="mt-5 rounded-xl border border-[#18201d]/10 p-4 text-sm leading-6">
        <summary className="cursor-pointer font-semibold text-[#315f51]">{transportMode === "walking" ? "步行" : "公交参考"}路段与采用状态（{response.edges.length} 条）</summary>
        <ul className="mt-3 space-y-3">{response.edges.map((edge) => <li key={edge.id} className="rounded-lg bg-[#f4f8f5] p-3">
          <p className="break-words text-sm font-medium">{name(edge.origin.place_id)} → {name(edge.destination.place_id)} · {edge.used ? "已用于本次草案" : "未采用的查询记录"}</p>
          <EdgeDetails edge={edge} />
        </li>)}</ul>
      </details>
      <section aria-label="草案规则与未知信息" className="mt-5 rounded-xl bg-[#edf3ef] p-4 text-xs leading-6 text-[#56605c]">
        <h3 className="font-semibold text-[#18392f]">本次规则与尚未核实的信息</h3>
        <ul className="mt-2 list-disc space-y-1 pl-4">{response.rules.map((rule, index) => <li key={`rule-${index}`}>{rule}</li>)}{response.unknowns.map((unknown, index) => <li key={`unknown-${index}`}>{unknown}</li>)}</ul>
      </section>
    </div>;
}

export default function SchedulePreview({ request, preview }: { request: TripRequest; preview: ScheduleController }) {
  const { lunch, state, validationMessage, canQuery, optionalPlaces, candidateLoading, transportMode } = preview;
  const required = request.must_visit_places ?? [];
  const response = state.response;
  const loading = state.status === "loading";
  return <section aria-label="行程草案" className="mt-6 min-w-0 rounded-3xl border-2 border-[#315f51]/30 bg-white p-5 sm:p-8">
    <h2 className="text-xl font-semibold text-[#18392f]">行程草案 · {transportMode === "walking" ? "步行" : "公交／地铁参考"}</h2>
    <p className="mt-2 text-sm leading-6 text-[#56605c]">先按原顺序安排全部必去地点，再尝试在每日尾部加入最多 1 个可选地点，并校验当日返回住宿。支持上海 1～3 天、最多 6 个必去地点和本次前 3 个有效可选地点；至少需要 1 个必去或可选地点。每日 1 个可选名额是本轮产品规则，不代表最优安排。</p>
    <p className="mt-2 text-sm leading-6 text-[#315f51]">旅行日期：{request.start_date} 至 {request.end_date}；每日窗口：{scheduleClock(request.daily_start_time)}—{scheduleClock(request.daily_end_time)}。修改日期或每日窗口请返回修改旅行需求。</p>
    <p className="mt-2 text-xs leading-5 text-[#68726c]">修改交通方式、规则、停留时间，或获取、排除、恢复候选都会立即清除旧草案，但不会自动生成。返回修改需求、重新生成或刷新页面后，草案与本区设置清空，交通方式恢复步行；不改写旧 Mock 活动、绑定或地图路线。</p>
    <fieldset className="mt-5 min-w-0 rounded-xl border border-[#315f51]/20 p-4">
      <legend className="px-1 text-sm font-semibold text-[#18392f]">草案交通方式</legend>
      <div className="flex flex-wrap gap-2">
        {([['walking', '步行'], ['transit', '公交／地铁']] as const).map(([mode, label]) => <button type="button" key={mode} aria-pressed={transportMode === mode}
          onClick={() => preview.setTransportMode(mode)} className={`min-h-11 rounded-xl border px-4 py-2 text-sm ${transportMode === mode ? "border-[#315f51] bg-[#edf3ef] text-[#18392f]" : "border-[#18201d]/15 text-[#56605c]"}`}>{label}</button>)}
      </div>
      <p className="mt-2 text-xs leading-5 text-[#68726c]">整份草案仅使用所选方式；切换只清除旧草案，点击生成才查询。不自动比较或改用其他方式，不影响下方旧地图的交通方式。</p>
      {transportMode === "transit" && <p className="mt-2 text-xs leading-5 text-[#835718]">{transitNotice}</p>}
    </fieldset>

    <section aria-label="本次可选试排范围" className="mt-5 min-w-0 rounded-xl border border-[#315f51]/20 bg-[#f4f8f5] p-4 text-sm leading-6">
      <h3 className="font-semibold text-[#18392f]">本次输入：必去 {required.length} 个 · 试排可选 {optionalPlaces.length} 个</h3>
      <p className="mt-1 text-xs text-[#56605c]">按候选清单原顺序，取未排除且有效的前 3 个。未纳入本轮试排：{preview.notSelectedCount} 个；这是范围限制，不代表这些地点放不下。</p>
      {optionalPlaces.length > 0 ? <ol aria-label="本次试排可选地点" className="mt-2 list-decimal space-y-1 pl-5">{optionalPlaces.map((place) => <li key={place.id} className="break-words">{place.name}<span className="block text-xs text-[#68726c]">{place.address || "暂未提供地址"}</span></li>)}</ol>
        : <p className="mt-2 text-xs text-[#68726c]">目前没有有效的未排除可选地点。有必去地点时仍可只生成必去草案。</p>}
      {preview.candidateQueriedAt && <p className="mt-2 text-xs text-[#68726c]">采用的候选检索时间：<time dateTime={preview.candidateQueriedAt}>{timestamp(preview.candidateQueriedAt)}</time></p>}
      {preview.candidateStatus === "partial" && <p className="mt-2 text-xs text-[#835718]">候选检索仅部分成功，本轮只使用成功取得的有效候选，不代表完整候选范围。</p>}
      {preview.showingPreviousCandidates && !candidateLoading && <p className="mt-2 text-xs text-[#835718]">最近检索失败，保留的是上次有效候选及排除决定，不是本次新结果。可以主动基于这份旧清单生成草案。</p>}
      {candidateLoading && <p role="status" className="mt-2 text-sm text-[#835718]">候选正在更新，请等待检索结束后再生成；旧草案已经失效。</p>}
    </section>
    {required.length <= 6 && <StaySettings places={required} preview={preview} />}
    <StaySettings places={optionalPlaces} optional preview={preview} />

    <fieldset className="mt-5 min-w-0 rounded-xl border border-[#18201d]/10 p-4">
      <legend className="px-1 text-sm font-semibold text-[#18392f]">午餐时间设置</legend>
      <label className="flex min-h-10 items-center gap-2 text-sm text-[#315f51]">
        <input type="checkbox" checked={lunch.enabled} onChange={(event) => preview.setLunchEnabled(event.target.checked)} />安排午餐时间
      </label>
      <div className="mt-2 grid min-w-0 gap-3 sm:grid-cols-2">
        <label className="min-w-0 text-sm">午餐开始时间
          <input type="time" className={inputStyle} value={lunch.start} disabled={!lunch.enabled}
            onChange={(event) => preview.setLunchStart(event.target.value)} />
        </label>
        <label className="min-w-0 text-sm">午餐结束时间
          <input type="time" className={inputStyle} value={lunch.end} disabled={!lunch.enabled}
            onChange={(event) => preview.setLunchEnd(event.target.value)} />
        </label>
      </div>
      <p className="mt-2 text-xs leading-5 text-[#68726c]">默认 12:00—13:00，可调整或关闭。启用时必须完整位于每日窗口内；仅预留用餐时间，未计餐厅绕行与餐费，不代表餐厅预约。</p>
    </fieldset>

    {validationMessage && <p role="alert" className="mt-4 rounded-xl bg-amber-50 p-3 text-sm leading-6 text-[#835718]">{validationMessage}</p>}
    <p aria-label="本次生成范围" className="mt-4 break-words text-xs leading-6 text-[#56605c]">本次生成：必去 {required.length} 个；试排可选 {optionalPlaces.length} 个{optionalPlaces.length > 0 ? `（${optionalPlaces.map((place) => place.name).join("、")}）` : ""}；另 {preview.notSelectedCount} 个未纳入本轮范围。</p>
    <button type="button" disabled={!canQuery || loading || candidateLoading} onClick={() => { void preview.query(); }}
      className="mt-4 min-h-11 max-w-full rounded-xl bg-[#18392f] px-5 py-3 text-sm font-semibold text-white focus-visible:outline-2 focus-visible:outline-offset-2 disabled:cursor-not-allowed disabled:opacity-50">
      {loading ? "正在生成行程草案…" : "生成行程草案"}
    </button>
    <div aria-live="polite" className="mt-3 text-sm leading-6 text-[#315f51]">
      {state.status === "idle" && <p>{state.message ?? "尚未生成行程草案。不会自动查询交通。"}</p>}
      {loading && <p role="status">正在查询{transportMode === "walking" ? "步行路线" : "公交／地铁参考方案"}并生成草案，请稍候。旧草案不再作为当前结果展示。</p>}
      {state.status === "failed" && <p role="alert">{state.message ?? "草案生成失败，请主动重试。"}</p>}
    </div>

    {response && <ScheduleResult response={response} />}
    <p className="mt-5 rounded-xl bg-amber-50 p-3 text-xs leading-6 text-[#835718]">营业时间、预约要求、门票、餐费及其他真实费用均未知，预算尚未核实。午餐未选择餐厅，绕行尚未计入。下方旧 Mock 的预算、天气、活动与交通示例不适用于这份草案。</p>
  </section>;
}
