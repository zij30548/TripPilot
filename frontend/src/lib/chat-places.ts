import { apiFetch } from "./auth-session";
import { API_ENDPOINTS } from "./api-endpoints";
import { chatCity, chatRegionBelongsTo } from "./chat-cities";
import type { Place } from "@/types/place";

export type PlaceRole = "accommodation" | "required";
export type ChatPlace = Place & { region: { citycode: string; cityname: string; adcode: string } };
export type PlaceBatch = { id: string; city_id: string; keyword: string; role: PlaceRole; queried_at: string; places: ChatPlace[] };
export type PlaceClarification = "city_ambiguous" | "city_confirmation_required" | "multi_city" | "missing_place" | "unsupported";
export type PlaceOperation = { operation_id: string; state: "running" | "succeeded" | "failed" | "cancelled" | "uncertain"; error_code: string | null };
export type PlacesWorkspace = {
  schema_version: "chat-places-v1"; conversation_id: string; intent_revision: number; revision: number;
  city_id: string | null; accommodation: ChatPlace | null; required_places: ChatPlace[];
  batch: PlaceBatch | null; last_operation: PlaceOperation | null; clarification: PlaceClarification | null;
  proposed_city_id: string | null; request_text: string | null; stale: boolean;
};
export type PlacesAction = { action: "set_city"; city_id: string } | { action: "search"; keyword: string; role: PlaceRole } |
  { action: "ask"; text: string } | { action: "confirm"; batch_id: string; place_id: string; role: PlaceRole } |
  { action: "remove"; place_id: string; role: PlaceRole } | { action: "sync" } | { action: "cancel" };
