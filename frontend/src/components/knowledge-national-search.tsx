"use client";

import { type KnowledgeVenueKind } from "@/lib/knowledge-national";
import { useNationalKnowledge } from "@/lib/use-national-knowledge";

const field = "h-12 w-full min-w-0 rounded-xl border border-[#e1e6e3] bg-white px-3 text-sm text-[#17211f] outline-none focus:border-[#294e43] focus:ring-2 focus:ring-[#294e43]/10 disabled:opacity-50";
const focus = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#294e43]/40 focus-visible:ring-offset-4";
const sourceKinds = { standing: "长期服务资料", temporary: "阶段性资料", historical: "历史资料", catalog: "服务目录" };

export default function KnowledgeNationalSearch({ active = true }: { active?: boolean }) {
  const { input, state, directory, update, query, cancel, loadCatalog } = useNationalKnowledge(active);
  const catalog = directory.catalog;
  const venues = catalog?.venues.filter(row => row.city_id === input.city_id && (!input.kind || row.kind === input.kind)) ?? [];
  return <main className="mx-auto w-full max-w-5xl px-5 py-10 text-[#17211f] sm:px-10 sm:py-14">
    <header>
      <p className="text-xs font-medium tracking-[0.18em] text-[#74817c]">REFERENCE LIBRARY</p>
      <h1 className="mt-3 text-3xl font-semibold tracking-tight sm:text-4xl">知识库</h1>
      <p className="mt-3 text-sm leading-7 text-[#68756f]">查阅已收录城市的博物馆与游乐园官方资料。</p>
    </header>
    {directory.status === "loading" && <p role="status" className="mt-6 text-sm text-[#68756f]">正在读取本地资料目录…</p>}
    {directory.status === "failed" && <div className="mt-6 text-sm leading-7 text-[#945746]"><p role="alert">{directory.message}</p><button type="button" onClick={() => void loadCatalog()} className={`mt-2 min-h-10 rounded-lg px-2 text-[#294e43] underline ${focus}`}>重新加载目录</button></div>}
    <form className="mt-8 space-y-4" onSubmit={event => { event.preventDefault(); void query(); }}>
      <div className="grid gap-3 sm:grid-cols-3">
        <label className="grid min-w-0 gap-2 text-xs font-medium text-[#68756f]">城市
          <select required className={field} value={input.city_id} disabled={!catalog} onChange={event => update({ city_id: event.target.value })}>
            <option value="">选择已收录城市</option>{catalog?.cities.map(city => <option key={city.id} value={city.id}>{city.name}</option>)}
          </select>
        </label>
        <label className="grid min-w-0 gap-2 text-xs font-medium text-[#68756f]">场馆类型
          <select className={field} value={input.kind} disabled={!catalog} onChange={event => update({ kind: event.target.value as KnowledgeVenueKind | "" })}>
            <option value="">全部类型</option><option value="museum">博物馆</option><option value="amusement_park">游乐园</option>
          </select>
        </label>
        <label className="grid min-w-0 gap-2 text-xs font-medium text-[#68756f]">场馆／馆区
          <select required className={field} value={input.venue_id} disabled={!catalog || !input.city_id} onChange={event => update({ venue_id: event.target.value })}>
            <option value="">选择具体场馆</option>{venues.map(venue => <option key={venue.id} value={venue.id}>{venue.name}</option>)}
          </select>
        </label>
      </div>
      <div className="grid items-end gap-3 sm:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)_auto]">
        <label className="grid min-w-0 gap-2 text-xs font-medium text-[#68756f]">想查询的问题
          <input required minLength={2} maxLength={160} className={field} value={input.question} onChange={event => update({ question: event.target.value })} placeholder="预约、开放时间、儿童参观…" />
        </label>
        <label className="grid min-w-0 gap-2 text-xs font-medium text-[#68756f]">参观日期（可选）
          <input type="date" className={field} value={input.visit_date} onChange={event => update({ visit_date: event.target.value })} />
        </label>
        <button disabled={!catalog || state.status === "loading"} className={`h-12 rounded-xl bg-[#294e43] px-6 text-sm font-medium text-white hover:bg-[#203f36] disabled:cursor-not-allowed disabled:opacity-50 ${focus}`}>{state.status === "loading" ? "正在检索…" : "查询资料"}</button>
      </div>
    </form>
    <div className="mt-4 flex flex-wrap items-start justify-between gap-x-5 gap-y-3">
      <details className="max-w-2xl text-xs leading-6 text-[#74817c]">
        <summary className={`w-fit cursor-pointer rounded-sm ${focus}`}>收录范围与使用提示</summary>
        <div className="mt-3 space-y-2 border-l border-[#dce3de] pl-4">
          <p>{catalog ? `当前收录${catalog.cities.length}个城市、${catalog.venues.length}个场馆，${catalog.document_count}份资料、${catalog.chunk_count}个主题片段。语料版本${catalog.corpus_version}。` : "目录加载后可查看实际已收录城市和场馆。"}仅开放清单中的资料，不代表全国场馆已穷尽覆盖；图书馆尚未收录。</p>
          <p>只检索本地已核对资料，不实时搜索官网。每次最多5条，匹配顺序不是可信度百分比；不提供实时余票。</p>
          <p>相关资料不等于问题已获回答，不保证参观当天适用。AI问答暂未开放，请核对资料条件和官方原文。</p>
          <button type="button" disabled={directory.status === "loading"} onClick={() => void loadCatalog()} className={`min-h-10 rounded-sm text-[#294e43] underline ${focus}`}>重新加载资料目录</button>
        </div>
      </details>
      {state.status === "loading" && <button type="button" onClick={cancel} className={`min-h-10 rounded-md px-2 text-xs font-medium text-[#294e43] underline underline-offset-4 ${focus}`}>取消查询</button>}
    </div>
    {state.message && <p role={state.status === "failed" ? "alert" : "status"} className="mt-7 border-l-2 border-[#9baea3] pl-4 text-sm leading-7 text-[#68756f]">{state.message}</p>}
    {!state.response && state.status === "idle" && !state.message && <section aria-label="开始资料查询" className="mt-12 border-t border-[#e4e9e5] py-14 text-center sm:py-20">
      <h2 className="text-sm font-medium">从一个具体场馆开始</h2>
      <p className="mx-auto mt-2 max-w-sm text-xs leading-6 text-[#839087]">选择城市与场馆，主动查询参观资料；未命中不代表没有限制。</p>
    </section>}
    {state.response && <section aria-label="资料检索结果" className="mt-10">
      <header className="border-b border-[#e4e9e5] pb-5">
        <div className="flex flex-wrap items-baseline justify-between gap-3"><h2 className="text-base font-semibold">{state.response.city.name} · {state.response.venue.name}</h2><p className="text-xs text-[#839087]">{state.response.matches.length}条相关资料</p></div>
        <p className="mt-2 break-words text-xs leading-6 text-[#74817c]">本次问题：{state.response.request.question}{state.response.request.visit_date ? ` · 参观日期：${state.response.request.visit_date}` : " · 未指定参观日期"}</p>
      </header>
      {state.response.status === "no_match" ? <div role="status" className="py-14 text-center"><p className="text-sm font-medium">未找到相关资料</p><p className="mt-2 text-xs leading-6 text-[#74817c]">可换一种表述或查阅馆方官网；没有资料不能据此确认免费、无需预约或可以入场。</p></div> : <>
        <p className="mt-4 text-xs leading-6 text-[#839087]">以下是整理后的资料及短摘录，不是AI回答，也不是实时核验。</p>
        <ol className="divide-y divide-[#e4e9e5]">{state.response.matches.map((match, index) => <li key={match.chunk_id} className="min-w-0 py-7 sm:py-8">
          <p className="text-[11px] font-medium tracking-wide text-[#839087]">{String(index + 1).padStart(2, "0")} / {sourceKinds[match.source.kind]}</p>
          <h3 className="mt-2 break-words text-lg font-semibold tracking-tight">{match.section.heading}</h3><p className="mt-1 break-words text-xs leading-6 text-[#74817c]">{match.source.title}</p>
          <div className="mt-5"><p className="text-[11px] font-medium text-[#839087]">整理摘要</p><p className="mt-2 whitespace-pre-line break-words text-sm leading-7 text-[#36463e]">{match.section.summary}</p></div>
          {match.section.excerpt && <blockquote className="mt-4 border-l-2 border-[#d7e2da] pl-4 text-xs leading-6 text-[#68756f]"><p className="mb-1 text-[11px] font-medium text-[#839087]">原文短摘录</p>{match.section.excerpt}</blockquote>}
          <p className="mt-4 break-words text-xs leading-6 text-[#7b6444]">适用性提示：{match.applicability_check.notice}</p>
          <div className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-2">
            <a href={match.source.url} target="_blank" rel="noopener noreferrer" className={`inline-flex min-h-10 items-center gap-1.5 rounded-sm text-xs font-medium text-[#294e43] underline decoration-[#b5c5b9] underline-offset-4 ${focus}`}>打开官方原文<span className="sr-only">（新标签页）</span><span aria-hidden="true">↗</span></a>
            <details className="text-xs leading-6 text-[#74817c]"><summary className={`w-fit cursor-pointer rounded-sm ${focus}`}>来源日期与适用范围</summary>
              <div className="mt-3 max-w-2xl space-y-3 border-l border-[#dce3de] pl-4">
                <p className="break-words">{match.source.applicability}</p>
                <dl className="grid gap-1"><div><dt className="inline">来源机构：</dt><dd className="inline">{match.source.publisher}</dd></div><div><dt className="inline">来源发布／更新：</dt><dd className="inline">{match.source.published_at ?? "未知"} ／ {match.source.updated_at ?? "未知"}</dd></div><div><dt className="inline">注明生效／失效：</dt><dd className="inline">{match.source.effective_from ?? "未知"} ／ {match.source.effective_until ?? "未知"}</dd></div><div><dt className="inline">实际获取：</dt><dd className="inline">{match.source.fetched_at}（不是重新发布）</dd></div></dl>
                <p className="break-all text-[11px] leading-6">片段{match.chunk_id} · 内容版本{match.source.content_version}<br />SHA256 {match.content_hash}</p><p className="text-[11px] leading-6">{match.source.reuse_basis}</p>
              </div>
            </details>
          </div>
        </li>)}</ol>
      </>}
    </section>}
  </main>;
}
