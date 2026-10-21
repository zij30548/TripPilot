"use client";

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import type { ScheduleCapture } from "@/lib/schedule-adjustment";
import { useScheduleMap } from "@/lib/use-schedule-map";
import SchedulePlaceMap from "@/components/trip/schedule-place-map";

type Props = {
  capture: ScheduleCapture;
  selection: {key: string; date?: string; edgeId?: string};
  isCurrent: () => boolean;
  onClose: () => void;
};

// The parent only mounts this overlay following an explicit map action. A fresh
// action identity/draft version owns a fresh view; incidental cost renders do not.
export default function DialogueMap(props: Props) {
  return <MapSession key={`${props.selection.key}:${props.capture.version}`} {...props} />;
}

function MapSession({capture,selection,isCurrent,onClose}: Props) {
  const [seed] = useState(() => structuredClone(capture));
  const response = seed.original;
  const map = useScheduleMap(response,{isCurrent},seed.drawing);
  const root = useRef<HTMLDivElement>(null), close = useRef<HTMLButtonElement>(null), applied = useRef(false);
  const requestedDate = selection.date ?? response.days[0]?.date;
  const requestedDay = response.days.find(day=>day.date===requestedDate);
  const requestedRoute = selection.edgeId == null ? -1 : requestedDay?.items.findIndex(item=>item.edge_id===selection.edgeId&&(item.kind==="walk"||item.kind==="transit")) ?? -1;
  const invalidSelection = !requestedDay || (selection.edgeId != null && requestedRoute < 0);
  const valid = isCurrent();
  useEffect(()=>{
    const previous=document.activeElement instanceof HTMLElement?document.activeElement:null;
    close.current?.focus();
    return ()=>{if(previous?.isConnected)previous.focus({preventScroll:true});};
  },[]);
  useEffect(()=>{
    if(applied.current || invalidSelection || !isCurrent())return;
    if(map.date!==requestedDate){map.selectDate(requestedDate!);return;}
    applied.current=true;
    if(requestedRoute>=0)map.selectRoute(requestedRoute);
    else if(!map.initialized)map.toggle();
  },[map,requestedDate,requestedRoute,invalidSelection,isCurrent]);
  const keyDown=(event:KeyboardEvent<HTMLDivElement>)=>{
    if(event.key==="Escape"){event.preventDefault();event.stopPropagation();onClose();return;}
    if(event.key!=="Tab")return;
    const nodes=Array.from(root.current?.querySelectorAll<HTMLElement>('button:not([disabled]), a[href], select:not([disabled]), input:not([disabled]), [tabindex="0"]')??[])
      .filter(node=>!node.closest("[hidden], [inert]")&&node.getAttribute("aria-hidden")!=="true");
    const first=nodes[0],last=nodes.at(-1);
    if(!first){event.preventDefault();root.current?.focus();return;}
    if(event.shiftKey&&(document.activeElement===first||document.activeElement===root.current)){event.preventDefault();last?.focus();}
    else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
  };
  const day=response.days.find(day=>day.date===map.date);
  const places=new Map([response.request.accommodation_place,...response.request.must_visit_places,...response.request.optional_places??[]].map(place=>[place.id,place]));
  return <div className="fixed inset-0 z-[70] bg-[#142b23]/35 p-2 sm:p-6" onMouseDown={event=>{if(event.target===event.currentTarget)onClose();}}>
    <div ref={root} role="dialog" aria-modal="true" aria-labelledby="dialogue-map-title" tabIndex={-1} onKeyDown={keyDown}
      className="mx-auto flex h-full w-full max-w-5xl flex-col overflow-hidden rounded-2xl bg-white shadow-xl outline-none">
      <header className="flex shrink-0 items-center justify-between gap-3 border-b border-[#e9eeeb] px-4 py-3 sm:px-6">
        <div className="min-w-0"><h2 id="dialogue-map-title" className="text-base font-semibold text-[#18392f]">当前行程地图</h2><p className="mt-1 text-xs text-[#68766f]">当前正式草案 · 一次查看一段已有参考路线</p></div>
        <button ref={close} type="button" onClick={onClose} aria-label="关闭行程地图" className="min-h-11 shrink-0 rounded-lg px-3 text-sm text-[#315f51] hover:bg-[#f2f5f3]">关闭</button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-3 sm:p-5">
        {!valid ? <p role="status" className="p-3 text-sm leading-6 text-[#835718]">地图对应的正式草案已失效。请关闭后，重新生成或读取当前正式草案再打开。</p>
          : invalidSelection ? <p role="alert" className="p-3 text-sm leading-6 text-[#835718]">指定日期或交通段不属于这份正式草案，未初始化地图，也没有补查路线。</p>
          : <>
            <div role="group" aria-label="地图日期" className="flex flex-wrap gap-2">{response.days.map((item,index)=><button key={item.date} type="button" aria-pressed={item.date===map.date}
              onClick={()=>map.selectDate(item.date)} className={`min-h-11 rounded-lg border px-3 py-2 text-sm ${item.date===map.date?"border-[#315f51] bg-[#eef5f0] text-[#18392f]":"border-[#e0e7e2] text-[#59685f]"}`}>第 {index+1} 天 · {item.date}</button>)}</div>
            <SchedulePlaceMap map={map} />
            <section aria-label="地图对应当天安排" className="mt-5 min-w-0">
              <h3 className="text-sm font-semibold text-[#18392f]">{map.date} · 访问顺序与交通</h3>
              {!day?.items.some(item=>item.kind==="visit")?<p className="mt-3 text-sm text-[#68766f]">当天没有已安排游览，不用其他日期地点补齐。</p>
                : <ol className="mt-2 divide-y divide-[#e8eeea]">{day.items.map((item,index)=>{
                  const visit=map.data.visits.find(visit=>visit.itemIndex===index);
                  const selected=map.selection?.target==="visit"&&map.selection.itemIndex===index;
                  const routeSelected=map.route?.itemIndex===index;
                  const movement=item.kind==="walk"||item.kind==="transit";
                  return <li key={`${map.date}-${index}`} ref={node=>{if(node)map.cards.current.set(index,node);else map.cards.current.delete(index);}}
                    tabIndex={visit?-1:undefined} data-map-selected={selected} data-route-selected={routeSelected}
                    className={`min-w-0 px-2 py-3 outline-none ${selected||routeSelected?"rounded-lg bg-[#eef5f0] ring-1 ring-[#315f51]":""}`}>
                    <p className="text-xs text-[#68766f]">{item.start_time}—{item.end_time} · {item.duration_minutes} 分钟</p>
                    {visit?<><p className="mt-1 break-words text-sm text-[#18392f]">{visit.number}. {visit.place?.name??"地点快照缺失"}</p>
                      <p className="mt-1 break-words text-xs text-[#68766f]">{visit.place?.address??"地址未知"}</p>
                      <button type="button" onClick={()=>map.locateVisit(index)} className="mt-1 min-h-11 rounded-lg px-2 text-sm text-[#315f51] underline underline-offset-4">定位地点 {visit.number}</button></>
                      : movement?<><p className="mt-1 break-words text-sm text-[#18392f]">{places.get(item.from_place_id!)?.name??"起点未知"} → {places.get(item.to_place_id!)?.name??"终点未知"}</p>
                        <button type="button" onClick={()=>map.selectRoute(index)} aria-label={`查看这段路线 ${item.start_time}`} className="mt-1 min-h-11 rounded-lg px-2 text-sm text-[#315f51] underline underline-offset-4">查看这段{item.kind==="walk"?"步行":"公交／地铁"}路线</button></>
                        : <p className="mt-1 text-sm text-[#59685f]">{item.kind==="lunch"?"午餐预留（没有餐厅地点）":"等待／时间预留"}</p>}
                  </li>;
                })}</ol>}
              <p className="mt-3 text-xs text-[#68766f]">{day?.return_time?`预计返回住宿参考点：${day.return_time}`:"当天没有已安排的返程时刻。"}</p>
            </section>
          </>}
      </div>
    </div>
  </div>;
}
