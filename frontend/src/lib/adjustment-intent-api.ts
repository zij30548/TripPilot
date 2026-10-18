import { explanationSnapshot } from "./explanation-api";
import { scheduledVisits } from "./weather-schedule-check";
import type { ScheduleCapture } from "./schedule-adjustment";
import { API_ENDPOINTS } from "./api-endpoints";

export const INTENT_VERSION = "adjustment-intent-v1";
export const intentMessages = {
  single_optional: "已理解为排除一个可选地点。以下仅是调整预览，采用前原方案不变。",
  unclear_request: "请明确写出要排除的一个可选地点全名，例如“不去某公园了，必去地点保留”。较复杂的表达请拆开确认。",
  ambiguous_target: "无法唯一确定地点；同名地点请使用下方对应的地点 ID，不会替你选择第一项。",
  negated_request: "这句话包含保留或否定删除的含义，未排除任何地点。请明确是否要排除。",
  multiple_changes: "暂不支持一次执行多项修改；本次没有执行其中任何一项。请只提出排除一个可选地点。",
  outside_scope: "目前只支持排除当前已安排的一个可选地点，暂不支持改交通、时长、日期或指定替代地点。",
  required_target: "必去地点不能在这里排除，请返回修改旅行需求。",
} as const;
type Reason = keyof typeof intentMessages;
export type IntentSelection = { decision: "execute" | "clarify" | "unsupported"; action: "exclude_optional" | null; target_id: string | null; reason_code: Reason };
export type IntentResponse = { contract_version: typeof INTENT_VERSION; request_id: string; draft_version: number; schedule_generated_at: string; interpreted_at: string; model: string; selection: IntentSelection; usage: { input_tokens: number | null; output_tokens: number | null } };
const record = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const exact = (v: Record<string, unknown>, keys: string[]) => Object.keys(v).length === keys.length && keys.every(k => k in v);
export function isIntentResponse(value: unknown, base: ScheduleCapture, id: string): value is IntentResponse {
  if (!record(value) || !exact(value, ["contract_version", "request_id", "draft_version", "schedule_generated_at", "interpreted_at", "model", "selection", "usage"]) ||
    value.contract_version !== INTENT_VERSION || value.request_id !== id || value.draft_version !== base.version ||
    typeof value.schedule_generated_at !== "string" || Date.parse(value.schedule_generated_at) !== Date.parse(base.original.generated_at) ||
    typeof value.interpreted_at !== "string" || !Number.isFinite(Date.parse(value.interpreted_at)) ||
    !["qwen3.7-flash-2026-07-15", "qwen3.7-flash"].includes(value.model as string) ||
    !record(value.usage) || !exact(value.usage, ["input_tokens", "output_tokens"]) || !Object.entries(value.usage).every(([k,v]) => v === null || typeof v === "number" && Number.isSafeInteger(v) && v >= 0 && v <= (k === "output_tokens" ? 1024 : 1_000_000)) ||
    !record(value.selection) || !exact(value.selection, ["decision", "action", "target_id", "reason_code"])) return false;
  const s = value.selection;
  if (s.decision === "execute") return s.action === "exclude_optional" && s.reason_code === "single_optional" && typeof s.target_id === "string" &&
    scheduledVisits(base.original).some(v => v.role === "optional" && v.place.id === s.target_id);
  return s.action === null && s.target_id === null && (s.decision === "clarify" && ["unclear_request", "ambiguous_target", "negated_request"].includes(s.reason_code as string) ||
    s.decision === "unsupported" && ["multiple_changes", "outside_scope", "required_target"].includes(s.reason_code as string));
}
const errors: Record<string, string> = {
  configuration: "千问配置暂不可用，其他行程功能不受影响。", authentication: "千问鉴权或权限检查失败。", quota: "千问额度或账户状态不允许调用。",
  model_unavailable: "当前千问型号或请求方式暂不可用，未切换型号。", rate_limit: "千问请求受到限流，请稍后主动重试。", timeout: "调整解析超时，请主动重试。",
  references: "调整目标未通过草案核对，未生成预览。", structure: "调整建议未通过格式检查，未生成预览。", refused: "千问未提供本次调整建议。", truncated: "调整建议被截断，未采用。", empty: "千问没有返回调整建议。",
};
export async function queryAdjustmentIntent(base: ScheduleCapture, instruction: string, id: string, signal: AbortSignal): Promise<IntentResponse> {
  if (!instruction.trim() || instruction.length > 400) throw new Error("请输入 1～400 字的调整要求。");
  const body = JSON.stringify({ contract_version: INTENT_VERSION, request_id: id, draft_version: base.version, instruction, snapshot: explanationSnapshot(base.original) });
  if (new TextEncoder().encode(body).length > 65536) throw new Error("当前草案超出解析范围。");
  if (signal.aborted) throw new DOMException("已取消", "AbortError");
  const controller = new AbortController(); let timedOut = false;
  const cancel = () => controller.abort(); signal.addEventListener("abort", cancel, { once: true });
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, 25000);
  try {
    const res = await fetch(API_ENDPOINTS.adjustment, { method: "POST", headers: { "Content-Type": "application/json" }, body, signal: controller.signal });
    const data: unknown = await res.json();
    if (controller.signal.aborted) throw new DOMException("已取消", "AbortError");
    if (!res.ok) { const code = record(data) && record(data.detail) ? data.detail.code : ""; throw new Error(errors[String(code)] ?? "调整解析失败，原方案未改变。请主动重试。"); }
    if (!isIntentResponse(data, base, id)) throw new Error("调整建议与当前草案不一致，未生成预览。");
    return data;
  } catch (error) {
    if (signal.aborted) throw new DOMException("已取消", "AbortError");
    if (timedOut) throw new Error(errors.timeout);
    if (error instanceof Error && error.name === "Error") throw error;
    throw new Error("无法连接调整解析服务，原方案未改变。");
  } finally { clearTimeout(timer); signal.removeEventListener("abort", cancel); }
}
