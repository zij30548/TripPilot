import { isAnswerResponse, makeAnswerRequest, type AnswerResponse } from "./knowledge-answer";
import { isKnowledgeResponse, type KnowledgeResponse } from "./knowledge";
import { API_ENDPOINTS } from "./api-endpoints";

export async function answerKnowledge(source: KnowledgeResponse, requestId: string, signal: AbortSignal): Promise<AnswerResponse> {
  if (!isKnowledgeResponse(source, source.request) || source.request.limit !== 5 || !/^[A-Za-z0-9_-]{1,80}$/.test(requestId)) throw new Error("请重新检索资料。");
  const request = makeAnswerRequest(source, requestId), controller = new AbortController();
  const cancel = () => controller.abort(); signal.addEventListener("abort", cancel, { once: true });
  let timeout = false, stale = false;
  const timer = setTimeout(() => { timeout = true; controller.abort(); }, 25_000);
  try {
    if (signal.aborted) throw new DOMException("取消", "AbortError");
    const response = await fetch(API_ENDPOINTS.knowledgeAnswer, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(request), signal: controller.signal });
    stale = response.status === 409;
    if (!response.ok) throw new Error("回答失败");
    const raw = await response.text(); if (raw.length > 65536) throw new Error("结果过大");
    const data: unknown = JSON.parse(raw);
    if (!isAnswerResponse(data, request, source)) throw new Error("资料引用不一致");
    if (controller.signal.aborted) throw new DOMException("取消", "AbortError");
    return data;
  } catch {
    if (signal.aborted) throw new DOMException("已取消回答", "AbortError");
    throw new Error(stale ? "资料版本已变化，请重新检索后再主动生成回答。" : timeout ? "回答超时；资料保留，请主动重试。" : "回答未完成或引用格式检查失败；资料保留，未自动重试。");
  } finally { clearTimeout(timer); signal.removeEventListener("abort", cancel); }
}
