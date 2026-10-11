"use client";
import { useState } from "react";
import { useSaveTrip } from "@/lib/use-saved-trips";
import type { useSchedulePreview } from "@/lib/use-schedule-preview";
import type { TripRequest } from "@/types/trip";
import type { CostInputs } from "@/lib/schedule-costs";
import SavedHistory from "./saved-history";

export default function SaveTrip({schedule,requirements,getCosts}:{schedule:ReturnType<typeof useSchedulePreview>;requirements:TripRequest;getCosts:()=>CostInputs}){
  const [name,setName]=useState(`上海 ${requirements.start_date} 行程`);
  const save=useSaveTrip(schedule,requirements,getCosts), loading=save.state.status==="saving";
  return <section aria-label="保存正式行程" className="mt-6 rounded-2xl border border-[#315f51]/30 bg-white p-5 sm:p-7">
    <h2 className="text-xl font-semibold">保存行程</h2>
    <p className="mt-2 text-sm leading-6 text-[#56605c]">只保存点击时的正式草案、确认需求与相关用户费用输入。未采用的调整预览不会保存；部分或未安排结果会保留原状态和原因。AI 解读、天气、地图和临时状态不存档。</p>
    <label className="mt-3 block text-sm">存档名称<input maxLength={80} value={name} onChange={e=>setName(e.target.value)} className="mt-2 block min-h-11 w-full rounded-xl border border-[#315f51]/30 p-3"/></label>
    <div className="mt-4 flex flex-wrap gap-3"><button type="button" disabled={loading||schedule.state.status!=="success"||!name.trim()} onClick={()=>void save.save(name)} className="min-h-11 rounded-xl bg-[#18392f] px-5 py-2 text-sm text-white disabled:opacity-40">{loading?"正在保存…":"保存当前正式草案"}</button><SavedHistory label="查看历史存档"/>
      {loading&&<button type="button" onClick={save.cancel} className="min-h-11 rounded-xl border px-4 py-2 text-sm">停止等待保存</button>}
      {save.state.status==="uncertain"&&<button type="button" onClick={()=>void save.retry()} className="min-h-11 rounded-xl border px-4 py-2 text-sm">重试保存冻结快照</button>}</div>
    {save.state.message&&<p role="status" className="mt-3 break-words text-sm">{save.state.message}</p>}
    {save.state.detail&&<p className="mt-2 text-xs">服务端保存时间：{new Date(save.state.detail.saved_at).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai"})}。这只确认该次快照已保存，不表示之后的当前内容也已保存。</p>}
    <p className="mt-3 text-xs leading-5 text-[#68726c]">保存超时或停止等待不代表数据库一定未提交；重试冻结快照复用同一操作标识。再次“保存当前正式草案”会创建新的独立记录。</p>
  </section>;
}
