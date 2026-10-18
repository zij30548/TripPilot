import { isPlace, type Place } from "@/types/place";
import { regionBelongsTo, type DestinationId } from "./destinations";
import { API_ENDPOINTS } from "./api-endpoints";
import { apiFetch } from "./auth-session";

export async function searchPlaces(keyword: string, signal?: AbortSignal, destinationId?: DestinationId): Promise<Place[]> {
  const query = keyword.trim();
  if (!query || Array.from(query).length > 80) {
    throw new Error("请输入 1～80 个字的地点关键词。");
  }
  if (signal?.aborted) throw new DOMException("搜索已取消。", "AbortError");

  const controller = new AbortController();
  let timedOut = false;
  const cancel = () => controller.abort();
  signal?.addEventListener("abort", cancel, { once: true });
  const timeout = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, 15_000);

  try {
    const params = new URLSearchParams(destinationId ? { keyword: query, destination_id: destinationId } : { keyword: query, city: "上海" });
    const response = await apiFetch(`${API_ENDPOINTS.places}?${params}`, { signal: controller.signal });
    if (!response.ok) {
      const messages: Record<number, string> = {
        422: "搜索关键词未通过校验，请输入 1～80 个字后重试。",
        502: "地点归属或上游数据未通过检查，或搜索服务暂不可用；未采用这些结果，请更换关键词或稍后重试。",
        503: "地点搜索服务尚未配置，请联系服务维护者。",
        504: "地点搜索超时，请稍后重试。",
      };
      throw new Error(messages[response.status] ?? "暂时无法搜索地点，请稍后重试。");
    }
    const data: unknown = await response.json();
    if (controller.signal.aborted) throw new DOMException("搜索已取消。", "AbortError");
    if (!Array.isArray(data) || !data.every(isPlace)) {
      throw new Error("地点数据格式或坐标无效，未显示搜索结果，请重试。");
    }
    if (destinationId && !data.every(p => regionBelongsTo(p.region, destinationId))) throw new Error("地点归属未通过检查，请重新搜索确认。");
    // A repeated POI id must not create ambiguous card / marker selections.
    return data.filter((place, index) => data.findIndex((item) => item.id === place.id) === index);
  } catch (error) {
    if (signal?.aborted) throw new DOMException("搜索已取消。", "AbortError");
    if (timedOut) throw new Error("地点搜索超时，请稍后重试。");
    if (error instanceof TypeError) throw new Error("无法连接地点搜索服务，请确认后端已启动。");
    if (error instanceof SyntaxError) throw new Error("地点数据格式无效，请稍后重试。");
    throw error;
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener("abort", cancel);
  }
}
