import { API_ENDPOINTS } from "./api-endpoints";
import { apiFetch } from "./auth-session";
import { BUSINESS_SERVICES, type BusinessService } from "./business-usage-notices";

type ServiceUsage = { used: number; limit: number; remaining: number };
export type BusinessUsage = {
  version: "business-usage-v1"; timezone: "Asia/Shanghai"; now: string; reset_at: string;
  rate: { used: number; limit: number; window_seconds: number; reset_at: string };
  services: Record<BusinessService, ServiceUsage>;
};
const invalid = () => new Error("调用额度响应无效，请联系管理员。");
function record(value: unknown): value is Record<string, unknown> { return !!value && typeof value === "object" && !Array.isArray(value); }
function exact(value: Record<string, unknown>, fields: readonly string[]) { return Object.keys(value).length === fields.length && fields.every(field => Object.hasOwn(value, field)); }
function integer(value: unknown): value is number { return Number.isSafeInteger(value) && (value as number) >= 0; }
function timestamp(value: unknown): value is string { return typeof value === "string" && value.length <= 40 && /(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value)); }
export function parseBusinessUsage(value: unknown): BusinessUsage {
  if (!record(value) || !exact(value, ["version", "timezone", "now", "reset_at", "rate", "services"]) ||
      value.version !== "business-usage-v1" || value.timezone !== "Asia/Shanghai" || !timestamp(value.now) || !timestamp(value.reset_at) ||
      Date.parse(value.reset_at) <= Date.parse(value.now) || !record(value.rate) || !exact(value.rate, ["used", "limit", "window_seconds", "reset_at"]) ||
      !integer(value.rate.used) || !integer(value.rate.limit) || !integer(value.rate.window_seconds) || value.rate.window_seconds < 1 || value.rate.window_seconds > 86400 ||
      !timestamp(value.rate.reset_at) || Date.parse(value.rate.reset_at) <= Date.parse(value.now) || !record(value.services) || !exact(value.services, BUSINESS_SERVICES)) throw invalid();
  for (const service of BUSINESS_SERVICES) {
    const item = value.services[service];
    if (!record(item) || !exact(item, ["used", "limit", "remaining"]) || !integer(item.used) || !integer(item.limit) || !integer(item.remaining) ||
        item.remaining !== Math.max(0, item.limit - item.used)) throw invalid();
  }
  return value as BusinessUsage;
}
/** The small own-account response is bounded independently of route/media bodies. */
export async function queryBusinessUsage(signal: AbortSignal): Promise<BusinessUsage> {
  const response = await apiFetch(API_ENDPOINTS.usage, { signal });
  if (!response.ok) throw new Error(response.status === 503 ? "调用额度暂时无法核验，请联系管理员。" : "无法读取当前账号额度，请稍后手动重试。");
  if (!response.body) throw invalid();
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let length = 0;
  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      length += chunk.value.byteLength;
      if (length > 32 * 1024) { await reader.cancel(); throw invalid(); }
      chunks.push(chunk.value);
    }
  } finally { reader.releaseLock(); }
  if (signal.aborted) throw new DOMException("额度查询已取消", "AbortError");
  const body = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.byteLength; }
  let value: unknown;
  try { value = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(body)); } catch { throw invalid(); }
  return parseBusinessUsage(value);
}
