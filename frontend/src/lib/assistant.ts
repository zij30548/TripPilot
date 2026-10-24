import { API_ENDPOINTS } from "./api-endpoints";
import { apiFetch } from "./auth-session";
import { chatErrorMessage, isChatConversation, type ChatConversation, type ChatTurnRequest } from "./chat";

export const ASSISTANT_STATE_VERSION = "assistant-state-v1" as const;
export type AssistantTaskState = "queued" | "running" | "waiting" | "awaiting_confirmation" | "paused" | "succeeded" | "failed" | "cancelled" | "uncertain";
export type AssistantState = {
  schema_version: typeof ASSISTANT_STATE_VERSION;
  conversation_id: string;
  revision: number;
  task: null | {
    id: string;
    state: AssistantTaskState;
    model_calls: number;
    tool_calls: number;
    stop_reason: string | null;
    steps: { name: string; state: AssistantTaskState | "skipped"; elapsed_ms: number }[];
  };
  preferences: [];
  skills: { id: string; name: string; version: string }[];
  migration_ready: boolean;
};
export type TravelExecutionReceipt = {
  schema_version: "travel-execution-receipt-v1";
  budget: { version: "travel-task-budget-v1"; tier: "single" | "collaboration" | "reference"; model_limit: number; tool_limit: number;
    output_tokens_reserved_limit: number; deadline_seconds: number; shared: true; real_call_authorization: false };
  reservations: { models: number; tools: number; output_tokens: number };
  dispatch: { status: "observed" | "unknown"; sent: number | null; received: number | null; failed: number | null; cancelled: number | null; uncertain: number | null };
  trace_complete: boolean;
};
export type AssistantStateV2 = Omit<AssistantState, "schema_version" | "task"> & {
  schema_version: "assistant-state-v2";
  task: null | (NonNullable<AssistantState["task"]> & { execution_receipt: TravelExecutionReceipt | null });
};
export type VersionedAssistantState = AssistantState | AssistantStateV2;

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const STATES = ["queued", "running", "waiting", "awaiting_confirmation", "paused", "succeeded", "failed", "cancelled", "uncertain"];
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
const exact = (value: Record<string, unknown>, keys: string[]) => Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const integer = (value: unknown, max: number): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0 && value <= max;
const text = (value: unknown, max: number): value is string => typeof value === "string" && value.trim().length > 0 && Array.from(value).length <= max && !/[\u0000-\u001f\u007f]/.test(value);
const code = (value: unknown, max = 80): value is string => typeof value === "string" && new RegExp(`^[a-zA-Z0-9_.-]{1,${max}}$`).test(value);

export function isAssistantState(value: unknown): value is AssistantState {
  if (!record(value) || !exact(value, ["schema_version", "conversation_id", "revision", "task", "preferences", "skills", "migration_ready"]) ||
      value.schema_version !== ASSISTANT_STATE_VERSION || typeof value.conversation_id !== "string" || !UUID.test(value.conversation_id) ||
      !integer(value.revision, 1000) || typeof value.migration_ready !== "boolean" || !Array.isArray(value.preferences) || value.preferences.length !== 0 ||
      !Array.isArray(value.skills) || value.skills.length > 32 || !value.skills.every(skill => record(skill) && exact(skill, ["id", "name", "version"]) &&
        code(skill.id) && text(skill.name, 80) && code(skill.version)) || new Set(value.skills.map(skill => skill.id)).size !== value.skills.length) return false;
  if (value.task === null) return true;
  const task = value.task;
  return record(task) && exact(task, ["id", "state", "model_calls", "tool_calls", "stop_reason", "steps"]) &&
    typeof task.id === "string" && UUID.test(task.id) && STATES.includes(String(task.state)) && integer(task.model_calls, 8) && integer(task.tool_calls, 8) &&
    (task.stop_reason === null || code(task.stop_reason)) && Array.isArray(task.steps) && task.steps.length <= 8 && task.steps.every(step =>
      record(step) && exact(step, ["name", "state", "elapsed_ms"]) && text(step.name, 80) && [...STATES, "skipped"].includes(String(step.state)) && integer(step.elapsed_ms, 120_000));
}

