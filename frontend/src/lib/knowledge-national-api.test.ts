import { beforeEach, describe, expect, it, vi } from "vitest";
import { readNationalKnowledgeCatalog, searchNationalKnowledge } from "./knowledge-national-api";
import { nationalCatalogFixture, nationalResponseFixture } from "./knowledge-national.test-fixtures";

let fetcher: ReturnType<typeof vi.fn>;
beforeEach(() => { fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher); });
const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } });
describe("bounded same-origin national reference transport", () => {
  it("loads one directory and sends one exact POST, with no answer/model/route requests", async () => {
    fetcher.mockResolvedValueOnce(json(nationalCatalogFixture())).mockResolvedValueOnce(json(nationalResponseFixture()));
    const signal = new AbortController().signal, catalog = await readNationalKnowledgeCatalog(signal);
    const response = await searchNationalKnowledge(nationalResponseFixture().request, catalog, signal);
    expect(response).toEqual(nationalResponseFixture());
    expect(fetcher.mock.calls.map(row => row[0])).toEqual(["/api/knowledge/catalog-v2", "/api/knowledge/search-v2"]);
    expect(fetcher.mock.calls[1][1].method).toBe("POST"); expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual(response.request);
  });
  it("rejects invalid input before sending and does not leak error responses", async () => {
    await expect(searchNationalKnowledge({ ...nationalResponseFixture().request, city_id: "110000" }, nationalCatalogFixture(), new AbortController().signal)).rejects.toThrow("请选择");
    expect(fetcher).not.toHaveBeenCalled(); fetcher.mockResolvedValueOnce(new Response("private response", { status: 503 }));
    await expect(readNationalKnowledgeCatalog(new AbortController().signal)).rejects.toThrow("资料服务未完成"); expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it("rejects declared oversized bodies and streamed excess instead of truncating", async () => {
    fetcher.mockResolvedValueOnce(new Response("{}", { headers: { "Content-Length": String(1024 * 1024 + 1) } }));
    await expect(readNationalKnowledgeCatalog(new AbortController().signal)).rejects.toThrow("响应无效");
    let cancelled = false;
    const stream = new ReadableStream<Uint8Array>({ start(controller) { controller.enqueue(new Uint8Array(128 * 1024 + 1)); }, cancel() { cancelled = true; } });
    fetcher.mockResolvedValueOnce(new Response(stream));
    await expect(searchNationalKnowledge(nationalResponseFixture().request, nationalCatalogFixture(), new AbortController().signal)).rejects.toThrow("响应无效"); expect(cancelled).toBe(true);
  });
  it("times out once and never automatically retries", async () => {
    vi.useFakeTimers();
    try {
      fetcher.mockImplementation((_url, init) => new Promise((_yes, no) => init.signal.addEventListener("abort", () => no(new DOMException("cancelled", "AbortError")))));
      const result = readNationalKnowledgeCatalog(new AbortController().signal).catch(error => error.message);
      await vi.advanceTimersByTimeAsync(10_000); expect(await result).toContain("超时"); expect(fetcher).toHaveBeenCalledTimes(1);
    } finally { vi.useRealTimers(); }
  });
  it("already cancelled work never sends", async () => {
    const controller = new AbortController(); controller.abort();
    await expect(readNationalKnowledgeCatalog(controller.signal)).rejects.toMatchObject({ name: "AbortError" }); expect(fetcher).not.toHaveBeenCalled();
  });
});
