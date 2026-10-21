"use client";

import { venues } from "@/lib/knowledge";
import { useKnowledgeSearch } from "@/lib/use-knowledge-search";

const kinds = { standing: "长期服务资料", temporary: "阶段性须知", historical: "历史公告", catalog: "课程目录" };
const field = "h-12 w-full rounded-xl border border-[#e1e6e3] bg-white px-4 text-sm text-[#17211f] outline-none transition focus:border-[#294e43] focus:ring-2 focus:ring-[#294e43]/10";
const focus = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#294e43]/40 focus-visible:ring-offset-4";

export default function KnowledgeSearch() {
  const { input, state, update, query, cancel } = useKnowledgeSearch();
  return (
    <main className="mx-auto w-full max-w-5xl px-5 py-10 text-[#17211f] sm:px-10 sm:py-14">
      <header>
        <p className="text-xs font-medium tracking-[0.18em] text-[#74817c]">REFERENCE LIBRARY</p>
        <h1 className="mt-3 text-3xl font-semibold tracking-tight sm:text-4xl">知识库</h1>
        <p className="mt-3 text-sm leading-7 text-[#68756f]">查阅官方资料，核对出行事项。</p>
      </header>
      <form className="mt-8 grid items-end gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.6fr)_auto]" onSubmit={event => { event.preventDefault(); void query(); }}>
        <label className="grid min-w-0 gap-2 text-xs font-medium text-[#68756f]">
          场馆／馆区
          <select required className={field} value={input.venue_id} onChange={event => update({ venue_id: event.target.value })}>
            <option value="">选择馆区</option>{venues.map(venue => <option key={venue.id} value={venue.id}>{venue.name}</option>)}
          </select>
        </label>
        <label className="grid min-w-0 gap-2 text-xs font-medium text-[#68756f]">
          想查询的问题
          <input required minLength={2} maxLength={160} className={field} value={input.question} onChange={event => update({ question: event.target.value })} placeholder="预约、开放时间、亲子服务…" />
        </label>
        <button disabled={state.status === "loading"} className={`h-12 rounded-xl bg-[#294e43] px-6 text-sm font-medium text-white transition hover:bg-[#203f36] disabled:cursor-wait disabled:opacity-50 ${focus}`}>{state.status === "loading" ? "正在检索…" : "查询资料"}</button>
      </form>
      <div className="mt-4 flex flex-wrap items-start justify-between gap-x-5 gap-y-3">
        <details className="max-w-2xl text-xs leading-6 text-[#74817c]">
          <summary className={`w-fit cursor-pointer rounded-sm ${focus}`}>收录范围与使用提示</summary>
          <div className="mt-3 space-y-2 border-l border-[#dce3de] pl-4">
            <p>目前为上海博物馆人民广场馆、东馆及上海自然博物馆：6份官方资料，15个主题片段。版本2026-10-14.1。图书馆、游乐园及其他地区尚未收录。</p>
            <p>仅检索本地资料，不在线搜索。每次最多5条，文字匹配顺序不是可信度评分；不含实时余票或完整活动安排。</p>
            <p>相关资料不等于问题已获回答，不保证今天适用；请核对适用对象、例外和日期。AI问答暂未开放，可查阅资料及官方原文。</p>
          </div>
        </details>
        {state.status === "loading" && <button type="button" onClick={cancel} className={`min-h-9 rounded-md px-2 text-xs font-medium text-[#294e43] underline underline-offset-4 ${focus}`}>取消查询</button>}
      </div>
      {state.message && <p role={state.status === "failed" ? "alert" : "status"} className="mt-7 border-l-2 border-[#9baea3] pl-4 text-sm leading-7 text-[#68756f]">{state.message}</p>}
      {!state.response && state.status === "idle" && !state.message && <section aria-label="开始资料查询" className="mt-12 border-t border-[#e4e9e5] py-14 text-center sm:py-20">
        <div aria-hidden="true" className="mx-auto flex h-11 w-11 items-center justify-center rounded-2xl bg-[#edf1ee] text-[#597164]">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M4 4.5h6a3 3 0 0 1 3 3V21a4 4 0 0 0-4-2H4z" /><path d="M20 4.5h-4a3 3 0 0 0-3 3M20 4.5V19h-3a4 4 0 0 0-4 2" /></svg>
        </div>
        <h2 className="mt-5 text-sm font-medium">从一个具体问题开始</h2>
        <p className="mx-auto mt-2 max-w-xs text-xs leading-6 text-[#839087]">选择馆区，查询预约、参观或亲子服务资料。</p>
      </section>}
      {state.response && <section aria-label="资料检索结果" className="mt-10">
        <header className="border-b border-[#e4e9e5] pb-5">
          <div className="flex flex-wrap items-baseline justify-between gap-3"><h2 className="text-base font-semibold">{state.response.venue.name}</h2><p className="text-xs text-[#839087]">{state.response.matches.length}条相关资料</p></div>
          <p className="mt-2 break-words text-xs leading-6 text-[#74817c]">本次问题：{state.response.request.question}</p>
        </header>
        {state.response.status === "no_match" ? <div role="status" className="py-14 text-center"><p className="text-sm font-medium">未找到相关资料</p><p className="mt-2 text-xs leading-6 text-[#74817c]">可以换一种表述，或查阅馆方官网；未命中不代表没有限制。</p></div> : <>
          <p className="mt-4 text-xs leading-6 text-[#839087]">以下是相关资料，不是已核实的答案；请核对适用范围与日期。</p>
          <ol className="divide-y divide-[#e4e9e5]">{state.response.matches.map((match, index) => <li key={match.chunk_id} id={`knowledge-${match.chunk_id.replace("/", "-")}`} tabIndex={-1} className="min-w-0 scroll-mt-6 py-7 sm:py-8">
            <p className="text-[11px] font-medium tracking-wide text-[#839087]">{String(index + 1).padStart(2, "0")} / {kinds[match.source.kind]}</p>
            <h3 className="mt-2 text-lg font-semibold tracking-tight">{match.section.heading}</h3><p className="mt-1 text-xs leading-6 text-[#74817c]">{match.source.title}</p>
            <div className="mt-5"><p className="text-[11px] font-medium text-[#839087]">整理摘要</p><p className="mt-2 break-words text-sm leading-7 text-[#36463e]">{match.section.summary}</p></div>
            {match.section.excerpt && <blockquote className="mt-4 border-l-2 border-[#d7e2da] pl-4 text-xs leading-6 text-[#68756f]"><p className="mb-1 text-[11px] font-medium text-[#839087]">原文摘录</p>{match.section.excerpt}</blockquote>}
            <div className="mt-5 flex flex-wrap items-center gap-x-6 gap-y-2">
              <a href={match.source.url} target="_blank" rel="noopener noreferrer" className={`inline-flex min-h-10 items-center gap-1.5 rounded-sm text-xs font-medium text-[#294e43] underline decoration-[#b5c5b9] underline-offset-4 ${focus}`}>打开官方原文<span className="sr-only">（新标签页）</span><span aria-hidden="true">↗</span></a>
              <details className="text-xs leading-6 text-[#74817c]">
                <summary className={`w-fit cursor-pointer rounded-sm ${focus}`}>日期与适用性</summary>
                <div className="mt-3 max-w-2xl space-y-3 border-l border-[#dce3de] pl-4">
                  <p className="break-words">适用性需核实：{match.source.applicability}</p>
                  <dl className="grid gap-1"><div><dt className="inline">来源机构：</dt><dd className="inline">{match.source.publisher}</dd></div><div><dt className="inline">来源发布／更新：</dt><dd className="inline">{match.source.published_at ?? "未知"} ／ {match.source.updated_at ?? "未知"}</dd></div><div><dt className="inline">注明生效／失效：</dt><dd className="inline">{match.source.effective_from ?? "未知"} ／ {match.source.effective_until ?? "未知"}</dd></div><div><dt className="inline">实际获取：</dt><dd className="inline">{match.source.fetched_at}（不代表重新发布）</dd></div></dl>
                  <p className="break-all text-[11px] leading-6">片段{match.chunk_id} · 内容版本{match.source.content_version}<br />SHA256 {match.content_hash}</p><p className="text-[11px] leading-6">{match.source.reuse_basis}</p>
                </div>
              </details>
            </div>
          </li>)}</ol>
        </>}
      </section>}
    </main>
  );
}
