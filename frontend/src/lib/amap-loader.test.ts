import { beforeEach, describe, expect, it, vi } from "vitest";

const officialLoader = vi.hoisted(() => ({ load: vi.fn(), reset: vi.fn() }));

vi.mock("@amap/amap-jsapi-loader", () => ({ default: officialLoader }));

const browserGlobals = window as unknown as Record<string, unknown>;

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  vi.resetModules();
  officialLoader.load.mockReset();
  officialLoader.reset.mockReset();
  Reflect.deleteProperty(window, "_AMapSecurityConfig");
  Reflect.deleteProperty(window, "AMap");
  vi.stubEnv("NEXT_PUBLIC_AMAP_JS_KEY", "fixture-browser-js-key");
  vi.stubEnv("NEXT_PUBLIC_AMAP_SERVICE_HOST", "http://127.0.0.1:8000/_AMapService");
});

describe("loadAMap", () => {
  it("sets only serviceHost before loading SDK 2.0 and shares concurrent initialization", async () => {
    const pending = deferred<object>();
    officialLoader.load.mockImplementation(() => {
      expect(browserGlobals._AMapSecurityConfig).toEqual({
        serviceHost: "http://127.0.0.1:8000/_AMapService",
      });
      return pending.promise;
    });
    const { loadAMap } = await import("@/lib/amap-loader");

    const first = loadAMap();
    const second = loadAMap();
    const sdk = { fixture: "sdk" };
    pending.resolve(sdk);

    await expect(first).resolves.toBe(sdk);
    await expect(second).resolves.toBe(sdk);
    await expect(loadAMap()).resolves.toBe(sdk);
    expect(officialLoader.load).toHaveBeenCalledTimes(1);
    expect(officialLoader.load).toHaveBeenCalledWith(expect.objectContaining({
      key: "fixture-browser-js-key",
      version: "2.0",
      plugins: expect.arrayContaining(["AMap.DistrictSearch"]),
    }));
    expect(JSON.stringify(browserGlobals._AMapSecurityConfig)).not.toContain("securityJsCode");
    expect(fetch).not.toHaveBeenCalled();
  });

  it.each(["NEXT_PUBLIC_AMAP_JS_KEY", "NEXT_PUBLIC_AMAP_SERVICE_HOST"])("fails safely before SDK loading when %s is missing", async (name) => {
    vi.stubEnv(name, "");
    const { loadAMap } = await import("@/lib/amap-loader");
    await expect(loadAMap()).rejects.toThrow(/[\u4e00-\u9fff]/);
    expect(officialLoader.load).not.toHaveBeenCalled();
  });

  it.each([
    "javascript:alert(1)",
    "//example.test/_AMapService",
    "http://example.test/arbitrary-proxy",
    "http://user:password@example.test/_AMapService",
    "http://example.test/_AMapService?jscode=fixture-secret",
    "http://example.test/_AMapService#fragment",
  ])("rejects an invalid serviceHost before loading the SDK", async (serviceHost) => {
    vi.stubEnv("NEXT_PUBLIC_AMAP_SERVICE_HOST", serviceHost);
    const { loadAMap } = await import("@/lib/amap-loader");
    await expect(loadAMap()).rejects.toThrow(/[\u4e00-\u9fff]/);
    expect(officialLoader.load).not.toHaveBeenCalled();
  });

  it("allows retry after a failed SDK initialization", async () => {
    const sdk = { fixture: "retried-sdk" };
    officialLoader.load
      .mockRejectedValueOnce(new Error("fixture-private-loader-error"))
      .mockResolvedValueOnce(sdk);
    const { loadAMap } = await import("@/lib/amap-loader");
    await expect(loadAMap()).rejects.toThrow();
    await expect(loadAMap()).resolves.toBe(sdk);
    expect(officialLoader.load).toHaveBeenCalledTimes(2);
  });
});

describe("getShanghaiCenter", () => {
  function districtSDK(result: unknown, status = "complete") {
    const search = vi.fn((_keyword: string, callback: (status: string, data: unknown) => void) => {
      callback(status, result);
    });
    const DistrictSearch = vi.fn(function () {
      return { search };
    });
    return { sdk: { DistrictSearch }, search, DistrictSearch };
  }

  it("uses Shanghai-only basic district data to obtain the map center", async () => {
    const { getShanghaiCenter } = await import("@/lib/amap-loader");
    const { sdk, search, DistrictSearch } = districtSDK({
      districtList: [{ name: "上海市", adcode: "310000", center: { getLng: () => 120, getLat: () => 30 } }],
    });

    await expect(getShanghaiCenter(sdk as unknown as Parameters<typeof getShanghaiCenter>[0])).resolves.toEqual([120, 30]);
    expect(DistrictSearch).toHaveBeenCalledWith(expect.objectContaining({
      subdistrict: 0,
      extensions: "base",
    }));
    expect(search).toHaveBeenCalledWith("上海", expect.any(Function));
    expect(fetch).not.toHaveBeenCalled();
  });

  it.each([
    { districtList: [] },
    { districtList: [{ adcode: "310000", center: { getLng: () => 181, getLat: () => 30 } }] },
    { districtList: [{ adcode: "310000", center: { getLng: () => 120, getLat: () => 91 } }] },
    { districtList: [{ adcode: "310000", center: { getLng: () => Number.NaN, getLat: () => 30 } }] },
    { districtList: [{ adcode: "310000", center: { getLng: () => 120, getLat: () => Infinity } }] },
    { districtList: [{ adcode: "310000", center: null }] },
    { districtList: [{ adcode: "110000", center: { getLng: () => 120, getLat: () => 30 } }] },
    null,
  ])("rejects missing or unsafe district centers instead of supplying a fallback", async (result) => {
    const { getShanghaiCenter } = await import("@/lib/amap-loader");
    const { sdk } = districtSDK(result);
    await expect(getShanghaiCenter(sdk as unknown as Parameters<typeof getShanghaiCenter>[0])).rejects.toThrow();
  });

  it("rejects upstream district lookup failure", async () => {
    const { getShanghaiCenter } = await import("@/lib/amap-loader");
    const { sdk } = districtSDK({ info: "fixture-private-district-error" }, "error");
    await expect(getShanghaiCenter(sdk as unknown as Parameters<typeof getShanghaiCenter>[0])).rejects.toThrow();
  });

  it("ignores a late district callback after cancellation", async () => {
    const { getShanghaiCenter } = await import("@/lib/amap-loader");
    let callback!: (status: string, result: unknown) => void;
    const DistrictSearch = vi.fn(function () {
      return { search: (_keyword: string, next: typeof callback) => { callback = next; } };
    });
    const controller = new AbortController();
    const result = getShanghaiCenter({ DistrictSearch } as unknown as Parameters<typeof getShanghaiCenter>[0], controller.signal);
    const rejected = expect(result).rejects.toThrow();
    controller.abort();
    callback("complete", { districtList: [{ adcode: "310000", center: { getLng: () => 120, getLat: () => 30 } }] });
    await rejected;
  });
});
