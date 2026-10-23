/** Only bounded, allowlisted response headers enter this store; never bodies or credentials. */
export const BUSINESS_SERVICES = ["amap_poi", "amap_route", "amap_weather", "amap_proxy", "qwen_text", "qwen_vision", "media_photo", "media_video"] as const;
export type BusinessService = typeof BUSINESS_SERVICES[number];
export const BUSINESS_SERVICE_LABELS: Record<BusinessService, string> = {
  amap_poi: "地点搜索", amap_route: "交通路线", amap_weather: "天气查询", amap_proxy: "地图安全代理",
  qwen_text: "行程 AI", qwen_vision: "照片／选中帧识别", media_photo: "图片处理", media_video: "视频抽帧",
};
type UsageCode = "business_rate_limited" | "business_quota_exhausted" | "business_service_disabled" | "business_usage_unavailable";
export type BusinessUsageNotice = {
  epoch: number; code: UsageCode; service: BusinessService | null; scope: "account" | "global" | null;
  retryAfterSeconds: number | null; resetAt: string | null;
};
const listeners = new Set<() => void>();
let notice: BusinessUsageNotice | null = null;
export const businessUsageNoticeSnapshot = () => notice;
export const businessUsageNoticeServerSnapshot = () => null;
export function subscribeBusinessUsageNotice(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
export function clearBusinessUsageNotice(): void {
  if (!notice) return;
  notice = null;
  listeners.forEach(listener => listener());
}
export function recordBusinessUsageNotice(response: Response, epoch: number): void {
  const code = response.headers?.get("X-Business-Usage-Code");
  if (!(response.status === 429 && ["business_rate_limited", "business_quota_exhausted", "business_service_disabled"].includes(code ?? "")) &&
      !(response.status === 503 && code === "business_usage_unavailable")) return;
  const service = response.headers.get("X-Business-Usage-Service");
  const scope = response.headers.get("X-Business-Usage-Scope");
  const retry = response.headers.get("Retry-After");
  const reset = response.headers.get("X-Business-Usage-Reset-At");
  if ((service !== null && !BUSINESS_SERVICES.includes(service as BusinessService)) ||
      (scope !== null && scope !== "account" && scope !== "global") ||
      (retry !== null && (!/^\d{1,6}$/.test(retry) || Number(retry) > 604800)) ||
      (reset !== null && (reset.length > 40 || !/(?:Z|[+-]\d{2}:\d{2})$/.test(reset) || !Number.isFinite(Date.parse(reset))))) return;
  notice = { epoch, code: code as UsageCode, service: service as BusinessService | null,
    scope: scope as "account" | "global" | null, retryAfterSeconds: retry === null ? null : Number(retry), resetAt: reset };
  listeners.forEach(listener => listener());
}
export function businessUsageNoticeText(value: BusinessUsageNotice): string {
  const service = value.service ? BUSINESS_SERVICE_LABELS[value.service] : "业务调用";
  const scope = value.scope === "global" ? "服务整体" : "当前浏览器工作区";
  if (value.code === "business_usage_unavailable") return "调用额度暂时无法核验，已阻止新增外部调用或媒体处理。请联系管理员；只读历史仍可使用。";
  if (value.code === "business_service_disabled") return `${service}暂未开放调用额度，请联系管理员；不会自动重试。`;
  const reason = value.code === "business_rate_limited" ? "短时调用过于频繁" : `${service}的当日调用额度已用完`;
  const wait = value.retryAfterSeconds === null ? "请稍后手动重试。" : `请至少等待 ${value.retryAfterSeconds} 秒后手动重试。`;
  return `${scope}${reason}。${wait}等待结束不保证服务仍有剩余额度；只读历史仍可使用。`;
}
