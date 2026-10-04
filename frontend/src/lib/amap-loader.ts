type DistrictSearchClient = {
  search: (keyword: string, callback: (status: string, result: unknown) => void) => void;
};

export type AMapSDK = Pick<typeof AMap, "Map" | "Marker" | "Polyline"> & {
  DistrictSearch: new (options: {
    level: "province";
    subdistrict: 0;
    extensions: "base";
  }) => DistrictSearchClient;
};

let sdkPromise: Promise<AMapSDK> | null = null;

export function loadAMap(): Promise<AMapSDK> {
  if (typeof window === "undefined") {
    return Promise.reject(new Error("地图只能在浏览器中加载。"));
  }
  if (sdkPromise) return sdkPromise;

  const key = process.env.NEXT_PUBLIC_AMAP_JS_KEY?.trim();
  const configuredServiceHost = process.env.NEXT_PUBLIC_AMAP_SERVICE_HOST?.trim();
  if (!key || !configuredServiceHost) {
    return Promise.reject(new Error("地图尚未配置，请设置地图的浏览器 Key 和安全代理地址后重启前端。"));
  }
  let serviceHost: string;
  try {
    const proxy = new URL(configuredServiceHost);
    proxy.pathname = proxy.pathname.replace(/\/+$/, "");
    if (!["http:", "https:"].includes(proxy.protocol) || proxy.pathname !== "/_AMapService" ||
      proxy.username || proxy.password || proxy.search || proxy.hash) {
      throw new Error("Invalid map proxy configuration");
    }
    serviceHost = proxy.toString();
  } catch {
    return Promise.reject(new Error("地图安全代理地址无效，请配置以 /_AMapService 结尾的 HTTP 或 HTTPS 地址后重启前端。"));
  }

  // Only the proxy URL is public. The security code stays on that server.
  window._AMapSecurityConfig = { serviceHost };
  sdkPromise = import("@amap/amap-jsapi-loader")
    .then(async (loader) => {
      try {
        return await loader.default.load({ key, version: "2.0", plugins: ["AMap.DistrictSearch"] });
      } catch {
        // The official loader keeps a failed state until explicitly reset.
        const resettable = loader.default as typeof loader.default & { reset?: () => void };
        resettable.reset?.();
        throw new Error("地图加载失败。");
      }
    })
    .then((sdk: unknown) => sdk as AMapSDK)
    .catch(() => {
      sdkPromise = null;
      throw new Error("地图加载失败，请检查地图配置或刷新后重试。");
    });
  return sdkPromise;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export function getShanghaiCenter(amap: AMapSDK, signal?: AbortSignal): Promise<[number, number]> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("地图加载已取消。", "AbortError"));
      return;
    }
    let settled = false;
    const finish = (center?: [number, number], error?: Error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      signal?.removeEventListener("abort", cancel);
      if (center) resolve(center);
      else reject(error ?? new Error("暂时无法获取上海地图中心，请刷新后重试。"));
    };
    const cancel = () => finish(undefined, new DOMException("地图加载已取消。", "AbortError"));
    const timeout = setTimeout(() => finish(), 10_000);
    signal?.addEventListener("abort", cancel, { once: true });

    try {
      const district = new amap.DistrictSearch({ level: "province", subdistrict: 0, extensions: "base" });
      district.search("上海", (status, result) => {
        if (settled) return;
        if (status !== "complete" || !isRecord(result) || !Array.isArray(result.districtList)) {
          finish();
          return;
        }
        const shanghai: unknown = result.districtList.find((item: unknown) =>
          isRecord(item) && String(item.adcode) === "310000",
        );
        const center = isRecord(shanghai) ? shanghai.center : null;
        let lng: unknown;
        let lat: unknown;
        try {
          lng = isRecord(center) ? (typeof center.getLng === "function" ? center.getLng() : center.lng) : undefined;
          lat = isRecord(center) ? (typeof center.getLat === "function" ? center.getLat() : center.lat) : undefined;
        } catch {
          finish();
          return;
        }
        if (typeof lng !== "number" || typeof lat !== "number" ||
          !Number.isFinite(lng) || !Number.isFinite(lat) ||
          lng < -180 || lng > 180 || lat < -90 || lat > 90) {
          finish();
          return;
        }
        finish([lng, lat]);
      });
    } catch {
      finish();
    }
  });
}
