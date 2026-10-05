import { hasValidCoordinates } from "@/types/place";
import type { WalkingRouteEndpoint, WalkingRouteRequest } from "@/types/route";
import { isTransitRouteResponse, type TransitRouteResponse } from "@/types/transit";

const TRANSIT_URL = "http://127.0.0.1:8000/routes/transit";

export class TransitRouteError extends Error {
  constructor(public readonly kind: "timeout" | "error", message: string) {
    super(message);
    this.name = "TransitRouteError";
  }
}

function isValidEndpoint(endpoint: WalkingRouteEndpoint): boolean {
  return typeof endpoint.place_id === "string" && endpoint.place_id.length <= 128 && /^[A-Za-z0-9_-]+$/.test(endpoint.place_id) &&
    typeof endpoint.longitude === "number" && typeof endpoint.latitude === "number" && hasValidCoordinates(endpoint);
}

export async function queryTransitRoute(request: WalkingRouteRequest, signal?: AbortSignal): Promise<TransitRouteResponse> {
  if (!isValidEndpoint(request.origin) || !isValidEndpoint(request.destination)) {
    throw new TransitRouteError("error", "地点坐标无效，请重新绑定有效地点。");
  }
  if (signal?.aborted) throw new DOMException("路线查询已取消。", "AbortError");
  const controller = new AbortController();
  const cancel = () => controller.abort();
  let timedOut = false;
  signal?.addEventListener("abort", cancel, { once: true });
  const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, 20_000);
  try {
    const response = await fetch(TRANSIT_URL, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request), signal: controller.signal,
    });
    if (!response.ok) {
      if (response.status === 504) throw new TransitRouteError("timeout", "公交／地铁查询超时，请稍后重试。");
      const messages: Record<number, string> = {
        422: "地点坐标未通过校验，请重新绑定地点后重试。",
        502: "高德公交／地铁方案暂时不可用，请稍后重试。",
        503: "公交／地铁服务尚未配置，请联系服务维护者。",
      };
      // Error bodies may contain upstream details; never display them.
      throw new TransitRouteError("error", messages[response.status] ?? "公交／地铁查询失败，请稍后重试。");
    }
    const data: unknown = await response.json();
    if (controller.signal.aborted) throw new DOMException("路线查询已取消。", "AbortError");
    if (!isTransitRouteResponse(data)) throw new TransitRouteError("error", "公交／地铁数据格式无效，未展示方案，请稍后重试。");
    return data;
  } catch (error) {
    if (signal?.aborted) throw new DOMException("路线查询已取消。", "AbortError");
    if (timedOut) throw new TransitRouteError("timeout", "公交／地铁查询超时，请稍后重试。");
    if (error instanceof TransitRouteError) throw error;
    throw new TransitRouteError("error", "无法获取公交／地铁方案，请确认服务可用后重试。");
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener("abort", cancel);
  }
}
