import { isScheduleRequest, isScheduleResponse, type ScheduleRequest, type ScheduleResponse } from "@/types/schedule";

export class ScheduleError extends Error {
  constructor(public readonly kind: "timeout" | "error", message: string) {
    super(message); this.name = "ScheduleError";
  }
}
export async function querySchedulePreview(request: ScheduleRequest, signal?: AbortSignal): Promise<ScheduleResponse> {
  if (!isScheduleRequest(request)) throw new ScheduleError("error", "请检查确认地点、日期、每日时间、停留分钟和午餐设置后再生成草案。");
  if (signal?.aborted) throw new DOMException("草案生成已取消。", "AbortError");
  const controller = new AbortController(); const cancel = () => controller.abort(); let timedOut = false;
  signal?.addEventListener("abort", cancel, { once: true });
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, 25_000);
  try {
    const response = await fetch("http://127.0.0.1:8000/trips/schedule-preview", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(request), signal: controller.signal,
    });
    if (!response.ok) {
      const messages: Record<number, string> = {
        422: "草案需求未通过检查，请调整地点和时间设置后重试。",
        502: "步行草案服务暂时不可用，请稍后重试。", 503: "步行服务尚未配置，请联系服务维护者。",
        504: "生成步行草案超时，请主动重试。",
      };
      throw new ScheduleError(response.status === 504 ? "timeout" : "error", messages[response.status] ?? "生成步行草案失败，请主动重试。");
    }
    const data: unknown = await response.json();
    if (controller.signal.aborted) throw new DOMException("草案生成已取消。", "AbortError");
    if (!isScheduleResponse(data, request)) throw new ScheduleError("error", "步行草案数据不完整或时间不一致，未展示草案，请重试。");
    return data;
  } catch (error) {
    if (signal?.aborted) throw new DOMException("草案生成已取消。", "AbortError");
    if (timedOut) throw new ScheduleError("timeout", "生成步行草案超时，请主动重试。");
    if (error instanceof ScheduleError) throw error;
    throw new ScheduleError("error", "无法生成步行草案，请确认服务可用后重试。");
  } finally { clearTimeout(timer); signal?.removeEventListener("abort", cancel); }
}
