import { isScheduleResponse, type ScheduleResponse } from "@/types/schedule";
import { API_ENDPOINTS } from "./api-endpoints";
import { apiFetch } from "./auth-session";

export const EXPLANATION_VERSION = "schedule-explanation-v1";
export const explanationCodes = ["arrangement", "visits", "movement", "reserved_time", "return_to_stay", "not_arranged", "optional_attempts", "empty_day"] as const;
export type ExplanationCode = typeof explanationCodes[number];
export type ExplanationResponse = {
  contract_version: typeof EXPLANATION_VERSION; request_id: string; schedule_generated_at: string; explained_at: string; model: string;
  highlights: { message_code: ExplanationCode; fact_ids: string[]; text: string }[];
  limitations: string[]; usage: { input_tokens: number | null; output_tokens: number | null };
};

export function explanationSnapshot(response: ScheduleResponse) {
  if (!isScheduleResponse(response, response.request)) throw new Error("当前草案未通过检查，请重新生成。");
  const place = ({ id, name, longitude, latitude, source }: ScheduleResponse["request"]["accommodation_place"]) => ({ id, name, longitude, latitude, source });
  return {
    generated_at: response.generated_at, status: response.status,
    request: {
      ...(response.request.destination_id ? { destination_id: response.request.destination_id } : {}),
      start_date: response.request.start_date, end_date: response.request.end_date,
      daily_start_time: response.request.daily_start_time, daily_end_time: response.request.daily_end_time,
      accommodation_place: place(response.request.accommodation_place), must_visit_places: response.request.must_visit_places.map(place),
      optional_places: (response.request.optional_places ?? []).map(place),
      duration_settings: response.request.duration_settings.map(({ place_id, minutes, source }) => ({ place_id, minutes, source })),
      lunch: { enabled: response.request.lunch.enabled, start_time: response.request.lunch.start_time, end_time: response.request.lunch.end_time },
      transport_mode: response.request.transport_mode,
    },
    days: response.days.map(({ date, return_time, items }) => ({ date, return_time, items: items.map((item) => ({
      kind: item.kind, start_time: item.start_time, end_time: item.end_time, duration_minutes: item.duration_minutes,
      place_id: item.place_id, from_place_id: item.from_place_id, to_place_id: item.to_place_id, edge_id: item.edge_id, duration_source: item.duration_source,
    })) })),
    edges: response.edges.map(({ id, origin, destination, transport_mode, status, duration_seconds, duration_minutes, source, queried_at, used }) => ({
      id, origin: { ...origin }, destination: { ...destination }, transport_mode, status, duration_seconds, duration_minutes, source, queried_at, used,
    })),
    unscheduled: response.unscheduled.map(({ place_id, reason }) => ({ place_id, reason })),
    optional_results: response.optional_results.map(({ place_id, scheduled_date, not_attempted_reason, attempts }) => ({
      place_id, scheduled_date, not_attempted_reason, attempts: attempts.map(({ date, outcome }) => ({ date, outcome })),
    })),
  };
}

