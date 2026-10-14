"use client";
import type { useKnowledgeAnswer } from "@/lib/use-knowledge-answer";
import { missingLabels } from "@/lib/knowledge-answer";

export default function KnowledgeAnswer({ answer }: { answer: ReturnType<typeof useKnowledgeAnswer> }) {
  const { state, generate, cancel } = answer, r = state.response;
  return <section aria-label="依据资料回答" className="my-6 rounded-2xl border border-[#315f51]/30 bg-[#f2f7f3] p-5">
    <h3 className="text-lg font-semibold">依据资料回答</h3>
    <p className="mt-2 text-sm leading-6">仅点击后生成。AI 只依据本次最多 5 个片段；检索命中不等于可以回答，不影响正式行程。</p>
    <div className="mt-3 flex flex-wrap gap-3"><button type="button" disabled={state.status === "loading"} onClick={() => void generate()} className="min-h-11 rounded-full bg-[#315f51] px-5 py-3 text-white disabled:opacity-50">{state.status === "loading" ? "正在依据资料回答…" : r ? "重新生成回答" : "依据资料生成回答"}</button>
      {state.status === "loading" && <button type="button" onClick={cancel} className="min-h-11 rounded-full border px-5">取消回答</button>}</div>
    {state.message && <p role={state.status === "failed" ? "alert" : "status"} className="mt-4 leading-7">{state.message}</p>}
    {r && <div className="mt-4" aria-live="polite">
      <p className="font-semibold" role="status">{r.status === "answered" ? "已依据收录资料回答" : r.status === "partial" ? "部分有依据，仍有事项待确认" : "资料不足，无法确认"}</p>
      {r.origin === "local_no_match" && <p className="mt-2 text-sm">未匹配到片段，本次未调用模型。</p>}
      <ol className="mt-3 space-y-4">{r.answers.map((a, i) => <li key={i}><p className="break-words leading-7">{a.text}</p><div className="mt-1 flex flex-wrap gap-3">{a.evidence_ids.map(id => {
        const c = r.citations.find(c => c.evidence_id === id)!;
        return <a key={id} className="min-h-11 py-2 font-semibold underline" href={`#knowledge-${c.chunk_id.replace("/", "-")}`}>依据 {id} · 查看资料片段</a>;
      })}</div></li>)}</ol>
      {r.missing.length > 0 && <ul className="mt-3 space-y-2 text-sm">{r.missing.map(code => <li key={code}>{missingLabels[code]}</li>)}</ul>}
      <p className="mt-4 text-sm leading-6">适用提示：这是已收录资料的说明，不保证今天的运营状态。获取日期不是发布或更新时间；原文摘录与整理摘要请见下方资料卡。引用身份已校验不代表结论已获完整语义验证。</p>
      <details className="mt-3 text-xs"><summary className="min-h-11 cursor-pointer py-2">回答来源与版本</summary><p className="break-all leading-6">{r.model ?? "本地判断"} · {r.prompt_version} · 语料 {r.retrieval.corpus_version} · SHA256 {r.retrieval.corpus_hash}</p></details>
    </div>}
  </section>;
}
