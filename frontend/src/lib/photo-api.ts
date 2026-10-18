import { type DestinationId } from "./destinations";
import { API_ENDPOINTS } from "./api-endpoints";

export type PhotoClue = { name: string; city: string | null };
export type PhotoResult = { contract_version: "photo-landmark-v2"; request_id: string; destination_id: DestinationId;
  status: "possible" | "uncertain" | "unrecognized"; clues: PhotoClue[]; model: string;
  usage: { input_tokens: number | null; output_tokens: number | null } };
const record = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
const exact = (v: Record<string, unknown>, keys: string[]) => Object.keys(v).length === keys.length && keys.every(k => k in v);
const bounded = (v: unknown, n: number): v is string => typeof v === "string" && v.trim().length > 0 && Array.from(v).length <= n;
export function parsePhotoResult(v: unknown, requestId: string, city: DestinationId): PhotoResult {
  if (!record(v) || !exact(v, ["contract_version", "request_id", "destination_id", "status", "clues", "model", "usage"])
    || v.contract_version !== "photo-landmark-v2" || v.request_id !== requestId || v.destination_id !== city
    || v.model !== "qwen3-vl-plus" || typeof v.status !== "string" || !["possible", "uncertain", "unrecognized"].includes(v.status)
    || !Array.isArray(v.clues) || v.clues.length !== (v.status === "possible" ? 1 : 0)
    || !v.clues.every(c => record(c) && exact(c, ["name", "city"]) && bounded(c.name, 80) && (c.city === null || bounded(c.city, 40)))
    || !record(v.usage) || !exact(v.usage, ["input_tokens", "output_tokens"])
    || ![v.usage.input_tokens, v.usage.output_tokens].every(x => x === null || Number.isSafeInteger(x) && Number(x) >= 0 && Number(x) <= 1_000_000)
    || v.usage.output_tokens !== null && Number(v.usage.output_tokens) > 1024) throw new Error("识别结果未通过校验，未加入任何地点。");
  return v as PhotoResult;
}

export async function recognizePhoto(file: File, city: DestinationId, requestId: string, signal: AbortSignal): Promise<PhotoResult> {
  if (!file.size || file.size > 5 * 1024 * 1024) throw new Error("请选择一张不超过 5 MiB 的照片。");
  if (signal.aborted) throw new DOMException("已取消", "AbortError");
  const controller = new AbortController(); let timeout = false;
  const cancel = () => controller.abort(); signal.addEventListener("abort", cancel, { once: true });
  const timer = setTimeout(() => { timeout = true; controller.abort(); }, 35_000);
  try {
    // No multipart filename, remote URL, EXIF-derived labels or claimed POI.
    const response = await fetch(`${API_ENDPOINTS.photo}?${new URLSearchParams({ destination_id: city, request_id: requestId })}`,
      { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file, signal: controller.signal });
    if (!response.ok) {
      const messages: Record<number, string> = { 413: "图片超过 5 MiB 或 2000 万像素，请缩小。", 422: "图片内容无效或不是静态 JPEG、PNG、WebP。", 429: "照片处理忙或服务限流，请稍后主动重试。", 503: "照片识别尚未配置或配置不受支持。", 504: "照片识别超时；上游不一定停止处理或计费。", 502: "视觉模型未提供有效结果或权限不足；未加入地点，也未切换模型。" };
      throw new Error(messages[response.status] ?? "照片识别失败，已确认地点未改变。");
    }
    const data: unknown = await response.json();
    if (controller.signal.aborted) throw new DOMException("已取消", "AbortError");
    return parsePhotoResult(data, requestId, city);
  } catch (error) {
    if (signal.aborted) throw new DOMException("已取消", "AbortError");
    if (timeout) throw new Error("照片识别等待超时；上游不一定停止处理或计费。");
    if (error instanceof TypeError) throw new Error("无法连接照片识别服务。");
    if (error instanceof SyntaxError) throw new Error("识别结果格式无效，未加入地点。");
    throw error;
  } finally { clearTimeout(timer); signal.removeEventListener("abort", cancel); }
}
