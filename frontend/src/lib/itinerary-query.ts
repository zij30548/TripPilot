import { isScheduleResponse, type ScheduleDay } from "@/types/schedule";
import type { ScheduleCapture } from "./schedule-adjustment";
import { calculateScheduleCosts, type CostInputs, type ScheduleCosts } from "./schedule-costs";
import { API_ENDPOINTS } from "./api-endpoints";

export const QUERY_VERSION = "itinerary-query-v1";
export const clarificationMessages = {
  date_ambiguous: "请明确写出第几天或完整日期，例如“第二天”或“2026-10-20”。不会替你选择日期。",
  date_out_of_range: "该日期或天数不在当前草案范围内，请选择下方列出的日期。",
  multiple_requests: "请一次只查询一个日期的安排，或全程费用核对。本次未执行其中任何一项。",
  unsupported: "这里仅支持查看一天的安排或全程费用，不修改、生成、保存行程，也不查询天气或知识库。",
} as const;
export type ToolCall = { name: "get_day_schedule"; arguments: { date: string } } | { name: "get_budget_summary"; arguments: Record<string, never> } | { name: "request_clarification"; arguments: { reason: keyof typeof clarificationMessages } };
export type QueryRequest = { contract_version: typeof QUERY_VERSION; request_id: string; draft_version: number; cost_version: number; dates: string[]; question: string };
export type QueryResponse = Omit<QueryRequest, "question"> & { tool_call_id: string; tool_call: ToolCall; model: string; usage: { input_tokens: number | null; output_tokens: number | null } };
const record = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const exact = (v: Record<string, unknown>, keys: string[]) => Object.keys(v).length === keys.length && keys.every(k => Object.hasOwn(v, k));
export function isQueryResponse(v: unknown, request: QueryRequest): v is QueryResponse {
  if (!record(v) || !exact(v, ["contract_version", "request_id", "draft_version", "cost_version", "dates", "tool_call_id", "tool_call", "model", "usage"]) ||
    v.contract_version !== QUERY_VERSION || v.request_id !== request.request_id || v.draft_version !== request.draft_version || v.cost_version !== request.cost_version ||
    JSON.stringify(v.dates) !== JSON.stringify(request.dates) || typeof v.tool_call_id !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(v.tool_call_id) ||
    v.model !== "qwen3.7-flash-2026-07-15" || !record(v.usage) || !exact(v.usage, ["input_tokens", "output_tokens"]) ||
    !Object.entries(v.usage).every(([k, n]) => n === null || typeof n === "number" && Number.isSafeInteger(n) && n >= 0 && n <= (k === "output_tokens" ? 1024 : 1_000_000)) ||
    !record(v.tool_call) || !exact(v.tool_call, ["name", "arguments"]) || !record(v.tool_call.arguments)) return false;
  const call = v.tool_call, args = call.arguments as Record<string, unknown>;
  return call.name === "get_day_schedule" ? exact(args, ["date"]) && request.dates.includes(args.date as string)
    : call.name === "get_budget_summary" ? exact(args, [])
    : call.name === "request_clarification" && exact(args, ["reason"]) && typeof args.reason === "string" && Object.hasOwn(clarificationMessages, args.reason);
}
export type LocalQueryResult = { kind: "day"; day: ScheduleDay; base: ScheduleCapture } | { kind: "budget"; costs: ScheduleCosts } | { kind: "clarification"; message: string };
export function executeReadOnlyTool(response: QueryResponse, request: QueryRequest, base: ScheduleCapture, costs: CostInputs): LocalQueryResult {
  if (!isQueryResponse(response, request) || base.version !== request.draft_version ||
      JSON.stringify(base.original.days.map(d => d.date)) !== JSON.stringify(request.dates) || !isScheduleResponse(base.original, base.original.request)) throw new Error("查询与当前草案不一致，未执行工具。");
  // Exhaustive static dispatch, never eval, dynamic property invocation or fetch.
  const call = response.tool_call;
  switch (call.name) {
    case "get_day_schedule": return { kind: "day", day: structuredClone(base.original.days.find(d => d.date === call.arguments.date)!), base };
    case "get_budget_summary": return { kind: "budget", costs: calculateScheduleCosts(base.original, base.request, costs) };
    case "request_clarification": return { kind: "clarification", message: clarificationMessages[call.arguments.reason] };
  }
}
const errors: Record<string, string> = { configuration: "千问配置暂不可用，其他功能不受影响。", authentication: "千问权限检查失败。", quota: "千问额度或账户状态不允许调用。", model_unavailable: "当前型号或工具调用方式不可用，未切换模型。", rate_limit: "千问请求受到限流，请稍后主动重试。", timeout: "只读查询超时，未执行工具。", refused: "千问拒绝本次查询，未执行工具。", truncated: "工具调用被截断，未执行。", structure: "未取得唯一有效的工具调用，本次查询已结束。", references: "日期未通过当前草案核对，本次查询已结束。" };
export async function queryItinerary(request: QueryRequest, signal: AbortSignal): Promise<QueryResponse> {
  if (signal.aborted) throw new DOMException("已取消", "AbortError");
  const controller = new AbortController(); let timedOut = false;
  const cancel = () => controller.abort(); signal.addEventListener("abort", cancel, { once: true });
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, 25000);
  try {
    const res = await fetch(API_ENDPOINTS.itineraryQuery, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(request), signal: controller.signal });
    const data: unknown = await res.json();
    if (controller.signal.aborted) throw new DOMException("已取消", "AbortError");
    if (!res.ok) throw new Error(errors[String(record(data) && record(data.detail) ? data.detail.code : "")] ?? "只读查询失败，原行程与费用未改变。");
    if (!isQueryResponse(data, request)) throw new Error("工具调用与本次查询不一致，未执行。");
    return data;
  } catch (error) {
    if (signal.aborted) throw new DOMException("已取消", "AbortError");
    if (timedOut) throw new Error(errors.timeout);
    if (error instanceof Error && error.name === "Error") throw error;
    throw new Error("无法连接只读查询服务，未执行工具。");
  } finally { clearTimeout(timer); signal.removeEventListener("abort", cancel); }
}
