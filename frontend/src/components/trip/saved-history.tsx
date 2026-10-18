"use client";
import { useEffect, useRef, useState } from "react";
import { useSavedHistory } from "@/lib/use-saved-trips";
import type { SavedDetail } from "@/lib/saved-trips";
import { ScheduleResult } from "./schedule-preview";
import ScheduleBudget from "./schedule-budget";
import PlaceDetails from "../places/place-details";
import { destination } from "@/lib/destinations";

const stamp=(value:string)=>new Date(value).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai",hour12:false});
const button="min-h-11 rounded-xl border border-[#315f51]/40 bg-white px-4 py-2 text-sm text-[#18392f] disabled:opacity-40";
export function SavedTripDetails({detail,allowRestore=true}:{detail:SavedDetail;allowRestore?:boolean}){
  const {requirements:r,schedule:s,costs}=detail.snapshot;
  return <article aria-label="历史快照详情" className="min-w-0 space-y-4 break-words">
    <h2 className="text-xl font-semibold">{detail.name} · 历史快照</h2>
    {allowRestore && <a href={`/plan/from/${detail.id}`} target="_blank" rel="noopener noreferrer" className="inline-flex min-h-11 items-center rounded-xl bg-[#18392f] px-4 py-2 text-sm text-white">基于此行程继续规划<span className="ml-2 text-xs">（新标签页）</span></a>}
    <p>服务端保存时间：{stamp(detail.saved_at)}<br/>原草案生成时间：{stamp(s.generated_at)}</p>
    <p className="rounded-xl bg-amber-50 p-3 text-sm leading-6">只读存档，不覆盖当前工作草案。以下是保存时的数据；打开历史未重新查询交通、天气、营业或预约。保存时间不是核验时间，路线原查询时间在各段保留。</p>
    <section aria-label="历史确认需求" className="rounded-xl bg-[#f4f8f5] p-4 text-sm leading-7">
      <h3 className="font-semibold">保存的确认需求</h3>
      <p>目的地：{r.destination_id ? destination(r.destination_id).name : "上海（旧 v1 快照，未保存地点归属）"}</p>
      <p>{r.start_date}—{r.end_date} · {r.travelers} 人 · 全程总预算 ¥{r.budget}（全部旅客）</p>
      <p>每日窗口 {r.daily_start_time}—{r.daily_end_time} · 旅行节奏 {({relaxed:"轻松",balanced:"均衡",packed:"紧凑"})[r.pace]}</p>
      <p>兴趣：{r.interests.join("、")||"未选择"} · 不想去：{r.avoid_places.join("、")||"未填写"}（文字未作为自动过滤条件）</p>
      <p>停留设置与午餐规则见原请求及时间轴；住宿为参考点，不代表已预订。</p>
      <div className="mt-3 grid gap-4 sm:grid-cols-2"><div><h4 className="font-semibold">住宿参考点</h4><PlaceDetails place={s.request.accommodation_place}/></div>
        <div><h4 className="font-semibold">确认必去地点</h4>{s.request.must_visit_places.map(p=><div key={p.id} className="mb-3"><PlaceDetails place={p}/></div>)}{!s.request.must_visit_places.length&&<p>无</p>}</div></div>
      <details className="mt-3"><summary>可选地点与原规划设置</summary>{s.request.optional_places?.map(p=><div key={p.id}><PlaceDetails place={p}/></div>)}
        <ul>{s.request.duration_settings.map(v=><li key={v.place_id}>{[...s.request.must_visit_places,...s.request.optional_places??[]].find(p=>p.id===v.place_id)?.name}：{v.minutes} 分钟 · {v.source==="default"?"默认规划设置":"用户设置"}</li>)}</ul>
        <p>午餐：{s.request.lunch.enabled?`${s.request.lunch.start_time}—${s.request.lunch.end_time} 预留` : "关闭"}</p></details>
    </section>
    <ScheduleResult key={detail.id} response={s}/>
    <ScheduleBudget request={r} response={s} inputs={costs} readOnly onEdit={()=>undefined}/>
  </article>;
}
function HistoryWindow({close}:{close:()=>void}){
  const history=useSavedHistory(), dialog=useRef<HTMLDialogElement>(null);
  const {load,state}=history;
  useEffect(()=>{dialog.current?.showModal?.();void load(null);},[load]);
  return <dialog ref={dialog} aria-label="历史行程" onCancel={e=>{e.preventDefault();close();}} className="fixed inset-0 m-auto h-[92dvh] w-[calc(100%-24px)] max-w-6xl overflow-y-auto rounded-2xl border border-[#315f51]/30 bg-[#f4f2ec] p-4 text-[#18201d] shadow-xl backdrop:bg-black/40 sm:p-7">
    <header className="mb-5 flex flex-wrap items-center justify-between gap-3"><h1 className="text-2xl font-semibold">历史行程 · 只读</h1><button className={button} onClick={close}>关闭历史</button></header>
    <p className="mb-4 text-sm">仅显示当前邀请账号的存档；无归属的旧存档不公开。原记录不可修改、删除或分享。可从详情在新标签页创建独立规划副本。查看历史不会生成行程或调用外部服务。</p>
    {state.status!=="list"&&<button className={button} onClick={()=>void load(null,state.offset)}>返回历史列表</button>}
    {state.status==="loading"&&<p role="status" className="mt-4">正在读取历史…</p>}
    {state.status==="error"&&<p role="alert" className="mt-4">{state.error}</p>}
    {state.detail&&<SavedTripDetails detail={state.detail}/>}
    {state.list&&<><ul className="mt-4 space-y-3">{state.list.items.map(item=><li key={item.id} className="rounded-xl bg-white p-4">
      <button className="min-h-11 break-words text-left font-semibold text-[#18392f] underline" onClick={()=>void load(item.id,state.offset)}>{item.name}</button>
      <p className="text-sm leading-7">{item.start_date}—{item.end_date} · {item.transport_mode==="walking"?"步行":"公交／地铁参考"} · {({complete:"完整安排",partial:"部分安排",unscheduled:"尚未安排"})[item.status]}<br/>保存于 {stamp(item.saved_at)}</p>
    </li>)}</ul>{!state.list.items.length&&<p className="mt-4">尚无历史存档。</p>}
      <nav className="mt-5 flex flex-wrap gap-3" aria-label="历史分页"><button className={button} disabled={state.offset===0} onClick={()=>void load(null,state.offset-10)}>上一页</button><span className="py-3 text-sm">第 {state.offset/10+1} 页 · 每页最多 10 条</span><button className={button} disabled={!state.list.has_more||state.offset>=100000} onClick={()=>void load(null,state.offset+10)}>下一页</button></nav></>}
  </dialog>;
}
export default function SavedHistory({label="历史行程"}:{label?:string}){
  const [open,setOpen]=useState(false);
  return <><button type="button" className={button} onClick={()=>setOpen(true)}>{label}</button>{open&&<HistoryWindow close={()=>setOpen(false)}/>}</>;
}
