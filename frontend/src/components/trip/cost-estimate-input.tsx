"use client";

import { useId } from "react";
import { formatCents, type UserCost } from "@/lib/schedule-costs";

export default function CostEstimateInput({ row, onChange }: { row: UserCost; onChange: (row: UserCost, value: string) => void }) {
  const id = useId();
  return <div className="min-w-0 rounded-xl bg-white p-4">
    <label htmlFor={id} className="block break-words text-sm font-medium">{row.label}<span className="mt-1 block text-xs font-normal text-[#68726c]">用户估算 · 全部旅客合计（元）</span></label>
    <p id={`${id}-detail`} className="mt-1 break-words text-xs leading-5 text-[#68726c]">{row.detail}</p>
    <input id={id} type="text" inputMode="decimal" autoComplete="off" value={row.value} placeholder="留空表示未知" aria-invalid={row.money.status === "invalid"} aria-describedby={`${id}-detail ${id}-status`}
      onChange={(event) => onChange(row, event.target.value)} className="mt-2 min-h-11 w-full rounded-lg border border-[#315f51]/30 px-3 py-2 text-sm focus-visible:outline-2 focus-visible:outline-[#315f51]" />
    <p id={`${id}-status`} role={row.money.status === "invalid" ? "alert" : undefined} className={`mt-1 text-xs leading-5 ${row.money.status === "invalid" ? "text-[#9b3d30]" : "text-[#68726c]"}`}>{row.money.status === "invalid" ? row.money.message : row.money.status === "unknown" ? "未知，尚未计入" : `${formatCents(row.money.cents)} · 用户估算${row.money.cents === 0 ? "，未核实免费" : ""}`}</p>
  </div>;
}
