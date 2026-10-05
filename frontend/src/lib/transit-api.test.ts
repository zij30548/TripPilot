import { beforeEach, describe, expect, it, vi } from "vitest";

import { queryTransitRoute, TransitRouteError } from "@/lib/transit-api";
import type { WalkingRouteRequest } from "@/types/route";

// Offline fictional fixtures: no credentials, live POIs or upstream requests.
const request: WalkingRouteRequest = {
  origin: { place_id: "fixture-origin", longitude: 120, latitude: 30 },
  destination: { place_id: "fixture-destination", longitude: 120.02, latitude: 30.02 },
};
const walk = {
  mode: "walking", distance_meters: 320, duration_seconds: 240, instruction: "测试步行接驳", line_name: null,
  departure_stop: null, arrival_stop: null, geometry: [[[120, 30], [120.002, 30.002]]], geometry_complete: true,
};
const ride = {
  mode: "subway", distance_meters: 2200, duration_seconds: 600, instruction: null, line_name: "测试地铁线",
  departure_stop: "测试上车站", arrival_stop: "测试下车站", geometry: [[[120.004, 30.004], [120.02, 30.02]]], geometry_complete: true,
};
const route = { duration_seconds: 901, walking_distance_meters: 320, fare_cny: 4, geometry_complete: true, legs: [walk, ride] };
const result = { status: "ok", source: "amap", queried_at: "2026-10-05T01:02:03Z", selection_rule: "first_supported_complete", route };