const record = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const exact = (v: Record<string, unknown>, keys: string[]) => Object.keys(v).length === keys.length && keys.every((key) => key in v);
const text = (v: unknown, max: number): v is string => typeof v === "string" && v.trim().length > 0 && v.length <= max;
const time = (v: unknown): v is string => text(v, 40) && /^\d{4}-\d{2}-\d{2}T/.test(v) && Number.isFinite(Date.parse(v));
// Mirror only catalogue identity/type layout, not the scheduler or factual prose.
// Cross-language samples test this against the backend's actual catalogue.
export function explanationFactCodes(schedule: ScheduleResponse): ExplanationCode[] {
  const codes: ExplanationCode[] = ["arrangement"];
  for (const day of schedule.days) {
    if (!day.items.length) codes.push("empty_day");
    for (const item of day.items) codes.push(item.kind === "visit" ? "visits" : item.kind === "walk" || item.kind === "transit" ? "movement" : "reserved_time");
    if (day.return_time !== null) codes.push("return_to_stay");
  }
  schedule.unscheduled.forEach(() => codes.push("not_arranged"));
  schedule.optional_results.filter((entry) => entry.scheduled_date === null).forEach(() => codes.push("optional_attempts"));
  return codes;
}
export function isExplanationResponse(value: unknown, requestId: string, generatedAt: string, codes?: ExplanationCode[]): value is ExplanationResponse {
  if (!record(value) || !exact(value, ["contract_version", "request_id", "schedule_generated_at", "explained_at", "model", "highlights", "limitations", "usage"]) ||
      value.contract_version !== EXPLANATION_VERSION || value.request_id !== requestId || !time(value.schedule_generated_at) || Date.parse(value.schedule_generated_at) !== Date.parse(generatedAt) ||
      !time(value.explained_at) || !["qwen3.7-flash-2026-07-15", "qwen3.7-flash"].includes(value.model as string) || !Array.isArray(value.highlights) || value.highlights.length < 1 || value.highlights.length > 6 ||
      !Array.isArray(value.limitations) || value.limitations.length < 3 || value.limitations.length > 4 || !value.limitations.every((entry) => text(entry, 500)) ||
      !record(value.usage) || !exact(value.usage, ["input_tokens", "output_tokens"])) return false;
  for (const [key, tokens] of Object.entries(value.usage)) if (tokens !== null && (typeof tokens !== "number" || !Number.isInteger(tokens) || tokens < 0 || tokens > (key === "output_tokens" ? 1024 : 1_000_000))) return false;
  const used = new Set<string>();
  return value.highlights.every((entry) => {
    if (!record(entry) || !exact(entry, ["message_code", "fact_ids", "text"]) || !explanationCodes.includes(entry.message_code as ExplanationCode) || !text(entry.text, 4000) ||
        !Array.isArray(entry.fact_ids) || entry.fact_ids.length < 1 || entry.fact_ids.length > 3) return false;
    return entry.fact_ids.every((id) => {
      if (typeof id !== "string" || !/^f\d{1,3}$/.test(id) || used.has(id)) return false;
      if (codes && (id !== `f${Number(id.slice(1))}` || codes[Number(id.slice(1))] !== entry.message_code)) return false;
      used.add(id); return true;
    });
  });
}

const safeErrors: Record<string, string> = {
  configuration: "AI 解读尚未配置或配置不受支持，其他行程功能不受影响。",
  authentication: "千问鉴权或权限检查失败，请检查本机北京地域配置。",
  quota: "千问额度或账户状态暂不允许调用，请检查控制台。",
  model_unavailable: "当前账号无法使用已配置的千问快照或请求方式，未切换其他型号。",
  rate_limit: "千问请求受到限流，请稍后主动重试。", timeout: "AI 解读超时，可稍后主动重试。",
  refused: "千问未提供本次解读，原行程未改变。", truncated: "千问输出超出长度限制，未展示不完整解读。",
  empty: "千问返回空内容，未生成解读。", structure: "千问返回内容未通过结构检查，未展示解读。",
  references: "千问引用未通过行程依据检查，未展示解读。", upstream: "暂时无法取得千问解读，请稍后主动重试。",
};
export async function queryExplanation(response: ScheduleResponse, requestId: string, signal: AbortSignal): Promise<ExplanationResponse> {
  const body = JSON.stringify({ contract_version: EXPLANATION_VERSION, request_id: requestId, snapshot: explanationSnapshot(response) });
  if (new TextEncoder().encode(body).length > 65_536) throw new Error("当前草案超出解读范围，原行程仍可使用。");
  if (signal.aborted) throw new DOMException("已取消", "AbortError");
  const controller = new AbortController(); let timedOut = false;
  const cancel = () => controller.abort(); signal.addEventListener("abort", cancel, { once: true });
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, 25_000);
  try {
    const result = await apiFetch(API_ENDPOINTS.explanation, {
      method: "POST", headers: { "Content-Type": "application/json" }, body, signal: controller.signal,
    });
    const data: unknown = await result.json();
    if (controller.signal.aborted) throw new DOMException("已取消", "AbortError");
    if (!result.ok) {
      const code = record(data) && record(data.detail) && typeof data.detail.code === "string" ? data.detail.code : "";
      throw new Error(safeErrors[code] ?? (result.status === 422 ? "当前草案快照未通过核对，请重新生成后重试。" : "解读服务暂不可用，请主动重试。"));
    }
    if (!isExplanationResponse(data, requestId, response.generated_at, explanationFactCodes(response))) throw new Error("解读响应与本次请求不一致，未展示解读。");
    return data;
  } catch (error) {
    if (signal.aborted) throw new DOMException("已取消", "AbortError");
    if (timedOut) throw new Error(safeErrors.timeout);
    if (error instanceof Error && error.name === "Error") throw error;
    throw new Error("无法连接解读服务，请主动重试。");
  } finally { clearTimeout(timer); signal.removeEventListener("abort", cancel); }
}
