import { isWeatherForecastRequest, isWeatherForecastResponse, type WeatherForecastRequest, type WeatherForecastResponse } from "@/types/weather";

export class WeatherError extends Error {
  constructor(public readonly kind: "timeout" | "error", message: string) {
    super(message);
    this.name = "WeatherError";
  }
}

// One browser request, no automatic retry; retry budgeting belongs to the adapter.
export async function queryWeatherForecast(request: WeatherForecastRequest, signal?: AbortSignal): Promise<WeatherForecastResponse> {
  if (!isWeatherForecastRequest(request)) throw new WeatherError("error", "请确认旅行日期为连续的 1～3 天后再查询天气。");
  if (signal?.aborted) throw new DOMException("天气查询已取消。", "AbortError");
  const snapshot = { start_date: request.start_date, end_date: request.end_date };
  const controller = new AbortController();
  const cancel = () => controller.abort();
  let timedOut = false;
  signal?.addEventListener("abort", cancel, { once: true });
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, 15_000);
  try {
    const response = await fetch("http://127.0.0.1:8000/weather/forecast", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(snapshot), signal: controller.signal,
    });
    if (!response.ok) {
      const messages: Record<number, string> = {
        422: "旅行日期未通过检查，请返回修改需求后重试。",
        502: "天气服务暂时不可用或返回的数据异常，请稍后主动重试。",
        503: "天气服务尚未配置，请联系服务维护者。",
        504: "查询天气超时，请主动重试。",
      };
      throw new WeatherError(response.status === 504 ? "timeout" : "error", messages[response.status] ?? "查询天气失败，请主动重试。");
    }
    const data: unknown = await response.json();
    if (controller.signal.aborted) throw new DOMException("天气查询已取消。", "AbortError");
    if (!isWeatherForecastResponse(data, snapshot)) throw new WeatherError("error", "天气数据不完整或格式无效，本次未更新，请主动重试。");
    return data;
  } catch (error) {
    if (signal?.aborted) throw new DOMException("天气查询已取消。", "AbortError");
    if (timedOut) throw new WeatherError("timeout", "查询天气超时，请主动重试。");
    if (error instanceof WeatherError) throw error;
    // Never expose upstream bodies, arbitrary error text or credential-bearing URLs.
    throw new WeatherError("error", "无法查询天气，请确认服务可用后主动重试。");
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", cancel);
  }
}