describe("queryTransitRoute normalized public-transport contract", () => {
  let fetchMock: ReturnType<typeof vi.fn<typeof fetch>>;
  beforeEach(() => { fetchMock = vi.fn<typeof fetch>(); vi.stubGlobal("fetch", fetchMock); });

  it("uses only the local transit business endpoint, confirmed endpoints and lng/lat without client city/key/upstream", async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify(result)));
    await expect(queryTransitRoute(request)).resolves.toEqual(result);
    const [input, options] = fetchMock.mock.calls[0];
    expect(String(input)).toBe("http://127.0.0.1:8000/routes/transit");
    expect(options?.method).toBe("POST");
    expect(new Headers(options?.headers).get("Content-Type")).toBe("application/json");
    expect(JSON.parse(String(options?.body))).toEqual(request);
    expect(String(options?.body)).not.toMatch(/key|jscode|upstream|city|departure_time/);
  });

  it.each(["no_route", "unsupported", "same_place"])("preserves distinct %s with no invented route", async (status) => {
    const response = { ...result, status, route: null };
    fetchMock.mockResolvedValue(new Response(JSON.stringify(response)));
    await expect(queryTransitRoute(request)).resolves.toEqual(response);
  });

  it("preserves unknown fare and zero/null leg metrics without walking-only length restrictions", async () => {
    const response = { ...result, route: { ...route, fare_cny: null, walking_distance_meters: 0, legs: [
      { ...walk, distance_meters: 0, duration_seconds: 0 },
      { ...ride, distance_meters: 200000, duration_seconds: null },
    ] } };
    fetchMock.mockResolvedValue(new Response(JSON.stringify(response)));
    await expect(queryTransitRoute(request)).resolves.toEqual(response);
  });

  it("preserves missing geometry without fabricating a straight connecting line", async () => {
    const response = { ...result, route: { ...route, geometry_complete: false, legs: [walk, { ...ride, geometry: [], geometry_complete: false }] } };
    fetchMock.mockResolvedValue(new Response(JSON.stringify(response)));
    await expect(queryTransitRoute(request)).resolves.toEqual(response);
  });

  it.each([
    { ...request.origin, longitude: 181 }, { ...request.origin, latitude: 91 },
    { ...request.origin, longitude: Number.NaN }, { ...request.origin, latitude: Number.POSITIVE_INFINITY },
    { ...request.origin, place_id: "" },
  ])("rejects invalid endpoint before network", async (origin) => {
    await expect(queryTransitRoute({ ...request, origin })).rejects.toThrow();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    { ...result, source: "mock" }, { ...result, queried_at: "not-a-date" },
    { ...result, selection_rule: "fastest_guaranteed" }, { ...result, status: "unknown" },
    { ...result, route: null }, { ...result, status: "unsupported" },
    { ...result, route: { ...route, duration_seconds: 0 } },
    { ...result, route: { ...route, duration_seconds: -1 } },
    { ...result, route: { ...route, duration_seconds: "901" } },
    { ...result, route: { ...route, walking_distance_meters: -1 } },
    { ...result, route: { ...route, fare_cny: -1 } },
    { ...result, route: { ...route, fare_cny: "4" } },
    { ...result, route: { ...route, legs: [] } },
    { ...result, route: { ...route, legs: [walk] } },
    ...[
      { ...ride, mode: "taxi" }, { ...ride, line_name: null }, { ...ride, departure_stop: "" },
      { ...ride, arrival_stop: null }, { ...ride, duration_seconds: -1 }, { ...ride, distance_meters: -1 },
      { ...ride, geometry: [[[30, 120], [31, 121]]] },
      { ...ride, geometry: [[[120, 30], [181, 31]]] },
      { ...ride, geometry: [[[120, 30]]] },
      { ...ride, geometry: [[[120, 30], [121, null]]] },
      { ...ride, geometry: [[[120, 30], [121, 31, 5]]] },
      { ...ride, geometry: [], geometry_complete: true },
    ].map((invalidRide) => ({ ...result, route: { ...route, legs: [walk, invalidRide] } })),
    {}, null, "invalid JSON",
  ])("rejects malformed metrics/status/riding information/geometry without a misleading complete plan", async (response) => {
    fetchMock.mockResolvedValue(new Response(typeof response === "string" ? response : JSON.stringify(response)));
    await expect(queryTransitRoute(request)).rejects.toThrow();
  });

  it("rejects non-finite numeric values even in a deserialized response", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ ...result, route: { ...route, duration_seconds: Infinity } }) } as Response);
    await expect(queryTransitRoute(request)).rejects.toThrow();
  });

  it.each([422, 500, 502, 503, 504])("classifies HTTP %s without reflecting upstream errors", async (status) => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ detail: "fixture-private-detail" }), { status }));
    const error: unknown = await queryTransitRoute(request).catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(TransitRouteError);
    expect(error).toMatchObject({ kind: status === 504 ? "timeout" : "error" });
    expect((error as Error).message).not.toContain("fixture-private-detail");
    expect((error as Error).message).toMatch(/[\u4e00-\u9fff]/);
  });

  it("sanitizes network failures", async () => {
    fetchMock.mockRejectedValue(new Error("fixture-private-url-key"));
    const error: unknown = await queryTransitRoute(request).catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(TransitRouteError);
    expect((error as Error).message).not.toContain("fixture-private-url-key");
  });

  it("does not start a cancelled request", async () => {
    const controller = new AbortController(); controller.abort();
    await expect(queryTransitRoute(request, controller.signal)).rejects.toMatchObject({ name: "AbortError" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards abort and ignores a late response from an abort-insensitive transport", async () => {
    let resolve!: (value: Response) => void;
    fetchMock.mockImplementation(() => new Promise((done) => { resolve = done; }));
    const controller = new AbortController();
    const pending = queryTransitRoute(request, controller.signal);
    const rejected = expect(pending).rejects.toMatchObject({ name: "AbortError" });
    controller.abort();
    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(true);
    resolve(new Response(JSON.stringify(result)));
    await rejected;
  });

  it("has a bounded client timeout distinct from user cancellation", async () => {
    vi.useFakeTimers();
    fetchMock.mockImplementation((_input, options) => new Promise((_resolve, reject) => {
      options?.signal?.addEventListener("abort", () => reject(new DOMException("fixture-abort", "AbortError")));
    }));
    const rejected = expect(queryTransitRoute(request)).rejects.toMatchObject({ kind: "timeout" });
    await vi.advanceTimersByTimeAsync(20_001);
    await rejected;
    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(true);
  });
});
