import { hasValidCoordinates } from "@/types/place";
import { isWalkingRouteResponse, type WalkingRouteEndpoint, type WalkingRouteRequest, type WalkingRouteResponse } from "@/types/route";

const WALKING_URL = "http://127.0.0.1:8000/routes/walking";

export class WalkingRouteError extends Error {
  constructor(public readonly kind: "timeout" | "error", message: string) {
    super(message);
    this.name = "WalkingRouteError";
  }
}

function isValidEndpoint(endpoint: WalkingRouteEndpoint): boolean {
  return typeof endpoint.place_id === "string" && endpoint.place_id.length <= 128 && /^[A-Za-z0-9_-]+$/.test(endpoint.place_id) &&
    typeof endpoint.longitude === "number" && typeof endpoint.latitude === "number" &&
    hasValidCoordinates(endpoint);
}

export async function queryWalkingRoute(request: WalkingRouteRequest, signal?: AbortSignal): Promise<WalkingRouteResponse> {
  if (!isValidEndpoint(request.origin) || !isValidEndpoint(request.destination)) {
    throw new WalkingRouteError("error", "地点坐标无效，请重新绑定有效地点。");
  }
  if (signal?.aborted) throw new DOMException("路线查询已取消。", "AbortError");

  const controller = new AbortController();
  const cancel = () => controller.abort();
  let timedOut = false;
  signal?.addEventListener("abort", cancel, { once: true });
  const timeout = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, 20_000);

  try {
    const response = await fetch(WALKING_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
      signal: controller.signal,
    });
    if (!response.ok) {
      if (response.status === 504) throw new WalkingRouteError("timeout", "步行路线查询超时，请稍后重试。");
      const messages: Record<number, string> = {
        422: "地点坐标未通过校验，请重新绑定地点后重试。",
        502: "高德步行路线暂时不可用，请稍后重试。",
        503: "步行路线服务尚未配置，请联系服务维护者。",
      };
      // Do not display upstream/backend response bodies, URLs or credentials.
      throw new WalkingRouteError("error", messages[response.status] ?? "步行路线查询失败，请稍后重试。");
    }
    const data: unknown = await response.json();
    if (controller.signal.aborted) throw new DOMException("路线查询已取消。", "AbortError");
    if (!isWalkingRouteResponse(data)) {
      throw new WalkingRouteError("error", "步行路线数据格式无效，未展示路线，请稍后重试。");
    }
    return data;
  } catch (error) {
    if (signal?.aborted) throw new DOMException("路线查询已取消。", "AbortError");
    if (timedOut) throw new WalkingRouteError("timeout", "步行路线查询超时，请稍后重试。");
    if (error instanceof WalkingRouteError) throw error;
    throw new WalkingRouteError("error", "无法获取步行路线，请确认服务可用后重试。");
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener("abort", cancel);
  }
}
