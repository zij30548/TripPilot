import { apiFetch } from "./auth-session";
import { API_ENDPOINTS } from "./api-endpoints";

export type Preference = { key: string; value: string; version: number; source: "user"; consent: true; created_at: string; updated_at: string };
export type PreferenceView = { items: Preference[]; versions: Record<string, number> };
export type PreferenceCommand = { action: "set" | "delete"; key: string; expected_version: number; value?: string; consent?: boolean };
const record = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
const timestamp = (v: unknown) => typeof v === "string" && v.length <= 40 && /(?:Z|\+00:00)$/.test(v) && Number.isFinite(Date.parse(v));
export function preferenceItems(v: unknown): PreferenceView {
  if (!record(v) || Object.keys(v).sort().join() !== "items,schema_version,versions" || v.schema_version !== "assistant-preferences-v1" || !Array.isArray(v.items) || v.items.length > 30 ||
      !record(v.versions) || Object.keys(v.versions).length > 30 || !Object.entries(v.versions).every(([key, version]) => /^[a-z][a-z0-9_]{0,39}$/.test(key) && Number.isSafeInteger(version) && Number(version) >= 1 && Number(version) <= 1000) ||
      !v.items.every(p => record(p) && Object.keys(p).sort().join() === "consent,created_at,key,source,updated_at,value,version" &&
        typeof p.key === "string" && /^[a-z][a-z0-9_]{0,39}$/.test(p.key) && typeof p.value === "string" && p.value.trim() === p.value && p.value.length > 0 && p.value.length <= 500 && !/[\u0000-\u001f\u007f]/.test(p.value) &&
        Number.isSafeInteger(p.version) && Number(p.version) >= 1 && Number(p.version) <= 1000 && p.source === "user" && p.consent === true && timestamp(p.created_at) && timestamp(p.updated_at)) ||
      new Set(v.items.map(p => p.key)).size !== v.items.length) throw new Error("偏好记录未通过校验，未载入。");
  const versions = v.versions as Record<string, number>;
  if (!(v.items as Preference[]).every(item => versions[item.key] === item.version)) throw new Error("偏好版本不一致，未载入。");
  return { items: v.items as Preference[], versions };
}

export async function requestPreferences(cid: string, signal: AbortSignal, command?: PreferenceCommand): Promise<PreferenceView> {
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(cid)) throw new Error("会话标识无效。");
  const controller = new AbortController(), abort = () => controller.abort();
  signal.addEventListener("abort", abort, { once: true });
  const timer = setTimeout(abort, 10_000);
  try {
    if (signal.aborted) throw new DOMException("取消", "AbortError");
    const response = await apiFetch(`${API_ENDPOINTS.chatConversations}/${cid}/assistant-preferences`, { method: command ? "POST" : "GET", signal: controller.signal,
      ...(command ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(command) } : {}) });
    if (!response.body) throw new Error("偏好响应为空。");
    const reader = response.body.getReader(), chunks: Uint8Array[] = []; let length = 0;
    try { for (;;) { const item = await reader.read(); if (item.done) break; length += item.value.length; if (length > 64 * 1024) { await reader.cancel(); throw new Error("偏好响应过大。"); } chunks.push(item.value); } }
    finally { reader.releaseLock(); }
    const bytes = new Uint8Array(length); let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
    if (controller.signal.aborted) throw new DOMException("取消", "AbortError");
    const value: unknown = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
    if (!response.ok) throw new Error(response.status === 409 ? "偏好版本已改变，请重新读取后操作。" : "偏好操作未完成；如结果不确定，请先重新读取，不会自动重试。");
    return preferenceItems(value);
  } finally { clearTimeout(timer); signal.removeEventListener("abort", abort); }
}
