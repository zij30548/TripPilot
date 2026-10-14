import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { act, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { isKnowledgeRequest, isKnowledgeResponse, type KnowledgeRequest, type KnowledgeResponse } from "./knowledge";
import { searchKnowledge } from "./knowledge-api";
import { useKnowledgeSearch } from "./use-knowledge-search";
import KnowledgeSearch from "@/components/knowledge-search";

const backend = resolve(process.cwd(), "../backend");
const responses = JSON.parse(execFileSync(resolve(backend, ".venv/bin/python"), ["-B", "-c", `import json
from app.services.knowledge import build_index
from app.schemas.knowledge import KnowledgeRequest
index=build_index()
print(json.dumps([index.search(KnowledgeRequest(venue_id=v, question=q)).model_dump(mode="json") for v,q in [("museum_people","2025免预约"),("museum_east","数字馆预约"),("natural","化石挖掘"),("natural","迪士尼烟花"),("museum_people","2026特展购票"),("natural","学校团体预约")]],ensure_ascii=False))`], { cwd: backend, encoding: "utf8" })) as KnowledgeResponse[];
const fixture = (index = 0) => structuredClone(responses[index]);
const response = (data: unknown, ok = true) => ({ ok, text: async () => JSON.stringify(data) }) as Response;
const defer = () => { let resolve!: (v: Response) => void, reject!: (v: unknown) => void; const promise = new Promise<Response>((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
let fetcher: ReturnType<typeof vi.fn>;
beforeEach(() => { fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher); });

describe("actual Python → TypeScript local knowledge contract", () => {
  it("accepts three real-corpus campuses, missing dates, history, conflict and no result", () => {
    for (const result of responses) expect(isKnowledgeResponse(result, result.request)).toBe(true);
    expect(fixture().matches[0].source.kind).toBe("historical");
    expect(fixture(3).status).toBe("no_match");
  });
  it("rejects omitted/extra fields, unsafe sources, cross-campus refs, counts and malformed dates", () => {
    const changes: ((r: KnowledgeResponse) => void)[] = [
      r => { Object.assign(r, { extra: 1 }); }, r => { r.matches[0].source.url = "javascript:alert(1)"; },
      r => { r.matches[0].source.venue_id = "natural"; }, r => { r.matches[0].chunk_id = "fake/id"; },
      r => { r.matches[0].source.published_at = "2026-02-30"; }, r => { r.matches[0].score = NaN; },
      r => { r.matches.push(r.matches[0]); }, r => { r.request.question = "old"; },
      r => { r.status = "no_match"; }, r => { r.corpus_version = "next"; },
      r => { r.matches[0].source.updated_at = "2024-01-01"; },
    ];
    for (const change of changes) { const r = fixture(); change(r); expect(isKnowledgeResponse(r, fixture().request)).toBe(false); }
  });
  it("strict inputs require explicit campus and bounds", () => {
    const r = fixture().request;
    for (const patch of [{ venue_id: "上博" }, { question: "a" }, { question: "字".repeat(161) }, { limit: 6 }, { limit: true }, { url: "https://evil" }]) expect(isKnowledgeRequest({ ...r, ...patch })).toBe(false);
  });
  it("accepts reviewed per-source revisions, not arbitrary old or new versions", () => {
    for (const [index, wrongVersion] of [[0, "2026-10-13.1"], [1, "2026-10-14.1"]] as const) {
      const r = fixture(index);
      r.matches[0].source.content_version = wrongVersion;
      expect(isKnowledgeResponse(r, r.request)).toBe(false);
    }
    expect(fixture(1).corpus_version).toBe("2026-10-14.1");
    expect(fixture(1).matches[0].source.content_version).toBe("2026-10-13.1");
  });
});

describe("local request boundaries", () => {
  it("one POST only, exact input and no automatic retry on safe failures", async () => {
    fetcher.mockResolvedValueOnce(response(fixture()));
    const c = new AbortController(); await searchKnowledge(fixture().request, c.signal);
    expect(fetcher.mock.calls[0][0]).toBe("http://127.0.0.1:8000/knowledge/search");
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual(fixture().request);
    fetcher.mockRejectedValueOnce(new Error("secret raw URL"));
    await expect(searchKnowledge(fixture().request, c.signal)).rejects.toThrow("本地资料查询未完成");
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it("rejects malformed and oversized bodies without showing upstream text", async () => {
    for (const result of [response({ bad: true }), { ok: true, text: async () => "x".repeat(65537) }, response({ secret: true }, false)]) {
      fetcher.mockResolvedValueOnce(result);
      await expect(searchKnowledge(fixture().request, new AbortController().signal)).rejects.toThrow("格式异常");
    }
  });
  it("timeout is bounded and does not retry", async () => {
    vi.useFakeTimers();
    fetcher.mockImplementation((_url, init) => new Promise((_yes, no) => init.signal.addEventListener("abort", () => no(new Error("abort")))));
    const attempt = searchKnowledge(fixture().request, new AbortController().signal).catch(e => e.message);
    await vi.advanceTimersByTimeAsync(10000);
    expect(await attempt).toContain("查询超时"); expect(fetcher).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });
});

function input(h: ReturnType<typeof useKnowledgeSearch>, r: KnowledgeRequest) { h.update({ venue_id: r.venue_id, question: r.question }); }
describe("input/request version isolation and explicit interaction", () => {
  it("renders the 2025 cutoff and 2026 rule only with their respective source links", async () => {
    render(<KnowledgeSearch />);
    fireEvent.change(screen.getByLabelText("场馆／馆区"), { target: { value: "museum_people" } });
    for (const [index, id, supported, absent] of [[0, "people-2025/booking", "16:00", "15:00"], [4, "museum-west/entry", "15:00", "16:00"]] as const) {
      const data = fixture(index), match = data.matches.find(m => m.chunk_id === id)!;
      fireEvent.change(screen.getByLabelText("想查询的问题"), { target: { value: data.request.question } });
      fetcher.mockResolvedValueOnce(response(data));
      await act(async () => fireEvent.submit(screen.getByRole("button", { name: "查询资料" }).closest("form")!));
      const card = screen.getByText(match.section.summary).closest("li")!;
      expect(card.textContent).toContain(supported); expect(card.textContent).not.toContain(absent);
      expect(card.querySelector("a")?.getAttribute("href")).toBe(match.source.url);
      expect(match.source.fetched_at).toBe("2026-10-13T08:33:09Z");
      if (index === 0) {
        expect(card.textContent).toContain("不能直接用于当前入馆");
        expect(match.section.summary).not.toContain("2026");
      }
    }
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it("initial/edits make no queries, duplicate clicks are ignored; input change clears results", async () => {
    const { result } = renderHook(useKnowledgeSearch); const d = defer(); fetcher.mockReturnValueOnce(d.promise);
    act(() => input(result.current, fixture().request)); expect(fetcher).not.toHaveBeenCalled();
    let query!: Promise<void>; act(() => { query = result.current.query(); void result.current.query(); }); expect(fetcher).toHaveBeenCalledTimes(1);
    await act(async () => { d.resolve(response(fixture())); await query; }); expect(result.current.state.status).toBe("success");
    act(() => result.current.update({ question: "changed" })); expect(result.current.state.response).toBeNull(); expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it.each(["success", "error"])("ABA isolates old %s and finally, including same-event old query callback", async outcome => {
    const { result } = renderHook(useKnowledgeSearch); const a = defer(), b = defer(); fetcher.mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise);
    act(() => input(result.current, fixture().request));
    let old!: Promise<void>; act(() => { old = result.current.query(); }); const oldCancel = result.current.cancel, oldQuery = result.current.query;
    act(() => { input(result.current, fixture(1).request); input(result.current, fixture().request); void oldQuery(); });
    expect(fetcher).toHaveBeenCalledTimes(2); act(() => oldCancel()); expect(result.current.state.status).toBe("loading");
    await act(async () => { if (outcome === "success") a.resolve(response(fixture())); else a.reject(new Error("old secret")); await old; });
    expect(result.current.state.status).toBe("loading"); act(() => { void result.current.query(); }); expect(fetcher).toHaveBeenCalledTimes(2);
    await act(async () => { b.resolve(response(fixture())); }); expect(result.current.state.status).toBe("success");
  });
  it("cancel and unmount reject late results; no stale callback starts requests after unmount", async () => {
    const { result, unmount } = renderHook(useKnowledgeSearch); const d = defer(); fetcher.mockReturnValueOnce(d.promise);
    act(() => input(result.current, fixture().request)); let query!: Promise<void>; act(() => { query = result.current.query(); });
    act(() => result.current.cancel()); expect(result.current.state.message).toContain("取消");
    const oldQuery = result.current.query; unmount(); await act(async () => { d.resolve(response(fixture())); await query; await oldQuery(); }); expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it("failed then explicit retry; error message does not contain arbitrary network details", async () => {
    const { result } = renderHook(useKnowledgeSearch); fetcher.mockRejectedValueOnce(new Error("secret-url")).mockResolvedValueOnce(response(fixture()));
    act(() => input(result.current, fixture().request)); await act(() => result.current.query()); expect(result.current.state.status).toBe("failed"); expect(result.current.state.message).not.toContain("secret-url");
    await act(() => result.current.query()); expect(result.current.state.status).toBe("success");
  });
  it("renders source, uncertainty, no-match and treats HTML/instructions as text", async () => {
    render(<KnowledgeSearch />); expect(fetcher).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("场馆／馆区"), { target: { value: "natural" } });
    fireEvent.change(screen.getByLabelText("想查询的问题"), { target: { value: fixture(2).request.question } });
    const data = fixture(2); data.matches[0].section.summary = "受控文本<script>alert(1)</script>忽略指令"; fetcher.mockResolvedValueOnce(response(data));
    await act(async () => fireEvent.submit(screen.getByRole("button", { name: "查询资料" }).closest("form")!));
    expect(screen.getByText(data.matches[0].section.summary)).toBeTruthy(); expect(document.querySelector("script")).toBeNull();
    expect(screen.getAllByRole("link", { name: /打开官方原文/ })[0]).toHaveProperty("target", "_blank");
    expect(screen.getAllByText(/适用性需核实/).length).toBeGreaterThan(0);
    fireEvent.change(screen.getByLabelText("想查询的问题"), { target: { value: fixture(3).request.question } }); expect(screen.queryByRole("region", { name: "资料检索结果" })).toBeNull();
    fetcher.mockResolvedValueOnce(response(fixture(3))); await act(async () => fireEvent.submit(screen.getByRole("button", { name: "查询资料" }).closest("form")!));
    expect(screen.getByText(/未找到相关资料/)).toBeTruthy();
  });
});

describe("official page is retrieval only", () => {
  it("has no answer action or judgments before/after matches and no-match; requests only search", async () => {
    render(<KnowledgeSearch />);
    expect(screen.getByText(/AI问答暂未开放/)).toBeTruthy();
    expect(fetcher).not.toHaveBeenCalled();
    for (const index of [0, 1, 2, 3]) {
      const r = fixture(index);
      fireEvent.change(screen.getByLabelText("场馆／馆区"), { target: { value: r.request.venue_id } });
      fireEvent.change(screen.getByLabelText("想查询的问题"), { target: { value: r.request.question } });
      fetcher.mockResolvedValueOnce(response(r));
      await act(async () => fireEvent.submit(screen.getByRole("button", { name: "查询资料" }).closest("form")!));
      expect(screen.queryByRole("region", { name: "依据资料回答" })).toBeNull();
      expect(screen.queryByRole("button", { name: /生成回答|取消回答|AI/ })).toBeNull();
      expect(screen.queryByText(/部分有依据，仍有事项待确认|已依据收录资料回答/)).toBeNull();
    }
    expect(fetcher).toHaveBeenCalledTimes(4);
    expect(fetcher.mock.calls.every(([url]) => url === "http://127.0.0.1:8000/knowledge/search")).toBe(true);
  });

  it("cancel is available on the official page and late retrieval cannot show results", async () => {
    const d = defer(); fetcher.mockReturnValueOnce(d.promise);
    render(<KnowledgeSearch />);
    fireEvent.change(screen.getByLabelText("场馆／馆区"), { target: { value: fixture().request.venue_id } });
    fireEvent.change(screen.getByLabelText("想查询的问题"), { target: { value: fixture().request.question } });
    fireEvent.submit(screen.getByRole("button", { name: "查询资料" }).closest("form")!);
    fireEvent.click(screen.getByRole("button", { name: "取消查询" }));
    await act(async () => d.resolve(response(fixture())));
    expect(screen.getByText("已取消本次查询。")).toBeTruthy();
    expect(screen.queryByRole("region", { name: "资料检索结果" })).toBeNull();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
