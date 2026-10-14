"use client";
import { formatCents, type Money, type ScheduleCosts } from "@/lib/schedule-costs";
import type { useItineraryQuery } from "@/lib/use-itinerary-query";
import type { LocalQueryResult } from "@/lib/itinerary-query";

const money = (v: Money) => v.status === "known" ? formatCents(v.cents) : v.status === "unknown" ? "未知" : `无效：${v.message}`;
export default function ItineraryQuery({ assistant, dates }: { assistant: ReturnType<typeof useItineraryQuery>; dates: string[] }) {
  const { state } = assistant;
  return <section aria-label="只读行程查询" className="mt-6 min-w-0 rounded-2xl border border-[#315f51]/25 bg-white p-5 text-sm leading-6 sm:p-8">
    <h2 className="text-xl font-semibold">只读行程查询</h2>
    <p className="mt-2">查看一天的安排或全程费用。AI 仅选择查询工具，结果由当前正式草案和费用核对提供；不会修改行程，也不查询天气或知识库。</p>
    {!assistant.available ? <p role="status">请先生成有效的正式草案。</p> : <p>可查询：{dates.map((d, i) => `第 ${i + 1} 天 ${d}`).join("；")}。</p>}
    <form className="mt-3 space-y-3" onSubmit={e => { e.preventDefault(); void assistant.query(); }}>
      <label className="block">查询要求<input aria-label="只读查询要求" maxLength={400} value={assistant.question} onChange={e => assistant.editQuestion(e.target.value)} placeholder="例如：看看第二天怎么安排" className="mt-1 block min-h-11 w-full rounded-xl border px-3 py-2" /></label>
      <div className="flex flex-wrap gap-3"><button type="submit" disabled={!assistant.available || state.status === "loading"} className="min-h-11 rounded-full bg-[#315f51] px-5 py-2 text-white disabled:opacity-50">{state.status === "loading" ? "正在查询…" : "查询当前行程"}</button>
        {state.status === "loading" && <button type="button" onClick={assistant.cancel} className="min-h-11 rounded-full border px-5">取消查询</button>}</div>
    </form>
    {state.message && <p role={state.status === "failed" ? "alert" : "status"} className="mt-3">{state.message}</p>}
    {state.result && <div aria-label="只读查询结果" className="mt-4 min-w-0 break-words rounded-xl bg-[#f4f8f5] p-4">
      <p className="mb-3 font-medium">来源：本次绑定的当前正式草案及费用输入 · 只读结果</p>
      <QueryResult result={state.result} />
    </div>}
  </section>;
}
function QueryResult({ result }: { result: LocalQueryResult }) {
  if (result.kind === "clarification") return <p>{result.message}</p>;
  if (result.kind === "budget") return <BudgetResult costs={result.costs} />;
  const schedule = result.base.original, places = [schedule.request.accommodation_place, ...schedule.request.must_visit_places, ...schedule.request.optional_places ?? []];
  const name = (id: string | null) => places.find(p => p.id === id)?.name ?? "未知地点";
  return <><h3 className="font-semibold">{result.day.date} 的安排（上海时间）</h3><p>草案生成：{schedule.generated_at}</p>
    {!result.day.items.length && <p>尚未安排景点；不以其他日期或 Mock 活动填充。</p>}
    <ol className="my-3 space-y-2">{result.day.items.map((item, i) => <li key={i} className="rounded-lg bg-white p-3">
      <p>{item.start_time}—{item.end_time} · {item.duration_minutes} 分钟</p>
      <p>{item.kind === "visit" ? `${name(item.place_id)} · ${schedule.request.must_visit_places.some(p => p.id === item.place_id) ? "必去" : "可选"} · 停留${item.duration_source === "default" ? "默认规划设置" : "用户设置"}`
        : item.kind === "lunch" ? "午餐预留（未指定餐厅）" : item.kind === "wait" ? "等待午餐窗口" : `${item.kind === "walk" ? "步行" : "公交／地铁参考"}：${name(item.from_place_id)} → ${name(item.to_place_id)}`}</p>
    </li>)}</ol>
    <p>预计返回住宿：{result.day.return_time ?? "未安排返程"} · {schedule.request.accommodation_place.name}</p>
    {schedule.unscheduled.length > 0 && <p>全程未安排必去：{schedule.unscheduled.map(p => `${name(p.place_id)}：${p.message}`).join("；")}</p>}
    <p>交通为查询时的参考估时，未核验旅行当天运营、营业或预约；不代表最优路线。</p>
  </>;
}
function BudgetResult({ costs }: { costs: ScheduleCosts }) {
  if (costs.status === "unavailable") return <p>{costs.message}</p>;
  return <><h3 className="font-semibold">全程费用核对 · 人民币 · {costs.travelers} 位旅客</h3>
    <p>{costs.dates[0]}—{costs.dates.at(-1)} · 草案生成：{costs.generatedAt}</p><p>全程预算：{money(costs.budget)}</p>
    {costs.errors.length ? <div role="alert"><p>存在非法金额，无法核算小计和预算差额，不沿用旧值。</p>{costs.errors.map((e, i) => <p key={i}>{e}</p>)}</div> : <>
      <p>交通参考已知小计：{formatCents(costs.transportCents!)} · 用户估算已填小计：{formatCents(costs.userCents!)}</p>
      <p>当前可计算小计：{formatCents(costs.subtotalCents!)}</p><p>预算减当前小计：{formatCents(costs.differenceCents!)}{costs.missing.length ? "（尚有未知费用，不是可自由支配余额）" : "（按参考票价与用户估算计算，不保证实际支出）"}</p>
      {costs.differenceCents! < 0 && <p>已计入部分已超过预算。</p>}
    </>}
    {costs.unarrangedRequired > 0 && <p>仍有 {costs.unarrangedRequired} 个必去未安排，只核对当前已安排部分及全程估算，不能说明全部需求满足预算。</p>}
    <h4 className="mt-3 font-semibold">用户估算（全部旅客合计）</h4><ul>{costs.user.map(r => <li key={r.key}>{r.label}：{money(r.money)}{r.money.status === "known" && r.money.cents === 0 ? "（用户填零，不代表核实免费）" : ""}</li>)}</ul>
    <details className="mt-3"><summary>实际交通明细</summary><p>按时间轴每次移动计费；公交暂按每位旅客均付整条参考票价，不叠加接驳，不核实优惠。</p>{costs.transport.map(r => <p key={r.key}>{r.date} {r.start} {r.from} → {r.to}：{money(r.fare)} × {costs.travelers} 人 = {money(r.money)} · 来源 {r.source} · 查询 {r.queriedAt}</p>)}</details>
    {costs.missing.length > 0 && <><h4 className="mt-3 font-semibold">尚未核算的费用</h4><ul>{costs.missing.map((m, i) => <li key={i}>{m}：未知</li>)}</ul></>}
    <p className="mt-3">步行交通票费计零不代表景点免费。费用核对不改变排程。</p>
  </>;
}
