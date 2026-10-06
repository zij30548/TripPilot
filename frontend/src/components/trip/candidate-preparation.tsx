"use client";

import type { TripRequest } from "@/types/trip";
import type { Candidate } from "@/types/candidates";
import type { useCandidatePool } from "@/lib/use-candidate-pool";
import PlaceDetails from "@/components/places/place-details";

type CandidatePool = ReturnType<typeof useCandidatePool>;

function retrievedAt(value: string) {
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

function SearchSources({ candidate }: { candidate: Candidate }) {
  return <p className="mt-2 break-words text-xs leading-5 text-[#68726c]">
    检索来源：{candidate.retrieval_sources.length
      ? candidate.retrieval_sources.map(({ interest, keyword }) => `${interest ?? "通用候选"} · ${keyword}`).join("；")
      : "你已确认的必去要求"}
  </p>;
}

export default function CandidatePreparation({ request, pool }: { request: TripRequest; pool: CandidatePool }) {
  const { state, candidates, excludedIds, activePlaces, canQuery } = pool;
  const required = candidates.filter((candidate) => candidate.role === "must_visit");
  const optional = candidates.filter((candidate) => candidate.role === "optional");
  const excludedCount = optional.filter((candidate) => excludedIds.has(candidate.place.id)).length;
  const loading = state.status === "loading";
  const attempted = state.status !== "idle" || state.response !== null;
  const lastAttempt = state.lastAttempt;

  return <section aria-label="候选地点准备" className="mt-6 min-w-0 rounded-3xl border border-[#315f51]/20 bg-[#f4f8f5] p-5 sm:p-8">
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div className="min-w-0 flex-1 basis-64">
        <h2 className="text-xl font-semibold text-[#18392f]">候选地点准备</h2>
        <p className="mt-2 text-sm leading-6 text-[#56605c]">为整次旅行准备地点清单。必去要求始终保留；点击获取后，再按兴趣搜索上海的真实可选地点。</p>
      </div>
      <button type="button" disabled={!canQuery || loading} onClick={() => { void pool.query(); }}
        className="min-h-11 max-w-full rounded-xl bg-[#18392f] px-5 py-3 text-sm font-semibold text-white focus-visible:outline-2 focus-visible:outline-offset-2 disabled:cursor-not-allowed disabled:opacity-50">
        {loading ? state.response ? "正在更新候选地点…" : "正在获取候选地点…" : attempted ? "重新获取候选地点" : "获取候选地点"}
      </button>
    </div>
    <p className="mt-3 text-xs leading-5 text-[#68726c]">关键词命中仅说明检索来源，不代表最佳推荐、精准匹配或满足预算。这里不会排程、绑定活动或修改当前 Mock 行程的时间、交通、天气与费用。</p>
    <p className="mt-1 text-xs leading-5 text-[#68726c]">切换日期仍保留本清单；返回修改需求、重新生成或刷新后清空候选准备和排除决定，已提交的必去要求不受排除操作影响。</p>
    {request.avoid_places.length > 0 && <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm leading-6 text-[#6c521e]">
      <p className="break-words">你填写的不想去的地点：{request.avoid_places.join("、")}</p>
      <p>本轮尚未按这些文字自动过滤，可手动排除下方可选地点。</p>
    </div>}
    {!canQuery && <p className="mt-4 rounded-xl bg-white p-3 text-sm leading-6 text-[#a63d2d]">请先返回修改旅行需求，确认住宿参考点并检查所选兴趣后再获取候选地点。不会根据文字住宿猜测坐标。</p>}
    <div aria-live="polite" aria-atomic="true" className="mt-4 text-sm leading-6 text-[#315f51]">
      {state.status === "idle" && <p>尚未获取可选地点，不会自动搜索。</p>}
      {loading && <p role="status">{state.response ? "正在更新，暂时保留上次候选和排除决定。" : "正在获取候选地点，请稍候。"}</p>}
      {state.status === "success" && <p>全部检索完成。{optional.length === 0 ? "本次没有新的可选地点，必去要求仍保留。" : `本次获得 ${optional.length} 个可选地点。`}</p>}
      {state.status === "partial" && <p>部分检索未完成，下方仅展示本次成功检索得到的候选，未混入上次结果。</p>}
      {state.status === "failed" && <p role="alert">{state.message ?? "获取候选地点失败，请主动重新获取。"}</p>}
    </div>
    {state.response && <p className="mt-2 text-xs leading-5 text-[#68726c]">当前候选检索时间：{retrievedAt(state.response.queried_at)} · 当前清单保留 {activePlaces.length} 个地点（必去与未排除可选）。尚未安排进活动。</p>}
    {!loading && lastAttempt && <details className="mt-3 rounded-xl border border-[#18201d]/10 bg-white p-3 text-xs leading-6 text-[#56605c]">
      <summary className="cursor-pointer font-medium">本次检索明细（{lastAttempt.queries.length} 项）</summary>
      <p className="mt-2">查询时间：{retrievedAt(lastAttempt.queried_at)}；每项仅检索第一页，最多 20 条。</p>
      <ul className="mt-1 space-y-1">{lastAttempt.queries.map((query) => <li key={query.keyword} className="break-words">
        {query.interest ?? "通用候选"} → {query.keyword}：{query.status === "success" ? `成功，返回 ${query.result_count} 个地点` : query.status === "timeout" ? "超时" : "未能获取"}
        {query.status !== "success" && query.message ? `；${query.message}` : ""}
      </li>)}</ul>
    </details>}
    <div className="mt-6">
      <h3 className="text-sm font-semibold text-[#18392f]">必去地点 · {required.length} 个</h3>
      <p className="mt-1 text-xs leading-5 text-[#68726c]">来自本次已确认需求，不能在候选区排除；如需调整，请返回修改旅行需求。</p>
      {required.length === 0 ? <p className="mt-3 text-sm text-[#68726c]">本次没有已确认的必去地点。</p>
        : <ul aria-label="候选中的必去地点" className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{required.map((candidate) => <li key={candidate.place.id} className="min-w-0 rounded-xl border border-[#315f51]/25 bg-white p-4">
          <span className="mb-2 inline-block rounded-full bg-[#18392f] px-2.5 py-1 text-xs font-semibold text-white">必去</span>
          <PlaceDetails place={candidate.place} />
          <SearchSources candidate={candidate} />
        </li>)}</ul>}
    </div>
    <div className="mt-6">
      <h3 className="text-sm font-semibold text-[#18392f]">可选地点 · {optional.length} 个{excludedCount > 0 ? `（已排除 ${excludedCount} 个）` : ""}</h3>
      <p className="mt-1 text-xs leading-5 text-[#68726c]">排除或恢复只调整这份清单，不会重新搜索或查询路线。类别为地点服务原始分类。</p>
      {optional.length > 0 && <ul aria-label="可选候选地点" className="mt-3 grid max-h-[34rem] gap-3 overflow-y-auto pr-1 sm:grid-cols-2 lg:grid-cols-3">{optional.map((candidate) => {
        const excluded = excludedIds.has(candidate.place.id);
        return <li key={candidate.place.id} className={`flex min-w-0 flex-col rounded-xl border p-4 ${excluded ? "border-[#18201d]/10 bg-[#eeefeb]" : "border-[#18201d]/15 bg-white"}`}>
          <span className="mb-2 text-xs font-semibold text-[#315f51]">可选 · {excluded ? "已排除" : "保留中"}</span>
          <PlaceDetails place={candidate.place} />
          <SearchSources candidate={candidate} />
          <button type="button" aria-label={`${excluded ? "恢复" : "排除"}可选地点：${candidate.place.name}`}
            onClick={() => excluded ? pool.restore(candidate.place.id) : pool.exclude(candidate.place.id)}
            className="mt-4 min-h-10 w-full rounded-lg border border-[#315f51]/30 px-3 py-2 text-sm font-semibold text-[#315f51] focus-visible:outline-2 focus-visible:outline-offset-2">
            {excluded ? "恢复" : "排除"}
          </button>
        </li>;
      })}</ul>}
    </div>
  </section>;
}
