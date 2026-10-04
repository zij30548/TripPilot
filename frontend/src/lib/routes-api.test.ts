import { beforeEach, describe, expect, it, vi } from "vitest";

import { queryWalkingRoute, WalkingRouteError } from "@/lib/routes-api";
import type { WalkingRouteRequest } from "@/types/route";

// Fictional coordinates and measurements: these tests never contact AMap.
const request: WalkingRouteRequest = {
  origin: { place_id: "fixture-origin", longitude: 120, latitude: 30 },
  destination: { place_id: "fixture-destination", longitude: 120.01, latitude: 30.01 },
};
const route = {
  distance_meters: 1250,
  duration_seconds: 901,
  segments: [[[120, 30], [120.005, 30.005]], [[120.005, 30.005], [120.01, 30.01]]],
};
const result = { status: "ok", source: "amap", queried_at: "2026-10-05T01:02:03Z", route };

describe("queryWalkingRoute normalized API contract", () => {
  let fetchMock: ReturnType<typeof vi.fn<typeof fetch>>;

  beforeEach(() => {
    fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);
  });

  it("POSTs bound endpoint identities and longitude/latitude to the local business endpoint without credentials", async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify(result)));
    const controller = new AbortController();
    await expect(queryWalkingRoute(request, controller.signal)).resolves.toEqual(result);

    const [input, options] = fetchMock.mock.calls[0];
    const url = new URL(String(input));
    expect(url.origin).toBe("http://127.0.0.1:8000");
    expect(url.pathname).toBe("/routes/walking");
    expect(url.search).toBe("");
    expect(options?.method).toBe("POST");
    expect(new Headers(options?.headers).get("Content-Type")).toBe("application/json");
    expect(JSON.parse(String(options?.body))).toEqual(request);
    expect(options?.signal?.aborted).toBe(false);
    expect(String(options?.body)).not.toMatch(/key|jscode|upstream/);
  });

  it.each(["no_route", "same_place"])("accepts the explicit %s state without generating distance or geometry", async (status) => {
    const empty = { ...result, status, route: null };
    fetchMock.mockResolvedValue(new Response(JSON.stringify(empty)));
    await expect(queryWalkingRoute(request)).resolves.toEqual(empty);
  });

  it.each([
    { ...request.origin, longitude: 181 },
    { ...request.origin, longitude: -181 },
    { ...request.origin, latitude: 91 },
    { ...request.origin, latitude: -91 },
    { ...request.origin, longitude: Number.NaN },
    { ...request.origin, latitude: Number.POSITIVE_INFINITY },
    { ...request.origin, place_id: "" },
  ])("rejects an invalid endpoint without issuing HTTP", async (origin) => {
    await expect(queryWalkingRoute({ ...request, origin })).rejects.toThrow();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    { ...result, source: "mock" },
    { ...result, queried_at: "not-a-date" },
    { ...result, status: "unknown" },
    { ...result, route: null },
    { ...result, status: "no_route" },
    { ...result, route: { ...route, distance_meters: -1 } },
    { ...result, route: { ...route, distance_meters: 0 } },
    { ...result, route: { ...route, distance_meters: 100001 } },
    { ...result, route: { ...route, distance_meters: "1250" } },
    { ...result, route: { ...route, duration_seconds: -1 } },
    { ...result, route: { ...route, duration_seconds: 0 } },
    { ...result, route: { ...route, duration_seconds: "901" } },
    { ...result, route: { ...route, segments: [] } },
    { ...result, route: { ...route, segments: [[[120, 30]]] } },
    { ...result, route: { ...route, segments: [[[120, 30], [181, 31]]] } },
    { ...result, route: { ...route, segments: [[[30, 120], [31, 121]]] } },
    { ...result, route: { ...route, segments: [[[120, 30], [121, null]]] } },
    { ...result, route: { ...route, segments: [[[120, 30], [121, 31, 5]]] } },
    { ...result, route: { ...route, segments: [[[120, 30], [120, 30]]] } },
    {}, null, "not JSON",
  ])("rejects invalid units/coordinates/status rather than fabricating a usable route", async (payload) => {
    fetchMock.mockResolvedValue(new Response(typeof payload === "string" ? payload : JSON.stringify(payload)));
    await expect(queryWalkingRoute(request)).rejects.toThrow();
  });

  it.each([422, 500, 502, 503, 504])("uses a safe classified message for HTTP %s", async (status) => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ detail: "fixture-private-upstream-response" }), { status }));
    const error: unknown = await queryWalkingRoute(request).catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(WalkingRouteError);
    expect(error).toMatchObject({ kind: status === 504 ? "timeout" : "error" });
    expect((error as Error).message).toMatch(/[\u4e00-\u9fff]/);
    expect((error as Error).message).not.toContain("fixture-private-upstream-response");
  });

  it("reports connection failures without forwarding the raw exception", async () => {
    fetchMock.mockRejectedValue(new TypeError("fixture-private-connection-detail"));
    const error: unknown = await queryWalkingRoute(request).catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(WalkingRouteError);
    expect((error as Error).message).not.toContain("fixture-private-connection-detail");
    expect((error as Error).message).toMatch(/[\u4e00-\u9fff]/);
  });

  it("does not issue a pre-cancelled request", async () => {
    const controller = new AbortController();
    controller.abort();
    await expect(queryWalkingRoute(request, controller.signal)).rejects.toMatchObject({ name: "AbortError" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards cancellation and rejects a late resolved body after cancellation", async () => {
    let resolve!: (response: Response) => void;
    fetchMock.mockImplementation(() => new Promise((done) => { resolve = done; }));
    const controller = new AbortController();
    const pending = queryWalkingRoute(request, controller.signal);
    const rejected = expect(pending).rejects.toMatchObject({ name: "AbortError" });
    controller.abort();
    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(true);
    resolve(new Response(JSON.stringify(result)));
    await rejected;
  });

  it("enforces the client timeout and classifies it separately from cancellation", async () => {
    vi.useFakeTimers();
    fetchMock.mockImplementation((_input, options) => new Promise((_resolve, reject) => {
      options?.signal?.addEventListener("abort", () => reject(new DOMException("fixture-abort", "AbortError")));
    }));
    const pending = queryWalkingRoute(request);
    const rejected = expect(pending).rejects.toMatchObject({ kind: "timeout" });
    await vi.advanceTimersByTimeAsync(30_000);
    await rejected;
    expect(fetchMock.mock.calls[0][1]?.signal?.aborted).toBe(true);
  });
});