export type PlacesRequest = PlacesAction & { operation_id: string; expected_intent_revision: number; expected_revision: number };
export const CLARIFICATIONS: Record<PlaceClarification, string> = {
  city_ambiguous: "还不能确定你说的是哪座城市，请先明确选择城市。",
  city_confirmation_required: "这次提到的城市还没有用于当前地点准备，请先明确确认城市，再主动搜索；不会自动切城。",
  multi_city: "已保留你的跨城表达。当前还不能安排城市之间的交通和多处住宿，请说明各城日期；不会偷偷只执行其中一城。",
  missing_place: "还缺少要找的具体地点或关键词，请补充后主动搜索。",
  unsupported: "当前只能准备和确认真实地点，尚不能在这里生成行程、预订或付款。",
};
const record = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
const exact = (v: Record<string, unknown>, keys: string[]) => Object.keys(v).length === keys.length && keys.every(key => Object.hasOwn(v, key));
const integer = (v: unknown, max: number): v is number => Number.isSafeInteger(v) && Number(v) >= 0 && Number(v) <= max;
const uuid = (v: unknown): v is string => typeof v === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(v);
const text = (v: unknown, max: number): v is string => typeof v === "string" && Array.from(v).length > 0 && Array.from(v).length <= max && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(v);
const city = (v: unknown): v is string => typeof v === "string" && /^\d{6}$/.test(v) && chatCity(v) !== null;
const role = (v: unknown): v is PlaceRole => v === "accommodation" || v === "required";
function place(v: unknown, id: string): v is ChatPlace {
  // Do not reuse legacy isPlace(): its region validator deliberately remains
  // six-city-only for saved-trip-v1/v2/v3 and the old planning workspace.
  return record(v) && exact(v, ["id", "name", "address", "longitude", "latitude", "category", "source", "region"]) &&
    text(v.id, 120) && text(v.name, 200) && (v.address === null || text(v.address, 1000)) && (v.category === null || text(v.category, 200)) &&
    typeof v.longitude === "number" && Number.isFinite(v.longitude) && v.longitude >= -180 && v.longitude <= 180 &&
    typeof v.latitude === "number" && Number.isFinite(v.latitude) && v.latitude >= -90 && v.latitude <= 90 &&
    v.source === "amap" && chatRegionBelongsTo(v.region, id);
}
function batch(v: unknown, id: string): v is PlaceBatch {
  return record(v) && exact(v, ["id", "city_id", "keyword", "role", "queried_at", "places"]) && uuid(v.id) && v.city_id === id &&
    text(v.keyword, 80) && role(v.role) && typeof v.queried_at === "string" && /(?:Z|[+-]\d{2}:\d{2})$/.test(v.queried_at) && Number.isFinite(Date.parse(v.queried_at)) &&
    Array.isArray(v.places) && v.places.length <= 20 && v.places.every(p => place(p, id)) && new Set(v.places.map(p => p.id)).size === v.places.length;
}
export function isPlacesWorkspace(v: unknown): v is PlacesWorkspace {
  if (!record(v) || !exact(v, ["schema_version", "conversation_id", "intent_revision", "revision", "city_id", "accommodation", "required_places", "batch", "last_operation", "clarification", "proposed_city_id", "request_text", "stale"]) ||
    v.schema_version !== "chat-places-v1" || !uuid(v.conversation_id) || !integer(v.intent_revision, 1000) || !integer(v.revision, 10000) ||
    !(v.city_id === null || city(v.city_id)) || !(v.proposed_city_id === null || city(v.proposed_city_id)) || typeof v.stale !== "boolean" ||
    !(v.request_text === null || text(v.request_text, 2000)) || !(v.clarification === null || typeof v.clarification === "string" && Object.hasOwn(CLARIFICATIONS, v.clarification)) ||
    !Array.isArray(v.required_places) || v.required_places.length > 6) return false;
  const id = typeof v.city_id === "string" ? v.city_id : "";
  if (!(v.accommodation === null || place(v.accommodation, id)) || !v.required_places.every(p => place(p, id)) ||
    new Set(v.required_places.map(p => p.id)).size !== v.required_places.length || !(v.batch === null || batch(v.batch, id)) ||
    v.city_id === null && (v.accommodation !== null || v.required_places.length > 0 || v.batch !== null) || v.stale && v.batch !== null) return false;
  const snapshots = [...v.required_places, ...(v.accommodation ? [v.accommodation] : []), ...(v.batch ? (v.batch as PlaceBatch).places : [])] as ChatPlace[];
  const seen = new Map<string, ChatPlace>();
  for (const snapshot of snapshots) {
    const prior = seen.get(snapshot.id);
    if (prior && ["name", "address", "longitude", "latitude", "category", "source"].some(key => prior[key as keyof ChatPlace] !== snapshot[key as keyof ChatPlace])) return false;
    if (prior && ["citycode", "cityname", "adcode"].some(key => prior.region[key as keyof ChatPlace["region"]] !== snapshot.region[key as keyof ChatPlace["region"]])) return false;
    seen.set(snapshot.id, snapshot);
  }
  const o = v.last_operation;
  return o === null || record(o) && exact(o, ["operation_id", "state", "error_code"]) && uuid(o.operation_id) &&
    ["running", "succeeded", "failed", "cancelled", "uncertain"].includes(String(o.state)) &&
    (o.error_code === null || typeof o.error_code === "string" && /^[a-z0-9_]{1,80}$/.test(o.error_code));
}
export function placesError(code: string | null): string {
  if (code === "model_structure" || code === "model_references" || code === "model_truncated") return "模型工具选择未通过校验，没有执行确认或采用，不会修补输出或自动重试。";
  if (code?.includes("configured") || code?.includes("configuration")) return "地点或模型服务尚未配置，没有切换其他服务或生成假候选。";
  if (code?.includes("migration") || code?.includes("schema")) return "地点工作副本尚未准备好，请管理员按独立迁移手册处理；不会改写原库或使用假结果。";
  if (code?.includes("quota") || code?.includes("rate") || code?.includes("disabled")) return "当前调用受额度或频率限制；请查看额度提示，不会自动重试。";
  if (code?.includes("conflict") || code?.includes("obsolete") || code?.includes("stale")) return "需求或地点准备版本已经变化，请重新读取并核对，不会覆盖新的内容。";
  if (code?.includes("duplicate")) return "这个真实POI已经在必去列表中，不会重复加入。";
  if (code?.includes("limit")) return "当前容量已达上限，必去最多六个；未改变原有地点。";
  if (code?.includes("busy") || code?.includes("used") || code?.includes("uncertain")) return "本次操作已登记或结果尚不确定，请先重新读取；不会重新发送原请求。";
  if (code?.includes("amap") || code?.includes("region")) return "地点查询或城市归属未通过校验，没有采用这些候选。";
  return "本次地点准备未完成，没有自动重试，也没有生成或保存行程；请重新读取核对状态。";
}
async function json(response: Response): Promise<unknown> {
  if (!response.body || Number(response.headers.get("content-length")) > 384 * 1024) throw new Error("地点响应为空或超限，未载入。");
  const reader = response.body.getReader(), parts: Uint8Array[] = [];
  let size = 0;
  try {
    for (;;) {
      const next = await reader.read(); if (next.done) break;
      size += next.value.length;
      if (size > 384 * 1024) { await reader.cancel(); throw new Error("地点响应超限，未载入。"); }
      parts.push(next.value);
    }
  } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(size); let at = 0;
  for (const part of parts) { bytes.set(part, at); at += part.length; }
  return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
}
export async function requestPlaces(id: string, signal: AbortSignal, command?: PlacesRequest): Promise<PlacesWorkspace> {
  if (!uuid(id)) throw new Error("会话标识无效。");
  if (signal.aborted) throw new DOMException("已取消", "AbortError");
  const controller = new AbortController(), abort = () => controller.abort();
  signal.addEventListener("abort", abort, { once: true });
  const timer = setTimeout(abort, 50_000);
  try {
    const response = await apiFetch(`${API_ENDPOINTS.chatConversations}/${id}/places`, {
      method: command ? "POST" : "GET", signal: controller.signal,
      ...(command ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(command) } : {}),
    });
    const value = await json(response);
    if (controller.signal.aborted) throw new DOMException("已取消", "AbortError");
    if (!response.ok) {
      const code = record(value) && record(value.detail) && typeof value.detail.code === "string" ? value.detail.code : null;
      throw new Error(placesError(code));
    }
    if (!isPlacesWorkspace(value) || value.conversation_id !== id) throw new Error("地点内容或所属会话未通过严格校验，未载入。");
    if (command && command.action !== "cancel" && (value.last_operation?.operation_id !== command.operation_id ||
      value.intent_revision !== command.expected_intent_revision || value.revision <= command.expected_revision)) throw new Error("地点回执不属于本次操作或需求版本，未应用。");
    return value;
  } catch (error) {
    if (signal.aborted) throw new DOMException("已取消", "AbortError");
    if (controller.signal.aborted) throw new Error("地点操作等待超时，可能已经发送或提交；请先重新读取，不会自动重发。");
    if (error instanceof Error && error.name === "Error") throw error;
    throw new Error("连接中断，结果尚未确认；请先重新读取，不会自动重发。");
  } finally { clearTimeout(timer); signal.removeEventListener("abort", abort); }
}
