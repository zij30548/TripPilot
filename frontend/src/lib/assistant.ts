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

function assistantError(code: string | null): string {
  if (["assistant_migration_required", "assistant_storage_unavailable", "assistant_not_ready"].includes(code ?? "")) {
    return "旅行助手存储尚未准备好，请联系管理员完成独立迁移。没有生成替代回复。";
  }
  if (code === "assistant_budget_exhausted") return "这次任务已达到执行上限，已停止。请查看已完成的步骤，不会自动继续调用。";
  return chatErrorMessage(code?.startsWith("assistant_") ? code.slice(10) : code);
}

async function request(path: string, signal: AbortSignal, body?: ChatTurnRequest, dialogue = false): Promise<unknown> {
  if (signal.aborted) throw new DOMException("已取消", "AbortError");
  const controller = new AbortController();
  const abort = () => controller.abort();
  let timedOut = false;
  const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, 65_000);
  signal.addEventListener("abort", abort, { once: true });
  try {
    const response = await apiFetch(`${API_ENDPOINTS.chatConversations}${path}`, {
      method: body ? "POST" : "GET", signal: controller.signal,
      ...(body ? { headers: { "Content-Type": "application/json", ...(dialogue ? { "X-TripPilot-Surface": "dialogue-v1" } : {}) }, body: JSON.stringify(body) } : {}),
    });
    const limit = body ? 384 * 1024 : 64 * 1024;
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
  return sendBoundTurn(id, body, signal, false);
}

/** The minimal dialogue only offers tools with a usable text-only outcome. */
export async function sendDialogueTurn(id: string, body: ChatTurnRequest, signal: AbortSignal): Promise<ChatConversation> {
  return sendBoundTurn(id, body, signal, true);
}

async function sendBoundTurn(id: string, body: ChatTurnRequest, signal: AbortSignal, dialogue: boolean): Promise<ChatConversation> {
  if (!UUID.test(id)) throw new Error("会话标识无效。");
  const value = await request(`/${id}/assistant-turns`, signal, body, dialogue);
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