/** Explicit new contract; it never broadens the legacy v1 parser or its limits. */
export function isAssistantStateV2(value: unknown): value is AssistantStateV2 {
  if (!record(value) || value.schema_version !== "assistant-state-v2" ||
      !isAssistantState({ ...value, schema_version: ASSISTANT_STATE_VERSION, task: null })) return false;
  if (value.task === null) return true;
  const task = value.task;
  if (!record(task) || !exact(task, ["id", "state", "model_calls", "tool_calls", "stop_reason", "steps", "execution_receipt"]) ||
      typeof task.id !== "string" || !UUID.test(task.id) || !STATES.includes(String(task.state)) || !integer(task.model_calls, 16) || !integer(task.tool_calls, 16) ||
      !(task.stop_reason === null || code(task.stop_reason)) || !Array.isArray(task.steps) || task.steps.length > 100 || !task.steps.every(step =>
        record(step) && exact(step, ["name", "state", "elapsed_ms"]) && text(step.name, 80) && [...STATES, "skipped"].includes(String(step.state)) && integer(step.elapsed_ms, 120_000))) return false;
  const receipt = task.execution_receipt;
  if (receipt === null) return true; // A legacy task has no trustworthy new aggregate.
  if (!record(receipt) || !exact(receipt, ["schema_version", "budget", "reservations", "dispatch", "trace_complete"]) ||
      receipt.schema_version !== "travel-execution-receipt-v1" || typeof receipt.trace_complete !== "boolean") return false;
  const b = receipt.budget, r = receipt.reservations, d = receipt.dispatch;
  if (!record(b) || !exact(b, ["version", "tier", "model_limit", "tool_limit", "output_tokens_reserved_limit", "deadline_seconds", "shared", "real_call_authorization"]) ||
      b.version !== "travel-task-budget-v1" || b.shared !== true || b.real_call_authorization !== false) return false;
  const limits: Record<string, readonly number[]> = { single: [6, 8, 8192, 60], collaboration: [10, 12, 16384, 90], reference: [16, 16, 24576, 90] };
  const limit = typeof b.tier === "string" && Object.hasOwn(limits, b.tier) ? limits[b.tier] : null;
  if (!limit || [b.model_limit, b.tool_limit, b.output_tokens_reserved_limit, b.deadline_seconds].some((v, i) => v !== limit[i]) ||
      !record(r) || !exact(r, ["models", "tools", "output_tokens"]) || !integer(r.models, limit[0]) || !integer(r.tools, limit[1]) || !integer(r.output_tokens, limit[2]) ||
      task.model_calls !== r.models || task.tool_calls !== r.tools || !record(d) || !exact(d, ["status", "sent", "received", "failed", "cancelled", "uncertain"])) return false;
  const counts = [d.sent, d.received, d.failed, d.cancelled, d.uncertain];
  if (d.status === "unknown") return counts.every(v => v === null);
  return d.status === "observed" && counts.every(v => integer(v, r.models as number)) &&
    Number(d.received) + Number(d.failed) + Number(d.cancelled) + Number(d.uncertain) === d.sent;
}

function assistantError(code: string | null): string {
  if (code === "assistant_full_runtime_not_ready") {
    return "完整旅行助手的模板、索引或运行资源尚未准备好。没有登记任务或发送模型，也不会自动切换旧引擎。请联系管理员核对。";
  }
  if (code === "assistant_runtime_dependencies_unavailable") {
    return "当前旅行助手运行依赖尚未准备好，请联系管理员核对。没有发送模型，也没有自动切回旧引擎。";
  }
  if (code === "assistant_runtime_surface_required") {
    return "当前运行时仅支持正式对话入口，请核对页面与服务版本。没有切换旧接口或生成替代回复。";
  }
  if (["assistant_migration_required", "assistant_storage_unavailable", "assistant_not_ready"].includes(code ?? "")) {
    return "旅行助手存储尚未准备好，请联系管理员完成独立迁移。没有生成替代回复。";
  }
  if (code === "assistant_budget_exhausted") return "这次任务已达到执行上限，已停止。请查看已完成的步骤，不会自动继续调用。";
  // This is a finite local message/code lookup. Never render the backend's
  // arbitrary message/diagnostic/input or echo an unrecognized reason string.
  return chatErrorMessage(code?.startsWith("assistant_") ? code.slice(10) : code);
}

export type DialogueSelectionContext = { workspace_version: string; display: "none" | "candidates" | "schedule" | "preview" | "budget" | "history" | "places";
  items: { ordinal: number; id: string; name: string; type: "place" | "saved" | "date" }[] };
