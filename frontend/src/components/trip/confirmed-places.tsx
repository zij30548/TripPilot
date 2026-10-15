import type { TripRequest } from "@/types/trip";
import PlaceDetails from "@/components/places/place-details";
import { destination } from "@/lib/destinations";

export default function ConfirmedPlaces({ request, restored = false, demo = false }: { request: TripRequest; restored?: boolean; demo?: boolean }) {
  const accommodation = request.accommodation_place;
  const mustVisit = request.must_visit_places ?? [];
  return <section aria-label="已确认需求地点" className="rounded-3xl border border-[#18201d]/10 bg-white p-6 sm:p-8">
    <h2 className="text-xl font-semibold">本次提交的需求地点</h2>
    <p className="mt-2 text-sm font-medium">目的地：{request.destination_id ? destination(request.destination_id).name : "上海（旧版契约）"}</p>
    {request.destination_id && accommodation && !accommodation.region && <p role="status" className="mt-2 text-sm text-amber-800">旧存档地点缺少城市归属信息。请修改需求，重新搜索确认地点后再生成。</p>}
    <p className="mt-2 text-sm leading-6 text-[#56605c]">{restored ? "这是本副本当前确认的需求，主动生成后才产生新正式草案；历史地点尚未重新检索。" : demo ? "已确认的结构化地点尚未用于安排当前 Mock 行程，也不会自动绑定到示例活动。" : "这些是你明确确认的需求地点，主动生成后才有正式草案。"}住宿参考点可以是酒店或附近地标，不代表已预订住宿。</p>
    <p className="mt-2 text-sm">{request.start_date} 至 {request.end_date} · {request.travelers} 人 · 全程总预算 ¥{request.budget}。兴趣用于候选搜索；节奏暂不参与排程，预算不自动优化行程。</p>
    <p className="mt-1 text-xs leading-5 text-[#68726c]">来源标记随需求提交；后端仅校验数据结构，本轮未重新查询地点详情或核实其真实性。“不想去的地点”等其他需求也尚未用于规划。</p>
    <div className="mt-5 grid gap-5 sm:grid-cols-2">
      <section aria-label="提交的住宿参考点" className="min-w-0 rounded-xl bg-[#f4f8f5] p-4">
        <h3 className="mb-3 text-sm font-semibold text-[#315f51]">住宿参考点</h3>
        {accommodation ? <PlaceDetails place={accommodation} /> : <p className="break-words text-sm text-[#68726c]">旧版文字需求（未确认具体地点）：{request.accommodation_location}</p>}
      </section>
      <section aria-label="提交的必去地点" className="min-w-0 rounded-xl bg-[#f4f8f5] p-4">
        <h3 className="mb-3 text-sm font-semibold text-[#315f51]">必去地点 · {mustVisit.length} 个已确认</h3>
        {mustVisit.length ? <ul className="space-y-4">{mustVisit.map((place) => <li key={place.id}><PlaceDetails place={place} /></li>)}</ul>
          : <p className="break-words text-sm text-[#68726c]">{request.must_visit.length ? `旧版文字需求（未确认具体地点）：${request.must_visit.join("、")}` : "未选择必去地点"}</p>}
      </section>
    </div>
  </section>;
}
