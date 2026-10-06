import { afterEach, describe, expect, it, vi } from "vitest";
import { CandidateError, queryCandidates } from "./candidates-api";
import { candidateSearches, isCandidateRequest, type CandidateRequest, type CandidateResponse } from "@/types/candidates";
import type { Place } from "@/types/place";

const place = (id: string): Place => ({ id, name: `地点 ${id}`, address: "测试地址", latitude: 31.2, longitude: 121.4, category: "原始类别", source: "amap" });
const request = (): CandidateRequest => ({ accommodation_place: place("hotel"), must_visit_places: [place("must")], interests: ["美食", "摄影", "摄影"] });
function response(input = request()): CandidateResponse {
  const sources = candidateSearches(input.interests);
  return {
    status: "success", queried_at: "2026-10-06T01:02:03.123456Z", keywords: sources.map((source) => source.keyword),
    queries: sources.map((source) => ({ ...source, status: "success", result_count: 2, message: null })),
    candidates: [...input.must_visit_places.map((item) => ({ place: item, role: "must_visit" as const, retrieval_sources: [] })),
      { place: place("optional"), role: "optional", retrieval_sources: sources }],
  };
}
const fetchResponse = (value: unknown) => vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => value }));
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers(); });

describe("candidate contract and safe API", () => {
  it("uses canonical interest order, deduplicates logical searches and has one generic default", () => {
    expect(candidateSearches(["购物", "摄影", "建筑", "摄影", "Citywalk", "美食", "博物馆"])).toEqual([
      { interest: "摄影", keyword: "公园" }, { interest: "Citywalk", keyword: "步行街" }, { interest: "美食", keyword: "餐厅" },
      { interest: "建筑", keyword: "历史建筑" }, { interest: "博物馆", keyword: "博物馆" }, { interest: "购物", keyword: "商场" },
    ]);
    expect(candidateSearches([])).toEqual([{ interest: null, keyword: "旅游景点" }]);
  });
  it("sends only confirmed snapshots and interests to the independent endpoint", async () => {
    fetchResponse(response());
    expect(await queryCandidates(request())).toEqual(response());
    expect(fetch).toHaveBeenCalledTimes(1);
    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(url).toBe("http://127.0.0.1:8000/places/candidates");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual(request());
  });
  it("accepts generic empty success separately from all queries failing", async () => {
    const input = { ...request(), interests: [] };
    const data = response(input);
    data.candidates = data.candidates.filter((entry) => entry.role === "must_visit");
    data.queries[0].result_count = 0;
    fetchResponse(data);
    expect((await queryCandidates(input)).status).toBe("success");
    data.status = "failed";
    data.queries[0] = { ...data.queries[0], status: "timeout", message: "获取超时，请重试。" };
    expect((await queryCandidates(input)).status).toBe("failed");
  });
  it("accepts partial success, merged sources and accommodation also required", async () => {
    const input = request(); input.must_visit_places.push(input.accommodation_place);
    const data = response(input);
    data.status = "partial";
    data.queries[1] = { ...data.queries[1], status: "failed", result_count: 0, message: "搜索暂时不可用。" };
    data.candidates.at(-1)!.retrieval_sources = [candidateSearches(input.interests)[0]];
    fetchResponse(data);
    expect(await queryCandidates(input)).toEqual(data);
  });
  it.each([
    ["missing accommodation", (input: Record<string, unknown>) => { delete input.accommodation_place; }],
    ["arbitrary keyword", (input: Record<string, unknown>) => { input.keyword = "用户输入"; }],
    ["arbitrary city", (input: Record<string, unknown>) => { input.city = "北京"; }],
    ["unknown interest", (input: Record<string, unknown>) => { input.interests = ["未知兴趣"]; }],
    ["duplicate required ID", (input: Record<string, unknown>) => { input.must_visit_places = [place("must"), place("must")]; }],
    ["invalid coordinate", (input: Record<string, unknown>) => { input.accommodation_place = { ...place("hotel"), longitude: Infinity }; }],
    ["extra invented data", (input: Record<string, unknown>) => { input.accommodation_place = { ...place("hotel"), ticket: 30 }; }],
  ])("rejects %s before network", async (_, mutate) => {
    const input = request() as unknown as Record<string, unknown>; mutate(input);
    const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock);
    expect(isCandidateRequest(input)).toBe(false);
    await expect(queryCandidates(input as unknown as CandidateRequest)).rejects.toThrow("返回修改需求");
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it.each([
    ["missing confirmed must", (data: CandidateResponse) => { data.candidates.shift(); }],
    ["changed confirmed snapshot", (data: CandidateResponse) => { data.candidates[0].place = { ...data.candidates[0].place, longitude: 121.5 }; }],
    ["must made optional", (data: CandidateResponse) => { data.candidates[0].role = "optional"; }],
    ["unknown must", (data: CandidateResponse) => { data.candidates.push({ place: place("unknown"), role: "must_visit", retrieval_sources: [] }); }],
    ["duplicate ID", (data: CandidateResponse) => { data.candidates.push(data.candidates[1]); }],
    ["lodging made optional", (data: CandidateResponse) => { data.candidates[1].place = place("hotel"); }],
    ["invalid source", (data: CandidateResponse) => { data.candidates[1].place.source = "other" as "amap"; }],
    ["invalid coordinate", (data: CandidateResponse) => { data.candidates[1].place.latitude = NaN; }],
    ["invented Place field", (data: CandidateResponse) => { Object.assign(data.candidates[1].place, { opening_hours: "09:00" }); }],
    ["missing retrieval source", (data: CandidateResponse) => { data.candidates[1].retrieval_sources = []; }],
    ["unrequested source", (data: CandidateResponse) => { data.candidates[1].retrieval_sources = [{ interest: "购物", keyword: "商场" }]; }],
    ["duplicate source", (data: CandidateResponse) => { data.candidates[1].retrieval_sources.push(data.candidates[1].retrieval_sources[0]); }],
    ["source tied to failed query", (data: CandidateResponse) => { data.status = "partial"; data.queries[0] = { ...data.queries[0], status: "failed", result_count: 0, message: "失败" }; }],
    ["source tied to zero results", (data: CandidateResponse) => { data.queries[0].result_count = 0; }],
    ["wrong keywords", (data: CandidateResponse) => { data.keywords[0] = "餐厅"; }],
    ["wrong query order", (data: CandidateResponse) => { data.queries.reverse(); }],
    ["over page limit", (data: CandidateResponse) => { data.queries[0].result_count = 21; }],
    ["fractional result count", (data: CandidateResponse) => { data.queries[0].result_count = 1.5; }],
    ["wrong aggregate status", (data: CandidateResponse) => { data.status = "failed"; }],
    ["array instead of role", (data: CandidateResponse) => { Object.assign(data.candidates[1], { role: ["optional"] }); }],
    ["array instead of query status", (data: CandidateResponse) => { Object.assign(data.queries[0], { status: ["success"] }); }],
    ["more sourced candidates than retrieved", (data: CandidateResponse) => { data.queries[0].result_count = 1; data.candidates[0].retrieval_sources = [candidateSearches(request().interests)[0]]; }],
    ["invalid retrieval time", (data: CandidateResponse) => { data.queried_at = "yesterday"; }],
    ["invalid calendar date", (data: CandidateResponse) => { data.queried_at = "2026-02-31T01:00:00Z"; }],
    ["non UTC time", (data: CandidateResponse) => { data.queried_at = "2026-10-06T10:00:00+08:00"; }],
    ["too many optionals", (data: CandidateResponse) => { for (let i = 0; i < 18; i++) data.candidates.push({ ...data.candidates[1], place: place(`extra${i}`) }); }],
  ])("rejects response with %s", async (_, mutate) => {
    const data = response(); mutate(data); fetchResponse(data);
    await expect(queryCandidates(request())).rejects.toThrow("数据不完整或格式无效");
  });
  it("keeps distinct IDs with identical names; required count has no optional cap", async () => {
    const input = request();
    input.must_visit_places = Array.from({ length: 22 }, (_, index) => ({ ...place(`must${index}`), name: "同名地点" }));
    const data = response(input); fetchResponse(data);
    expect((await queryCandidates(input)).candidates).toHaveLength(23);
  });
  it.each([422, 500, 502, 503, 504])("does not expose response body on HTTP %i or retry", async (status) => {
    const json = vi.fn(async () => ({ detail: "sensitive upstream URL" }));
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status, json }));
    await expect(queryCandidates(request())).rejects.not.toThrow("sensitive");
    expect(json).not.toHaveBeenCalled(); expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("masks arbitrary network or JSON exceptions", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("sensitive URL")));
    await expect(queryCandidates(request())).rejects.toThrow("无法获取候选地点");
    vi.mocked(fetch).mockResolvedValue({ ok: true, json: async () => { throw new SyntaxError("sensitive payload"); } } as unknown as Response);
    await expect(queryCandidates(request())).rejects.toThrow("无法获取候选地点");
  });
  it("has a 15 second timeout and does not retry", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn((_url, init) => new Promise((_resolve, reject) => init.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError"))))));
    const result = queryCandidates(request()).catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(14_999); expect(vi.mocked(fetch).mock.calls[0][1]?.signal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(await result).toMatchObject({ kind: "timeout" }); expect(fetch).toHaveBeenCalledTimes(1); expect(vi.getTimerCount()).toBe(0);
  });
  it("propagates explicit abort, guards a late successful transport and cleans timers", async () => {
    vi.useFakeTimers(); const controller = new AbortController();
    let resolve!: (value: unknown) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise((done) => { resolve = done; })));
    const result = queryCandidates(request(), controller.signal).catch((error: unknown) => error);
    controller.abort(); resolve({ ok: true, json: async () => response() });
    expect(await result).toMatchObject({ name: "AbortError" }); expect(vi.getTimerCount()).toBe(0);
    await expect(queryCandidates(request(), controller.signal)).rejects.toMatchObject({ name: "AbortError" });
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("exports a classified safe error", () => expect(new CandidateError("timeout", "超时").kind).toBe("timeout"));
});
