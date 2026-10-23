import { API_ENDPOINTS } from "./api-endpoints";
import { apiFetch } from "./auth-session";
import { isNationalKnowledgeCatalog, isNationalKnowledgeRequest, isNationalKnowledgeResponse, type NationalKnowledgeCatalog, type NationalKnowledgeRequest, type NationalKnowledgeResponse } from "./knowledge-national";

async function jsonRequest(endpoint: string, signal: AbortSignal, limit: number, init: RequestInit = {}): Promise<unknown> {
  const controller = new AbortController(), cancel = () => controller.abort();
  let timedOut = false;
  signal.addEventListener("abort", cancel, { once: true });
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, 10_000);
  try {
    if (signal.aborted) throw new DOMException("已取消", "AbortError");
    const response = await apiFetch(endpoint, { ...init, signal: controller.signal });
    if (!response.ok) throw new Error("资料服务未完成请求。");
    const declared = response.headers?.get("Content-Length");
    if (declared && (!/^\d+$/.test(declared) || Number(declared) > limit)) throw new Error("资料响应超限。");
    let raw: string;
    if (response.body?.getReader) {
      const reader = response.body.getReader(), chunks: Uint8Array[] = [];
      let size = 0;
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          size += value.byteLength;
          if (size > limit) throw new Error("资料响应超限。");
          chunks.push(value);
        }
      } catch (error) { await reader.cancel().catch(() => {}); throw error; }
      finally { reader.releaseLock(); }
      const bytes = new Uint8Array(size);
      let offset = 0;
      for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
      raw = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    } else {
      raw = await response.text();
      if (new TextEncoder().encode(raw).byteLength > limit) throw new Error("资料响应超限。");
    }
    if (controller.signal.aborted) throw new DOMException("已取消", "AbortError");
    return JSON.parse(raw) as unknown;
  } catch {
    if (signal.aborted) throw new DOMException("资料请求已取消", "AbortError");
    throw new Error(timedOut ? "资料查询超时，请主动重试。" : "资料服务未完成或响应无效，请主动重试。");
  } finally { clearTimeout(timer); signal.removeEventListener("abort", cancel); }
}
export async function readNationalKnowledgeCatalog(signal: AbortSignal): Promise<NationalKnowledgeCatalog> {
  const data = await jsonRequest(API_ENDPOINTS.knowledgeCatalog, signal, 1024 * 1024);
  if (!isNationalKnowledgeCatalog(data)) throw new Error("资料目录格式异常，请主动重新加载。");
  return data;
}
export async function searchNationalKnowledge(request: NationalKnowledgeRequest, catalog: NationalKnowledgeCatalog, signal: AbortSignal): Promise<NationalKnowledgeResponse> {
  if (!isNationalKnowledgeRequest(request, catalog)) throw new Error("请选择已收录场馆并输入2至160字的问题，日期须有效。");
  const data = await jsonRequest(API_ENDPOINTS.knowledgeSearchV2, signal, 128 * 1024, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(request),
  });
  if (!isNationalKnowledgeResponse(data, request, catalog)) throw new Error("资料内容与本次目录或查询不一致，请重新加载目录后主动查询。");
  return data;
}
