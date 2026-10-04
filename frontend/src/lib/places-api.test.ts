import { beforeEach, describe, expect, it, vi } from "vitest";

import { searchPlaces } from "@/lib/places-api";

// Fictional API fixtures: these are not claims about real AMap locations.
const place = {
  id: "test-place-1",
  name: "测试地点一",
  address: "测试地址",
  longitude: 120,
  latitude: 30,
  category: "测试分类",
  source: "amap",
};

describe("searchPlaces", () => {
  let fetchMock: ReturnType<typeof vi.fn<typeof fetch>>;

  beforeEach(() => {
    fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);
  });

  it("sends a trimmed keyword, the fixed Shanghai city and cancellation signal", async () => {
    const controller = new AbortController();
    fetchMock.mockResolvedValue(new Response(JSON.stringify([place])));

    await expect(searchPlaces("  测试地点  ", controller.signal)).resolves.toEqual([place]);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [input, options] = fetchMock.mock.calls[0];
    const url = new URL(String(input), "http://localhost:3000");
    expect(url.pathname).toBe("/places/search");
    expect(url.searchParams.get("keyword")).toBe("测试地点");
    expect(url.searchParams.get("city")).toBe("上海");
    expect(url.searchParams.has("key")).toBe(false);
    expect(url.searchParams.has("jscode")).toBe(false);
    expect(options?.signal).toBeInstanceOf(AbortSignal);
    expect(options?.signal?.aborted).toBe(false);
  });

  it("accepts an empty result and nullable optional fields", async () => {
    fetchMock.mockResolvedValueOnce(new Response("[]"));
    await expect(searchPlaces("测试")).resolves.toEqual([]);

    const optionalFields = { ...place, address: null, category: null };
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify([optionalFields])));
    await expect(searchPlaces("测试")).resolves.toEqual([optionalFields]);
  });

  it.each(["", "  ", "字".repeat(81)])("rejects an invalid keyword before making a request", async (keyword) => {
    await expect(searchPlaces(keyword)).rejects.toThrow();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    { ...place, longitude: 181 },
    { ...place, longitude: -181 },
    { ...place, latitude: 91 },
    { ...place, latitude: -91 },
    { ...place, longitude: "120" },
    { ...place, latitude: null },
    { ...place, id: "" },
    { ...place, name: "" },
    { ...place, source: "mock" },
    null,
  ])("rejects an invalid place instead of inventing coordinates", async (invalidPlace) => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify([invalidPlace])));
    await expect(searchPlaces("测试")).rejects.toThrow();
  });

  it.each([{}, { places: [place] }, null, "not JSON"])("rejects malformed response data", async (data) => {
    fetchMock.mockResolvedValue(new Response(typeof data === "string" ? data : JSON.stringify(data)));
    await expect(searchPlaces("测试")).rejects.toThrow();
  });

  it.each([422, 502, 503, 504])("provides a readable error for HTTP %s without echoing upstream details", async (status) => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ detail: "fixture-private-upstream-detail" }), { status }));
    const error = await searchPlaces("测试").catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(Error);
    expect((error as Error).message).toMatch(/[\u4e00-\u9fff]/);
    expect((error as Error).message).not.toContain("fixture-private-upstream-detail");
  });

  it("turns a connection failure into a readable error", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(searchPlaces("测试")).rejects.toThrow(/[\u4e00-\u9fff]/);
  });

  it("propagates cancellation to the request and does not report it as a service error", async () => {
    const controller = new AbortController();
    fetchMock.mockImplementation((_input, options) => new Promise((_resolve, reject) => {
      options?.signal?.addEventListener("abort", () => reject(new DOMException("Cancelled", "AbortError")));
    }));
    const request = searchPlaces("测试", controller.signal);
    const cancelled = expect(request).rejects.toMatchObject({ name: "AbortError" });
    controller.abort();
    await cancelled;
    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(true);
  });

  it("deduplicates POI identities so a card always corresponds to one marker", async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify([place, { ...place, name: "重复测试地点" }])));
    await expect(searchPlaces("测试")).resolves.toEqual([place]);
  });
});
