"use client";
import { venues } from "@/lib/knowledge";
import { useKnowledgeSearch } from "@/lib/use-knowledge-search";

const kinds = { standing: "长期服务页面", temporary: "阶段性参观须知", historical: "历史公告", catalog: "课程目录" };
const box = "w-full rounded-xl border border-[#315f51]/30 bg-white p-3";
export default function KnowledgeSearch() {
  const { input, state, update, query, cancel } = useKnowledgeSearch();
  return <main className="mx-auto max-w-4xl px-4 py-10 text-[#18392f] sm:px-8">
    <p className="text-sm font-semibold">TripPilot · 独立资料页</p>
    <h1 className="mt-3 text-3xl font-semibold">旅行资料查询</h1>
    <p className="mt-4 leading-7">检索已收录的官方资料，不在线搜索网站，也不改变您的行程。相关资料不等于问题已获回答，不保证今天适用。</p>
    <p className="mt-2 text-sm leading-6">AI问答暂未开放，可查阅以下资料及官方原文。</p>
    <details className="mt-4 rounded-xl bg-white p-4"><summary className="cursor-pointer font-semibold">收录范围与使用提示</summary>
      <p className="mt-3 leading-7">目前收录上博人民广场馆、东馆及上海自然博物馆的 6 份官方资料，整理为 15 个主题片段。版本：2026-10-14.1。请明确选择馆区，“上博”不会自动指向某一馆区。</p>
      <p className="mt-2 leading-7">包含参观、团队预约和亲子服务；不含实时余票或完整活动安排。获取时间不是发布时间。历史公告、无更新日期及来源差异均需核实，相关文字不等于当前适用或可入馆保证。</p>
    </details>
    <form className="mt-7 grid gap-4 rounded-2xl border border-[#315f51]/20 p-5" onSubmit={e => { e.preventDefault(); void query(); }}>
      <label>场馆／馆区<select required className={`${box} mt-2`} value={input.venue_id} onChange={e => update({ venue_id: e.target.value })}><option value="">请明确选择</option>{venues.map(v => <option key={v.id} value={v.id}>{v.name}</option>)}</select></label>
      <label>想查询的问题<input required minLength={2} maxLength={160} className={`${box} mt-2`} value={input.question} onChange={e => update({ question: e.target.value })} placeholder="例如：婴儿车怎么借？" /></label>
      <p className="text-sm">最多 160 字，每次最多显示 5 条相关片段；排序是文字匹配，不是可信度评分。</p>
      <div className="flex flex-wrap gap-3"><button disabled={state.status === "loading"} className="min-h-11 rounded-full bg-[#315f51] px-6 py-3 text-white disabled:opacity-50">{state.status === "loading" ? "正在检索…" : "查询资料"}</button>{state.status === "loading" && <button type="button" onClick={cancel} className="min-h-11 rounded-full border px-6">取消查询</button>}</div>
    </form>
    {state.message && <p role={state.status === "failed" ? "alert" : "status"} className="mt-5">{state.message}</p>}
    {state.response && <section aria-label="资料检索结果" className="mt-8">
      <h2 className="text-xl font-semibold">{state.response.venue.name} · 检索结果</h2>
      <p className="mt-2 break-words text-sm">本次问题：{state.response.request.question}</p>
      {state.response.status === "no_match" ? <p role="status" className="mt-5 rounded-xl bg-white p-5">未找到相关资料。可换一种表述，或到馆方官网核实；未命中不代表没有限制。</p> : <p className="mt-3 text-sm leading-6">以下仅为相关资料，不是问题答案。请同时核对适用对象、例外和日期；有相关文字仍可能不足以回答您的问题。</p>}
      <ol className="mt-4 grid gap-5">{state.response.matches.map((m, i) => <li key={m.chunk_id} id={`knowledge-${m.chunk_id.replace("/", "-")}`} tabIndex={-1} className="min-w-0 scroll-mt-5 rounded-2xl border border-[#315f51]/20 bg-white p-5">
        <p className="text-sm text-[#77522b]">{i + 1} · {kinds[m.source.kind]} · {state.response!.venue.name}</p>
        <h3 className="mt-2 text-lg font-semibold">{m.source.title}</h3><p className="mt-1 text-sm">章节：{m.section.heading}</p>
        <p className="mt-4 break-words leading-7">{m.section.summary}</p>
        {m.section.excerpt && <blockquote className="mt-3 border-l-2 pl-3 text-sm">来源短摘录：{m.section.excerpt}</blockquote>}
        <p className="mt-3 rounded-lg bg-[#faf3e6] p-3 text-sm leading-6">适用性需核实：{m.source.applicability}</p>
        <dl className="mt-4 grid gap-2 text-sm"><div><dt className="inline">来源机构：</dt><dd className="inline">{m.source.publisher}</dd></div>
          <div><dt className="inline">来源发布／更新：</dt><dd className="inline">{m.source.published_at ?? "未知"} ／ {m.source.updated_at ?? "未知"}</dd></div>
          <div><dt className="inline">来源注明生效／失效：</dt><dd className="inline">{m.source.effective_from ?? "未知"} ／ {m.source.effective_until ?? "未知"}</dd></div>
          <div><dt className="inline">实际获取：</dt><dd className="inline">{m.source.fetched_at}（不代表重新发布）</dd></div></dl>
        <a href={m.source.url} target="_blank" rel="noopener noreferrer" className="mt-4 inline-block min-h-11 break-words py-2 font-semibold underline">打开官方原文（新标签页）</a>
        <details className="mt-2 text-xs"><summary className="cursor-pointer">追溯与整理说明</summary><p className="mt-2 break-all leading-6">片段 {m.chunk_id}；内容版本 {m.source.content_version}；SHA256 {m.content_hash}。{m.source.reuse_basis}</p></details>
      </li>)}</ol>
    </section>}
  </main>;
}
