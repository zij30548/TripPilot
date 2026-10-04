import type { Ref } from "react";
import type { Place } from "@/types/place";
import type { Activity } from "@/types/trip";

const categories = { sightseeing: "城市漫步", food: "餐饮", museum: "文化参观", shopping: "购物" };

export default function ActivityCard({ activity, order, place, selected = false, choosingPlace = false,
  cardRef, onChoosePlace, onShowPlace, onRemovePlace }: {
  activity: Activity;
  order: number;
  place?: Place;
  selected?: boolean;
  choosingPlace?: boolean;
  cardRef?: Ref<HTMLElement>;
  onChoosePlace?: () => void;
  onShowPlace?: () => void;
  onRemovePlace?: () => void;
}) {
  return (
    <article ref={cardRef} tabIndex={-1} aria-label={activity.name}
      className={`rounded-2xl border bg-white p-4 outline-none sm:p-6 ${selected ? "border-[#315f51] ring-2 ring-[#315f51]/20" : "border-[#dde3dd]"}`}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="font-mono text-sm font-semibold text-[#315f51]"><time dateTime={activity.start_time}>{activity.start_time.slice(0, 5)}</time> — <time dateTime={activity.end_time}>{activity.end_time.slice(0, 5)}</time></p>
        <span className="rounded-full bg-[#edf3ef] px-3 py-1 text-xs text-[#315f51]">{categories[activity.category]}</span>
      </div>
      <div className="mt-4 flex items-start gap-3">
        <span className="grid size-7 shrink-0 place-items-center rounded-full bg-[#18392f] text-xs font-semibold text-white" aria-hidden="true">{order}</span>
        <div className="min-w-0"><h3 className="break-words text-lg font-semibold">{place && onShowPlace ? <button type="button" onClick={onShowPlace}
          aria-label={`在地图定位${activity.name}`} className="rounded text-left underline decoration-[#315f51]/30 underline-offset-4 hover:text-[#315f51] focus-visible:outline-2 focus-visible:outline-offset-2">{activity.name}</button> : activity.name}</h3><p className="mt-2 text-sm leading-6 text-[#56605c]">{activity.description}</p></div>
      </div>
      {onChoosePlace && <div className="mt-4 rounded-xl bg-[#f5f6f1] p-3 sm:p-4">
        {place ? <div className="space-y-1">
          <p className="text-xs font-semibold text-[#315f51]">已绑定 · 高德真实地点</p>
          <p className="break-words text-sm font-semibold">{place.name}</p>
          <p className="break-words text-xs leading-5 text-[#56605c]">地址：{place.address?.trim() || "暂无地址"}</p>
          <p className="text-[11px] leading-5 text-[#68726c]">来源：高德地图</p>
        </div> : <p className="text-xs leading-5 text-[#68726c]">尚未绑定真实地点</p>}
        {choosingPlace && <p className="mt-2 text-xs font-semibold text-[#315f51]">正在为此活动选择地点</p>}
        <div className="mt-3 flex flex-wrap gap-2">
          <button type="button" onClick={onChoosePlace} aria-label={`为${activity.name}${place ? "更换" : "绑定"}地点`}
            className="min-h-10 rounded-lg border border-[#315f51]/40 bg-white px-3 py-2 text-xs font-semibold text-[#315f51] focus-visible:outline-2 focus-visible:outline-offset-2">{place ? "更换地点" : "绑定地点"}</button>
          {place && <>
            <button type="button" onClick={onShowPlace} aria-label={`在地图查看${activity.name}`}
              className="min-h-10 rounded-lg bg-[#18392f] px-3 py-2 text-xs font-semibold text-white focus-visible:outline-2 focus-visible:outline-offset-2">在地图查看</button>
            <button type="button" onClick={onRemovePlace} aria-label={`解除${activity.name}的地点绑定`}
              className="min-h-10 rounded-lg px-3 py-2 text-xs text-[#a63d2d] underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-2">解除绑定</button>
          </>}
        </div>
        <p className="mt-2 text-[11px] leading-5 text-[#68726c]">仅关联地点；活动时间、费用和交通仍为 Mock。</p>
      </div>}
      <p className="mt-4 border-t border-[#18201d]/8 pt-3 text-xs text-[#56605c]">预计费用 · Mock <span className="float-right text-sm font-semibold text-[#18392f]">¥{activity.estimated_cost.toFixed(2)}</span></p>
    </article>
  );
}
