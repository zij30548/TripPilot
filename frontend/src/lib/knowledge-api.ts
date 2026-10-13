import { isKnowledgeRequest, isKnowledgeResponse, type KnowledgeRequest, type KnowledgeResponse } from "./knowledge";

export async function searchKnowledge(request: KnowledgeRequest, signal: AbortSignal): Promise<KnowledgeResponse> {
  if (!isKnowledgeRequest(request)) throw new Error("请选择馆区并输入2至160字的问题。");
  const controller = new AbortController();
  const cancel = () => controller.abort();
  signal.addEventListener("abort", cancel, { once: true });
  let timeout = false;
  const timer = setTimeout(() => { timeout = true; controller.abort(); }, 10_000);
  try {
    if (signal.aborted) throw new DOMException("取消", "AbortError");
    const response = await fetch("http://127.0.0.1:8000/knowledge/search", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(request), signal: controller.signal,
    });
    if (!response.ok) throw new Error("查询失败");
    const raw = await response.text();
    if (raw.length > 64 * 1024) throw new Error("结果过大");
    const data: unknown = JSON.parse(raw);
    if (!isKnowledgeResponse(data, request)) throw new Error("资料格式异常");
    if (controller.signal.aborted) throw new DOMException("取消", "AbortError");
    return data;
  } catch {
    if (signal.aborted) throw new DOMException("查询已取消", "AbortError");
    throw new Error(timeout ? "查询超时，请主动重试。" : "本地资料查询未完成或结果格式异常，请主动重试。");
  } finally { clearTimeout(timer); signal.removeEventListener("abort", cancel); }
}
