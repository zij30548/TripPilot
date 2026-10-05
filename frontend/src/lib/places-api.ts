import { isPlace, type Place } from "@/types/place";

const SEARCH_URL = "http://127.0.0.1:8000/places/search";

export async function searchPlaces(keyword: string, signal?: AbortSignal): Promise<Place[]> {
  const query = keyword.trim();
  if (!query || Array.from(query).length > 80) {
    throw new Error("请输入 1～80 个字的上海地点关键词。");
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
    const params = new URLSearchParams({ keyword: query, city: "上海" });
    const response = await fetch(`${SEARCH_URL}?${params}`, { signal: controller.signal });
    if (!response.ok) {
      const messages: Record<number, string> = {
        422: "搜索关键词未通过校验，请输入 1～80 个字后重试。",
        502: "高德地点搜索暂时不可用，请稍后重试。",
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
