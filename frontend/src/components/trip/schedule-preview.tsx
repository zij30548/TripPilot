"use client";

import { useState } from "react";
import type { useSchedulePreview } from "@/lib/use-schedule-preview";
import { scheduleClock, type ScheduleResponse } from "@/types/schedule";
import type { TripRequest } from "@/types/trip";

type ScheduleController = ReturnType<typeof useSchedulePreview>;
type ScheduleItem = ScheduleResponse["days"][number]["items"][number];
type ScheduleEdge = ScheduleResponse["edges"][number];

const inputStyle = "mt-2 block min-h-11 w-full min-w-0 rounded-xl border border-[#c5d4cc] bg-white px-3 py-2 text-sm outline-none focus:border-[#315f51] focus:ring-2 focus:ring-[#315f51]/15";
const itemLabels = { walk: "步行", visit: "停留", wait: "等待", lunch: "午餐预留" };
const edgeLabels = { ok: "已获取", same_place: "同一地点", no_route: "未找到步行路线", timeout: "查询超时", data_error: "路线数据异常", failed: "查询失败" };

function timestamp(value: string) {
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

function EdgeDetails({ edge }: { edge: ScheduleEdge }) {
  return <div className="mt-2 space-y-1 text-xs leading-5 text-[#56605c]">
    {edge.status === "ok" && <>
      <p>高德步行预计 {edge.duration_seconds} 秒 → 排程计入 {edge.duration_minutes} 分钟（向上取整）；距离 {edge.distance_meters} 米。</p>
      <p>来源：高德步行路线 · 查询时间：<time dateTime={edge.queried_at}>{timestamp(edge.queried_at)}</time></p>
    </>}
    {edge.status === "same_place" && <>
      <p>同一地点，无需步行查询；计入 0 分钟。地点停留时间仍按你的规划设置计算。</p>
      <p>来源：同一地点判定（无高德请求） · 判定时间：<time dateTime={edge.queried_at}>{timestamp(edge.queried_at)}</time></p>
    </>}
    {edge.status !== "ok" && edge.status !== "same_place" && <>
      <p>{edgeLabels[edge.status]}：{edge.message ?? "该路段未获得可用路线。"}</p>
      <p>来源：高德步行路线 · 查询时间：<time dateTime={edge.queried_at}>{timestamp(edge.queried_at)}</time></p>
    </>}
  </div>;
}

export default function SchedulePreview({ request, preview }: { request: TripRequest; preview: ScheduleController }) {
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const { stays, lunch, state, validationMessage, canQuery } = preview;
  const required = request.must_visit_places ?? [];
  const response = state.response;
  const activeDay = response?.days.find((day) => day.date === selectedDate) ?? response?.days[0];
  const places = new Map([request.accommodation_place, ...required].filter((place) => place != null).map((place) => [place.id, place]));
  const name = (id: string | null) => id === null ? "" : places.get(id)?.name ?? "已确认地点";
  const edges = new Map(response?.edges.map((edge) => [edge.id, edge]) ?? []);
  const loading = state.status === "loading";

  function itemTitle(item: ScheduleItem) {
    if (item.kind === "walk") return `${name(item.from_place_id)} → ${name(item.to_place_id)}`;
    if (item.kind === "visit") return name(item.place_id);
    return item.kind === "lunch" ? "午餐时间预留（未选择餐厅）" : "等待下一个可用时间段";
  }

  return <section aria-label="必去地点步行草案" className="mt-6 min-w-0 rounded-3xl border-2 border-[#315f51]/30 bg-white p-5 sm:p-8">
    <h2 className="text-xl font-semibold text-[#18392f]">必去地点步行草案</h2>
    <p className="mt-2 text-sm leading-6 text-[#56605c]">按已确认必去地点的原顺序，尝试安排住宿出发、步行、停留和当日返回住宿。仅支持上海 1～3 天、1～6 个必去地点；不保证最优路线，也不使用候选区中的可选地点或旧 Mock 活动。</p>
    <p className="mt-2 text-sm leading-6 text-[#315f51]">旅行日期：{request.start_date} 至 {request.end_date}；每日窗口：{scheduleClock(request.daily_start_time)}—{scheduleClock(request.daily_end_time)}。修改日期或每日窗口请返回修改旅行需求。</p>
    <p className="mt-2 text-xs leading-5 text-[#68726c]">草案规则或停留时间一旦修改，旧草案立即清除，需要再次主动生成。返回修改需求、重新生成或刷新页面后，草案与本区设置清空。</p>

    {required.length > 0 && required.length <= 6 && <fieldset className="mt-5 min-w-0">
      <legend className="text-sm font-semibold text-[#18392f]">每个必去地点的停留时间</legend>
      <p className="mt-1 text-xs leading-5 text-[#68726c]">默认规划设置，可修改；不是高德提供的游玩时长，也未核实营业或预约要求。每项 15～480 分钟。</p>
      <div className="mt-3 grid min-w-0 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {required.map((place) => <label key={place.id} className="block min-w-0 rounded-xl bg-[#f4f8f5] p-3 text-sm">
          <span className="block break-words font-medium">{place.name}</span>
          <span className="mt-1 block break-words text-xs text-[#68726c]">{place.address || "暂未提供地址"}</span>
          <input type="number" inputMode="numeric" min={15} max={480} step={1} aria-label={`停留分钟：${place.name}`}
            className={inputStyle} value={stays[place.id]?.value ?? "60"}
            onChange={(event) => preview.setStayMinutes(place.id, event.target.value)} />
          <span className="mt-2 block text-xs text-[#56605c]">分钟 · {stays[place.id]?.source === "user" ? "你修改的规划设置" : "默认规划设置，可修改"}</span>
        </label>)}
      </div>
    </fieldset>}

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
    <button type="button" disabled={!canQuery || loading} onClick={() => { void preview.query(); }}
      className="mt-4 min-h-11 max-w-full rounded-xl bg-[#18392f] px-5 py-3 text-sm font-semibold text-white focus-visible:outline-2 focus-visible:outline-offset-2 disabled:cursor-not-allowed disabled:opacity-50">
      {loading ? "正在生成步行草案…" : "生成步行草案"}
    </button>
    <div aria-live="polite" className="mt-3 text-sm leading-6 text-[#315f51]">
      {state.status === "idle" && <p>{state.message ?? "尚未生成步行草案。不会自动查询交通。"}</p>}
      {loading && <p role="status">正在校验步行路段并生成草案，请稍候。旧草案不再作为当前结果展示。</p>}
      {state.status === "failed" && <p role="alert">{state.message ?? "草案生成失败，请主动重试。"}</p>}
    </div>

    {response && <div className="mt-6 border-t border-[#315f51]/20 pt-5">
      <p className="text-sm font-semibold text-[#315f51]">{response.status === "complete" ? "已安排本次必去地点" : response.status === "partial" ? "部分必去地点尚未安排" : "本次草案未安排必去地点"}</p>
      <p className="mt-1 text-xs leading-5 text-[#68726c]">生成时间：<time dateTime={response.generated_at}>{timestamp(response.generated_at)}</time>。这是本次规则与步行估算下的草案，不保证未来日期营业、预约或实际可执行。</p>
      <div role="group" aria-label="选择草案日期" className="mt-4 flex flex-wrap gap-2">
        {response.days.map((day, index) => <button key={day.date} type="button" aria-pressed={activeDay?.date === day.date} onClick={() => setSelectedDate(day.date)}
          className={`min-h-11 max-w-full rounded-xl border px-3 py-2 text-sm ${activeDay?.date === day.date ? "border-[#315f51] bg-[#edf3ef] text-[#18392f]" : "border-[#18201d]/15 text-[#56605c]"}`}>草案第 {index + 1} 天 · {day.date}</button>)}
      </div>
      {activeDay && <section aria-label={`${activeDay.date} 步行草案`} className="mt-4 rounded-xl bg-[#f7f8f4] p-4">
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
              {item.kind === "visit" && <p className="mt-1 text-xs text-[#68726c]">停留来源：{item.duration_source === "user" ? "你修改的规划设置" : "默认规划设置，可修改"}；非高德游玩时长。</p>}
              {item.kind === "lunch" && <p className="mt-1 text-xs text-[#68726c]">只占用设定时间，不含餐厅路线、费用或营业保证。</p>}
              {edge && <EdgeDetails edge={edge} />}
            </li>;
          })}</ol>}
        <p className="mt-4 text-sm font-medium text-[#315f51]">{activeDay.return_time ? `预计返回住宿参考点：${activeDay.return_time}` : "本日尚无返回时刻（没有已安排的外出）。"}</p>
      </section>}
      {response.unscheduled.length > 0 && <section aria-label="未安排的必去地点" className="mt-5 rounded-xl border border-amber-200 bg-amber-50 p-4">
        <h3 className="text-sm font-semibold text-[#835718]">未安排的必去地点</h3>
        <p className="mt-1 text-xs leading-5 text-[#835718]">仅说明本次固定顺序和规则下未排入，不代表换顺序、调整规则或其他交通方式后也不可行。</p>
        <ul className="mt-3 space-y-3">{response.unscheduled.map((item) => <li key={item.place_id} className="text-sm leading-6">
          <p className="break-words font-medium">{name(item.place_id)}</p><p>{item.message}</p>
        </li>)}</ul>
      </section>}
      <details className="mt-5 rounded-xl border border-[#18201d]/10 p-4 text-sm leading-6">
        <summary className="cursor-pointer font-semibold text-[#315f51]">步行路段与采用状态（{response.edges.length} 条）</summary>
        <ul className="mt-3 space-y-3">{response.edges.map((edge) => <li key={edge.id} className="rounded-lg bg-[#f4f8f5] p-3">
          <p className="break-words text-sm font-medium">{name(edge.origin.place_id)} → {name(edge.destination.place_id)} · {edge.used ? "已用于本次草案" : "未采用的查询记录"}</p>
          <EdgeDetails edge={edge} />
        </li>)}</ul>
      </details>
      <section aria-label="草案规则与未知信息" className="mt-5 rounded-xl bg-[#edf3ef] p-4 text-xs leading-6 text-[#56605c]">
        <h3 className="font-semibold text-[#18392f]">本次规则与尚未核实的信息</h3>
        <ul className="mt-2 list-disc space-y-1 pl-4">{response.rules.map((rule, index) => <li key={`rule-${index}`}>{rule}</li>)}{response.unknowns.map((unknown, index) => <li key={`unknown-${index}`}>{unknown}</li>)}</ul>
      </section>
    </div>}
    <p className="mt-5 rounded-xl bg-amber-50 p-3 text-xs leading-6 text-[#835718]">营业时间、预约要求、门票、餐费及其他真实费用均未知，预算尚未核实。午餐未选择餐厅，绕行尚未计入。下方旧 Mock 的预算、天气、活动与交通示例不适用于这份草案。</p>
  </section>;
}
