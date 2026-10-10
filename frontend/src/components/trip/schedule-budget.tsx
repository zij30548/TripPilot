"use client";

import { useId, useState } from "react";
import { calculateScheduleCosts, emptyCostInputs, formatCents, type Money, type UserCost } from "@/lib/schedule-costs";
import type { ScheduleResponse } from "@/types/schedule";
import type { TripRequest } from "@/types/trip";

const timestamp = (value: string) => new Date(value).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false });
const money = (value: Money) => value.status === "known" ? formatCents(value.cents) : value.status === "unknown" ? "未知" : "金额无效";

// This component owns only user text, never a second schedule snapshot. It stays
// mounted while the official draft is invalidated; leaving the result unmounts it.
export default function ScheduleBudget({ response, request }: { response: ScheduleResponse | null; request: TripRequest }) {
  const [inputs, setInputs] = useState(emptyCostInputs);
  const prefix = useId();
  const costs = calculateScheduleCosts(response, request, inputs);
  const edit = (row: UserCost, value: string) => setInputs((current) => row.category === "ticket" ? { ...current, tickets: { ...current.tickets, [row.placeId!]: value } }
    : row.category === "meal" ? { ...current, meals: { ...current.meals, [row.date!]: value } } : { ...current, [row.category]: value });
  return <section aria-label="正式草案费用与预算" className="mt-6 min-w-0 rounded-3xl border border-[#315f51]/25 bg-[#f4f8f5] p-5 sm:p-8">
    <h2 className="text-xl font-semibold text-[#18392f]">正式草案费用与预算</h2>
    <p className="mt-2 text-sm leading-6 text-[#56605c]">人民币 · 全部旅客 · 全程总预算。仅核对当前正式草案，不使用下方 Mock 费用；本地核算不会改变排程、候选或调整预览。</p>
    {costs.status === "unavailable" ? <p role="status" className="mt-4 text-sm text-[#68726c]">{costs.message}</p> : <>
      <p className="mt-3 text-sm">草案生成时间：<time dateTime={costs.generatedAt}>{timestamp(costs.generatedAt)}</time> · {response!.request.transport_mode === "walking" ? "步行" : "公交／地铁参考"}</p>
      <p className="mt-1 text-sm">核算范围：{costs.dates[0]}—{costs.dates.at(-1)}，{costs.travelers} 位旅客；当前已安排地点、实际交通，以及全程餐饮、住宿与其他用户估算。</p>
      {costs.unarrangedRequired > 0 && <p className="mt-3 rounded-xl bg-amber-50 p-3 text-sm text-[#835718]">仍有 {costs.unarrangedRequired} 个必去地点未安排。这里只核对当前已安排部分及你填写的全程费用，不能说明整个需求已经满足预算。</p>}
      <section aria-label="交通费用明细" className="mt-5">
        <h3 className="font-semibold text-[#18392f]">交通参考费用</h3>
        <p className="mt-2 text-xs leading-6 text-[#56605c]">按时间轴每次实际移动计费，包含住宿往返；同一路段发生两次就计两次。公交每次只计整条方案票价，不再叠加接驳或换乘分段。暂按每位旅客均按该参考票价计费估算，未核实儿童优惠、通票或旅行当天实际价格。未知公交票价不能手动覆盖。</p>
        <ul className="mt-3 space-y-2">{costs.transport.map((row) => <li key={row.key} className="min-w-0 break-words rounded-xl bg-white p-3 text-sm leading-6">
          <p className="font-medium">{row.date} {row.start}—{row.end} · {row.from} → {row.to}</p>
          <p>{row.samePlace ? "同地点移动" : row.kind === "walk" ? "步行" : "公交／地铁整条方案"}：{row.kind === "transit" && !row.samePlace ? `每人参考票价 ${money(row.fare)} × ${costs.travelers} 人` : "交通票费按规则计 0"} · 本次计入 {money(row.money)}</p>
          {row.money.status === "invalid" && <p role="alert" className="text-[#9b3d30]">{row.money.message}</p>}
          <p className="text-xs text-[#68726c]">{row.source === "same_place" ? "来源：同地点规则（无需移动）" : `来源：高德${row.kind === "walk" ? "步行路线；票费为本地零费用规则" : "公交参考方案票价"}`} · 查询／判定时间：<time dateTime={row.queriedAt}>{timestamp(row.queriedAt)}</time> · 路段 {row.edgeId}</p>
        </li>)}</ul>
      </section>
      <section aria-label="用户费用估算" className="mt-5">
        <h3 className="font-semibold text-[#18392f]">你的费用估算</h3>
        <p className="mt-2 text-xs leading-6 text-[#56605c]">以下均为用户估算，单位：元，全部旅客合计，不再乘人数。空白表示未知，明确填 0 才计零，不代表已核实免费。同 ID 的原估算在本次结果内保留，不是新日期报价；新地点默认未知，移出当前草案的地点不计费。返回修改需求或刷新页面后清空。</p>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">{costs.user.map((row) => {
          const id = `${prefix}-${row.key}`;
          return <div key={row.key} className="min-w-0 rounded-xl bg-white p-4">
            <label htmlFor={id} className="block break-words text-sm font-medium">{row.label}<span className="mt-1 block text-xs font-normal text-[#68726c]">用户估算 · 全部旅客合计（元）</span></label>
            <p id={`${id}-detail`} className="mt-1 break-words text-xs leading-5 text-[#68726c]">{row.detail}</p>
            <input id={id} type="text" inputMode="decimal" autoComplete="off" value={row.value} placeholder="留空表示未知" aria-invalid={row.money.status === "invalid"} aria-describedby={`${id}-detail ${id}-status`}
              onChange={(event) => edit(row, event.target.value)} className="mt-2 min-h-11 w-full rounded-lg border border-[#315f51]/30 px-3 py-2 text-sm focus-visible:outline-2 focus-visible:outline-[#315f51]" />
            <p id={`${id}-status`} role={row.money.status === "invalid" ? "alert" : undefined} className={`mt-1 text-xs leading-5 ${row.money.status === "invalid" ? "text-[#9b3d30]" : "text-[#68726c]"}`}>{row.money.status === "invalid" ? row.money.message : row.money.status === "unknown" ? "未知，尚未计入" : `${money(row.money)} · 用户估算${row.money.cents === 0 ? "，未核实免费" : ""}`}</p>
          </div>;
        })}</div>
      </section>
      <section aria-label="预算核对汇总" aria-live="polite" className="mt-5 rounded-xl border border-[#315f51]/25 bg-white p-4 text-sm leading-6">
        <h3 className="font-semibold text-[#18392f]">当前核对结果</h3>
        <p>全程总预算：{money(costs.budget)} · 人民币／全部旅客</p>
        {costs.errors.length > 0 ? <div role="alert" className="mt-2 text-[#9b3d30]"><p>存在无效金额，暂停显示小计与预算差额；请修正，不沿用上次结果。</p><ul className="list-disc pl-5">{costs.errors.map((e, i) => <li key={i}>{e}</li>)}</ul></div> : <>
          <p>交通参考已知小计：{formatCents(costs.transportCents!)} · 用户估算已填小计：{formatCents(costs.userCents!)}</p>
          <p className="mt-2 font-semibold">当前可计算小计：{formatCents(costs.subtotalCents!)}</p>
          <p>预算减当前小计：{formatCents(costs.differenceCents!)}{costs.missing.length ? "（尚有未知费用，不是可自由支配余额）" : "（按当前参考票价和用户估算计算）"}</p>
          {costs.differenceCents! < 0 && <p className="mt-2 font-semibold text-[#9b3d30]">{costs.missing.length || costs.unarrangedRequired ? "已计入部分已超过预算" : "按当前参考票价和用户估算计算，已超过预算"} {formatCents(-costs.differenceCents!)}。</p>}
        </>}
        {costs.missing.length > 0 ? <details open className="mt-3"><summary className="cursor-pointer font-medium">尚未核算的费用（{costs.missing.length} 项）</summary><ul className="mt-2 list-disc space-y-1 pl-5">{costs.missing.map((label, i) => <li key={i}>{label}：未知</li>)}</ul></details>
          : costs.errors.length === 0 && <p className="mt-3">当前范围内费用字段已填齐，仍只是参考票价与用户估算，不保证实际支出。</p>}
        <p className="mt-3 text-xs text-[#68726c]">步行票费为零不代表景点免费。未安排的可选地点不纳入费用，也不算缺失项。预算不参与排程或自动调整。</p>
      </section>
    </>}
  </section>;
}
