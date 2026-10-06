import { isCandidateRequest, isCandidateResponse, type CandidateRequest, type CandidateResponse } from "@/types/candidates";

export class CandidateError extends Error {
  constructor(public readonly kind: "timeout" | "error", message: string) {
    super(message);
    this.name = "CandidateError";
  }
}

export async function queryCandidates(request: CandidateRequest, signal?: AbortSignal): Promise<CandidateResponse> {
  if (!isCandidateRequest(request)) throw new CandidateError("error", "请返回修改需求，确认有效地点和支持的兴趣后再获取候选。");
  if (signal?.aborted) throw new DOMException("获取已取消。", "AbortError");
  const controller = new AbortController();
  const cancel = () => controller.abort();
  let timedOut = false;
  signal?.addEventListener("abort", cancel, { once: true });
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, 15_000);
  try {
    const response = await fetch("http://127.0.0.1:8000/places/candidates", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(request), signal: controller.signal,
    });
    if (!response.ok) {
      const messages: Record<number, string> = {
        422: "已确认地点或兴趣未通过检查，请返回修改需求后重试。",
        502: "候选地点服务暂时不可用，请稍后重试。",
        503: "候选地点服务尚未配置，请联系服务维护者。",
        504: "获取候选地点超时，请主动重试。",
      };
      throw new CandidateError(response.status === 504 ? "timeout" : "error", messages[response.status] ?? "获取候选地点失败，请主动重试。");
    }
    const data: unknown = await response.json();
    if (controller.signal.aborted) throw new DOMException("获取已取消。", "AbortError");
    if (!isCandidateResponse(data, request)) throw new CandidateError("error", "候选地点数据不完整或格式无效，未更新列表，请重试。");
    return data;
  } catch (error) {
    if (signal?.aborted) throw new DOMException("获取已取消。", "AbortError");
    if (timedOut) throw new CandidateError("timeout", "获取候选地点超时，请主动重试。");
    if (error instanceof CandidateError) throw error;
    // Never forward arbitrary fetch/backend error text, credential-bearing URLs or response bodies.
    throw new CandidateError("error", "无法获取候选地点，请确认服务可用后重试。");
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", cancel);
  }
}
