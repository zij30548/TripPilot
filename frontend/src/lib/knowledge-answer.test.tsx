import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { act, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { isAnswerResponse, makeAnswerRequest, type AnswerResponse } from "./knowledge-answer";
import { answerKnowledge } from "./knowledge-answer-api";
import { useKnowledgeSearch } from "./use-knowledge-search";
import KnowledgeSearch from "@/components/knowledge-search";

const backend = resolve(process.cwd(), "../backend");
const fixtures = JSON.parse(execFileSync(resolve(backend, ".venv/bin/python"), ["-B", "-m", "tests.export_knowledge_answer_contract"], { cwd: backend, encoding: "utf8" })) as AnswerResponse[];
const fixture = (i = 0) => structuredClone(fixtures[i]);
const response = (v: unknown, status = 200) => ({ ok: status === 200, status, text: async () => JSON.stringify(v) }) as Response;
const deferred = () => { let resolve!: (v: Response) => void, reject!: (e: Error) => void; const promise = new Promise<Response>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
let fetcher: ReturnType<typeof vi.fn>;
beforeEach(() => { fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher); });
function answerForLast(i = 0) { const r = fixture(i); r.request = JSON.parse(fetcher.mock.calls.at(-1)![1].body); return r; }
async function search(h: { current: ReturnType<typeof useKnowledgeSearch> }, i = 0) {
  const r = fixture(i).retrieval; act(() => h.current.update(r.request)); fetcher.mockResolvedValueOnce(response(r));
  await act(() => h.current.query());
}

describe("Python → TS answer and citation contract", () => {
  it("accepts real Python rendered answered/partial/insufficient/local no-match", () => {
    for (const r of fixtures) expect(isAnswerResponse(r, r.request, r.retrieval)).toBe(true);
  });
  it.each([
    (r: AnswerResponse) => Object.assign(r, { extra: "bad" }),
    (r: AnswerResponse) => { r.citations[0].url = "https://evil.invalid"; },
    (r: AnswerResponse) => { r.citations[0].source_version = "old"; },
    (r: AnswerResponse) => { r.citations[0].content_hash = "0".repeat(64); },
    (r: AnswerResponse) => { r.citations[0].source_id = "museum-west"; },
    (r: AnswerResponse) => { r.answers[0].evidence_ids = []; },
    (r: AnswerResponse) => { r.answers[0].evidence_ids = ["E5"]; },
    (r: AnswerResponse) => { r.answers[0].evidence_ids = ["E1", "E1"]; },
    (r: AnswerResponse) => { r.answers[0].text = "<script>bad</script>"; },
    (r: AnswerResponse) => { r.answers[0].text = "字".repeat(181); },
    (r: AnswerResponse) => { r.answers[0].text = "已收录资料说明须预约[1]。"; },
    (r: AnswerResponse) => { r.answers[0].text = "已收录资料说明须预约【1】。"; },
    (r: AnswerResponse) => { r.answers[0].text = "依据E9须预约。"; },
    (r: AnswerResponse) => { r.answers[0].text = "依据E1须预约。"; },
    (r: AnswerResponse) => { r.answers[0].text = `需预约（来源：${r.retrieval.matches[0].source.id}）。`; },
    (r: AnswerResponse) => { r.retrieval.matches[0].source.venue_id = "natural"; },
    (r: AnswerResponse) => { r.retrieval.corpus_hash = "0".repeat(64); },
    (r: AnswerResponse) => { r.retrieval.matches[0].section.summary = "invented"; },
    (r: AnswerResponse) => { r.usage.output_tokens = 1025; },
    (r: AnswerResponse) => { r.status = "insufficient"; },
    (r: AnswerResponse) => { r.missing = ["live_availability"]; },
    (r: AnswerResponse) => { r.citations = []; },
  ])("rejects entire invalid result %#", change => {
    const r = fixture(); change(r); expect(isAnswerResponse(r, fixture().request, fixture().retrieval)).toBe(false);
  });
  it("request projection never transmits client sources, URL or prompt", async () => {
    const r = fixture(); fetcher.mockResolvedValueOnce(response(r));
    await answerKnowledge(r.retrieval, r.request.request_id, new AbortController().signal);
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual(makeAnswerRequest(r.retrieval, r.request.request_id));
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it("safe stale version errors and no retry", async () => {
    fetcher.mockResolvedValue(response({ detail: "private upstream" }, 409));
    await expect(answerKnowledge(fixture().retrieval, "x", new AbortController().signal)).rejects.toThrow("重新检索");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it("timeout 25s and no retry", async () => {
    vi.useFakeTimers(); fetcher.mockImplementation((_u, init) => new Promise((_y, no) => init.signal.addEventListener("abort", () => no(new Error("private")))));
    const attempt = answerKnowledge(fixture().retrieval, "x", new AbortController().signal).catch(e => e.message);
    await vi.advanceTimersByTimeAsync(25000); expect(await attempt).toContain("回答超时"); expect(fetcher).toHaveBeenCalledTimes(1); vi.useRealTimers();
  });
  it("truncated/invalid response and late timeout response cannot pass", async () => {
    fetcher.mockResolvedValueOnce({ ok: true, status: 200, text: async () => '{"status":' });
    await expect(answerKnowledge(fixture().retrieval, "x", new AbortController().signal)).rejects.toThrow("格式检查失败");
    vi.useFakeTimers(); const d = deferred(); fetcher.mockReturnValueOnce(d.promise);
    const attempt = answerKnowledge(fixture().retrieval, "offline-1", new AbortController().signal).catch(e => e.message);
    await vi.advanceTimersByTimeAsync(25000); d.resolve(response(fixture())); expect(await attempt).toContain("回答超时"); vi.useRealTimers();
  });
});

describe("explicit answer lifecycle, current source preserved", () => {
  it("search and edits never call model; duplicate generation ignored", async () => {
    const { result } = renderHook(useKnowledgeSearch); await search(result);
    expect(fetcher).toHaveBeenCalledTimes(1); const d = deferred(); fetcher.mockReturnValueOnce(d.promise);
    let p!: Promise<void>; act(() => { p = result.current.answer.generate(); void result.current.answer.generate(); });
    expect(fetcher).toHaveBeenCalledTimes(2);
    await act(async () => { d.resolve(response(answerForLast())); await p; }); expect(result.current.answer.state.status).toBe("success");
    act(() => result.current.update({ question: "new question" })); expect(result.current.answer.state.response).toBeNull(); expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it.each(["success", "error"])("ABA isolates stale %s, finally and old cancel callback", async outcome => {
    const { result } = renderHook(useKnowledgeSearch); await search(result); const a = deferred(); fetcher.mockReturnValueOnce(a.promise);
    let pa!: Promise<void>; act(() => { pa = result.current.answer.generate(); }); const oldCancel = result.current.answer.cancel, oldGenerate = result.current.answer.generate;
    const old = answerForLast(); act(() => result.current.update({ venue_id: "natural" })); await search(result);
    const b = deferred(); fetcher.mockReturnValueOnce(b.promise); let pb!: Promise<void>;
    act(() => { pb = result.current.answer.generate(); oldCancel(); void oldGenerate(); }); const newest = answerForLast();
    expect(fetcher).toHaveBeenCalledTimes(4);
    await act(async () => { if (outcome === "success") a.resolve(response(old)); else a.reject(new Error("old")); await pa; });
    expect(result.current.answer.state.status).toBe("loading"); act(() => { void result.current.answer.generate(); }); expect(fetcher).toHaveBeenCalledTimes(4);
    await act(async () => { b.resolve(response(newest)); await pb; }); expect(result.current.answer.state.response?.request.request_id).toBe(newest.request.request_id);
  });
  it("cancel keeps retrieval, no automatic regeneration; unmount rejects late result", async () => {
    const { result, unmount } = renderHook(useKnowledgeSearch); await search(result); const d = deferred(); fetcher.mockReturnValueOnce(d.promise);
    let p!: Promise<void>; act(() => { p = result.current.answer.generate(); }); const output = answerForLast();
    act(() => result.current.answer.cancel()); expect(result.current.state.response).toEqual(fixture().retrieval);
    expect(result.current.answer.state.message).toContain("已取消"); const saved = result.current.answer.generate; unmount();
    await act(async () => { d.resolve(response(output)); await p; await saved(); }); expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it("new search invalidates answer before search response; failure preserves current retrieval", async () => {
    const { result } = renderHook(useKnowledgeSearch); await search(result);
    fetcher.mockRejectedValueOnce(new Error("private")); await act(() => result.current.answer.generate());
    expect(result.current.state.response).toEqual(fixture().retrieval); expect(result.current.answer.state.status).toBe("failed");
    const d = deferred(); fetcher.mockReturnValueOnce(d.promise); let p!: Promise<void>; act(() => { p = result.current.query(); });
    expect(result.current.answer.state.status).toBe("idle"); expect(result.current.answer.state.response).toBeNull();
    await act(async () => { d.resolve(response(fixture().retrieval)); await p; }); expect(fetcher).toHaveBeenCalledTimes(3);
  });
  it("changed corpus binding invalidates old answer even with same question", async () => {
    const { result } = renderHook(useKnowledgeSearch); await search(result); const d = deferred(); fetcher.mockReturnValueOnce(d.promise);
    let p!: Promise<void>; act(() => { p = result.current.answer.generate(); }); const old = answerForLast();
    const changed = fixture().retrieval; changed.corpus_hash = "a".repeat(64);
    act(() => result.current.answer.bind(changed)); await act(async () => { d.resolve(response(old)); await p; });
    expect(result.current.answer.state.response).toBeNull();
  });
  it("component renders insufficiency, normal answer and citation anchors without extra calls", async () => {
    render(<KnowledgeSearch />);
    fireEvent.change(screen.getByLabelText("场馆／馆区"), { target: { value: "museum_east" } });
    fireEvent.change(screen.getByLabelText("想查询的问题"), { target: { value: fixture().request.question } });
    fetcher.mockResolvedValueOnce(response(fixture().retrieval));
    await act(async () => fireEvent.submit(screen.getByRole("button", { name: "查询资料" }).closest("form")!));
    fetcher.mockImplementationOnce((_url, init) => Promise.resolve(response({ ...fixture(), request: JSON.parse(init.body) })));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "依据资料生成回答" })));
    const link = screen.getByRole("link", { name: /依据 E1/ }); expect(link.getAttribute("href")).toBe(`#knowledge-${fixture().citations[0].chunk_id.replace("/", "-")}`);
    fireEvent.click(link); expect(fetcher).toHaveBeenCalledTimes(2); expect(screen.getByText("已依据收录资料回答")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("场馆／馆区"), { target: { value: "museum_people" } });
    fireEvent.change(screen.getByLabelText("想查询的问题"), { target: { value: fixture(2).request.question } });
    fetcher.mockResolvedValueOnce(response(fixture(2).retrieval)); await act(async () => fireEvent.submit(screen.getByRole("button", { name: "查询资料" }).closest("form")!));
    fetcher.mockImplementationOnce((_url, init) => Promise.resolve(response({ ...fixture(2), request: JSON.parse(init.body) })));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "依据资料生成回答" })));
    expect(screen.getByText("资料不足，无法确认")).toBeTruthy(); expect(screen.queryByRole("alert")).toBeNull();
  });
});
