"use client";

import { compareScheduleCosts, formatCents, type CostInputs, type ScheduleCosts, type UserCost } from "@/lib/schedule-costs";
import type { ScheduleResponse } from "@/types/schedule";
import type { TripRequest } from "@/types/trip";
import CostEstimateInput from "./cost-estimate-input";

function CostSide({ label, costs }: { label: string; costs: ScheduleCosts }) {
  return <section aria-label={label} className="min-w-0 break-words rounded-xl border border-[#315f51]/20 bg-white p-4 text-sm leading-6">
    <h4 className="font-semibold text-[#18392f]">{label}</h4>
    {costs.status === "unavailable" ? <p>{costs.message}</p> : <>
      {costs.errors.length ? <div role="alert"><p>金额无效，暂不显示小计和预算差额。</p><ul className="list-disc pl-5">{costs.errors.map((message, i) => <li key={i}>{message}</li>)}</ul></div> : <>
        <p>交通参考已知小计：{formatCents(costs.transportCents!)}</p>
        <p>用户估算已填小计：{formatCents(costs.userCents!)}</p>
        <p className="font-semibold">当前可计算小计：{formatCents(costs.subtotalCents!)}</p>
        <p>预算减当前小计：{formatCents(costs.differenceCents!)}{costs.missing.length ? "（尚有未知费用，不是可自由支配余额）" : "（按当前参考票价和用户估算计算）"}</p>
        {costs.differenceCents! < 0 && <p className="text-[#9b3d30]">{costs.missing.length ? "已计入部分已超过预算" : "当前参考费用已超过预算"} {formatCents(-costs.differenceCents!)}。</p>}
      </>}
      {costs.missing.length ? <div className="mt-2"><p>尚未核算的费用（{costs.missing.length} 项）</p><ul className="list-disc pl-5">{costs.missing.map((message, i) => <li key={i}>{message}：未知</li>)}</ul></div> : !costs.errors.length && <p className="mt-2">当前范围内费用字段已填齐，不保证实际支出。</p>}
    </>}
  </section>;
}

export default function ScheduleCostComparison({ original, proposed, request, inputs, onEdit }: {
  original: ScheduleResponse; proposed: ScheduleResponse; request: TripRequest; inputs: CostInputs; onEdit: (row: UserCost, value: string) => void;
}) {
  const { before, after, deltaCents, issues } = compareScheduleCosts(original, proposed, request, inputs);
  const originalIds = new Set(original.days.flatMap((day) => day.items.filter((item) => item.kind === "visit").map((item) => item.place_id)));
  const added = after.status === "ready" ? after.user.filter((row) => row.category === "ticket" && !originalIds.has(row.placeId!)) : [];
  return <section aria-label="调整预览费用对比" className="mt-5 min-w-0 rounded-2xl border border-[#315f51]/25 bg-[#f4f8f5] p-4 sm:p-5">
    <h3 className="text-lg font-semibold text-[#18392f]">费用对比</h3>
    <p className="mt-2 text-sm leading-6">两份方案均按当前填写的费用估算计算，共用全程总预算和人数。原、新路线查询批次见上方生成时间；不同批次参考票价可能变化，不能把全部差额归因于排除一个地点。</p>
    <div className="mt-3 grid gap-3 sm:grid-cols-2"><CostSide label="原方案费用" costs={before} /><CostSide label="预览方案费用" costs={after} /></div>
    <div aria-live="polite" className="mt-3 text-sm leading-6">
      <p className="font-semibold">{deltaCents === null ? "完整费用增减：暂不可计算" : deltaCents === 0 ? "按当前参考票价和用户估算，估算金额相同" : `按当前参考票价和用户估算，新方案${deltaCents > 0 ? "增加" : "减少"} ${formatCents(Math.abs(deltaCents))}`}</p>
      {issues.length > 0 && <ul className="list-disc pl-5">{issues.map((issue, i) => <li key={i}>{issue}</li>)}</ul>}
      <p>差额为新方案减原方案；已知小计下降不等于省钱，也不代表预算够用。费用只作参考，不改变行程采用条件。</p>
    </div>
    {added.length > 0 && <section aria-label="预览新增地点费用" className="mt-4">
      <h4 className="font-semibold">预览新增地点的用户估算</h4>
      <p className="mt-2 text-sm leading-6">填写估算不代表采用此方案，未采用的地点不计入正式预算。共有地点及餐饮、住宿、其他费用请在上方正式费用面板编辑，两边同步更新。</p>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">{added.map((row) => <CostEstimateInput key={row.key} row={row} onChange={onEdit} />)}</div>
    </section>}
    <p className="mt-3 text-xs leading-6 text-[#56605c]">取消只取消行程调整，不回滚你主动填写的费用。同 ID 的原估算在本次结果会话内保留，重新预览可复用；未在正式行程中的地点暂不计费。估算不是新日期报价，不会自动减少餐饮或住宿。</p>
  </section>;
}
