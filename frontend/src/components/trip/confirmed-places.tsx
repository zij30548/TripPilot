import type { TripRequest } from "@/types/trip";
import PlaceDetails from "@/components/places/place-details";

export default function ConfirmedPlaces({ request }: { request: TripRequest }) {
  const accommodation = request.accommodation_place;
  const mustVisit = request.must_visit_places ?? [];
  return <section aria-label="已确认需求地点" className="rounded-3xl border border-[#18201d]/10 bg-white p-6 sm:p-8">
    <h2 className="text-xl font-semibold">本次提交的需求地点</h2>
    <p className="mt-2 text-sm leading-6 text-[#56605c]">已确认的结构化地点尚未用于安排当前 Mock 行程，也不会自动绑定到示例活动。住宿参考点可以是酒店或附近地标，不代表已预订住宿。</p>
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