export type DialogueTurnRequest = ChatTurnRequest & { dialogue_context?: DialogueSelectionContext };
async function request(path: string, signal: AbortSignal, body?: DialogueTurnRequest, surface?: "dialogue-v1" | "dialogue-v2" | "dialogue-v3"): Promise<unknown> {
  if (signal.aborted) throw new DOMException("已取消", "AbortError");
  const controller = new AbortController();
  const abort = () => controller.abort();
  let timedOut = false;
  const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, surface === "dialogue-v3" ? 105_000 : 65_000);
  signal.addEventListener("abort", abort, { once: true });
  try {
    const response = await apiFetch(`${API_ENDPOINTS.chatConversations}${path}`, {
      method: body ? "POST" : "GET", signal: controller.signal,
      ...(body || surface ? { headers: { ...(body ? { "Content-Type": "application/json" } : {}), ...(surface ? { "X-TripPilot-Surface": surface } : {}) } } : {}),
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
    // v3 may carry <=96KiB complete program text, whose JSON escapes can double
    // wire size; this remains bounded, while the old 64KiB read limit is intact.
    const limit = body ? 384 * 1024 : surface === "dialogue-v3" ? 256 * 1024 : 64 * 1024;
    const declared = response.headers.get("content-length");
    if (declared && (!/^\d+$/.test(declared) || Number(declared) > limit) || !response.body) throw new Error("助手响应大小无效，未载入。");
    const reader = response.body.getReader(), chunks: Uint8Array[] = [];
    let total = 0;
    try {
      while (true) {
        const next = await reader.read();
        if (next.done) break;
        total += next.value.byteLength;
        if (total > limit) { await reader.cancel(); throw new Error("助手响应过大，未载入。"); }
        chunks.push(next.value);
      }
    } finally { reader.releaseLock(); }
    const bytes = new Uint8Array(total);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    let value: unknown;
    try { value = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)); }
    catch { throw new Error("助手响应格式无效，未载入。"); }
    if (controller.signal.aborted) throw new DOMException("已取消", "AbortError");
    if (!response.ok) {
      if (response.status === 429) throw new Error("调用频率或今日额度已达上限，请按额度提示等待；不会自动重试。");
      const code = record(value) && record(value.detail) && typeof value.detail.code === "string" ? value.detail.code : null;
      if (response.status === 404 && code === null) throw new Error("旅行助手接口尚未就绪，请核对当前服务版本。没有切换旧接口或生成替代回复。");
      throw new Error(assistantError(code));
    }
    return value;
  } catch (error) {
    if (signal.aborted) throw new DOMException("已取消", "AbortError");
    if (timedOut) throw new Error("旅行助手等待超时。任务可能已经开始，请重新读取会话核对，不要重复发送。");
    if (error instanceof Error && error.name === "Error") throw error;
    throw new Error("旅行助手连接中断，请重新读取会话核对；不会自动重试。");
  } finally { clearTimeout(timeout); signal.removeEventListener("abort", abort); }
}

export async function sendAssistantTurn(id: string, body: ChatTurnRequest, signal: AbortSignal): Promise<ChatConversation> {
  return sendBoundTurn(id, body, signal);
}

/** The minimal dialogue only offers tools with a usable text-only outcome. */
export async function sendDialogueTurn(id: string, body: ChatTurnRequest, signal: AbortSignal): Promise<ChatConversation> {
  return sendBoundTurn(id, body, signal, "dialogue-v1");
}

export async function sendToolDialogueTurn(id: string, body: DialogueTurnRequest, signal: AbortSignal): Promise<ChatConversation> {
  return sendBoundTurn(id, body, signal, "dialogue-v2");
}

export async function sendFullDialogueTurn(id: string, body: DialogueTurnRequest, signal: AbortSignal): Promise<ChatConversation> {
  if (!body.dialogue_context || !UUID.test(body.dialogue_context.workspace_version)) throw new Error("缺少当前工作区绑定，没有发送请求。");
  return sendBoundTurn(id, body, signal, "dialogue-v3");
}

export async function readDialogueActionReceipt(id: string, signal: AbortSignal): Promise<unknown> {
  if (!UUID.test(id)) throw new Error("会话标识无效。");
  return request(`/${id}/dialogue-action`, signal);
}

export async function readFullDialogueActionReceipt(id: string, signal: AbortSignal): Promise<unknown> {
  if (!UUID.test(id)) throw new Error("会话标识无效。");
  return request(`/${id}/dialogue-action`, signal, undefined, "dialogue-v3");
}

async function sendBoundTurn(id: string, body: DialogueTurnRequest, signal: AbortSignal, surface?: "dialogue-v1" | "dialogue-v2" | "dialogue-v3"): Promise<ChatConversation> {
  if (!UUID.test(id)) throw new Error("会话标识无效。");
  const value = await request(`/${id}/assistant-turns`, signal, body, surface);
  if (!isChatConversation(value) || value.id !== id || value.last_turn?.operation_id !== body.operation_id || value.revision <= body.expected_revision) {
    throw new Error("助手回复不属于本次会话请求，未应用。");
  }
  return value;
}

export async function readAssistantState(id: string, revision: number, signal: AbortSignal): Promise<AssistantState> {
  if (!UUID.test(id)) throw new Error("会话标识无效。");
  const value = await request(`/${id}/assistant-state`, signal);
  if (!isAssistantState(value) || value.conversation_id !== id || value.revision !== revision) throw new Error("执行记录与当前会话版本不一致，请先重新读取会话。");
  return value;
}

export async function readVersionedAssistantState(id: string, revision: number, signal: AbortSignal): Promise<VersionedAssistantState> {
  if (!UUID.test(id)) throw new Error("会话标识无效。");
  const value = await request(`/${id}/assistant-state`, signal, undefined, "dialogue-v3");
  if ((!isAssistantStateV2(value) && !isAssistantState(value)) || value.conversation_id !== id || value.revision !== revision) {
    throw new Error("执行记录与当前会话版本不一致，请先重新读取会话。");
  }
  return value;
}
