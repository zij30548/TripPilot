import { API_ENDPOINTS } from "./api-endpoints";
import { apiFetch } from "./auth-session";
import { isChatCityId, type ChatCityId } from "./chat-cities";

export const CHAT_VERSION = "chat-session-v1" as const;
export const CHAT_LIST_VERSION = "chat-list-v1" as const;
export type ChatRequirements = {
  destination_id: ChatCityId | null;
  start_date: string | null;
  end_date: string | null;
  travelers: number | null;
  budget_cny: string | null;
  accommodation_hint: string | null;
  required_place_hints: string[];
  preferences: string[];
  transport_mode: "walking" | "transit" | null;
};
export type ChatMessage = { id: string; role: "user" | "assistant"; text: string; created_at: string };
export type ChatIssue = "ambiguous" | "unsupported_scope" | "unsupported_action" | "unimplemented_preference";
export type ChatSummary = { id: string; title: string; revision: number; updated_at: string };
export type ChatConversation = ChatSummary & {
  schema_version: typeof CHAT_VERSION;
  requirements: ChatRequirements;
  confirmed_revision: number | null;
  sources: { field: string; quote: string }[];
  issues: ChatIssue[];
  messages: ChatMessage[];
  last_turn: { operation_id: string; state: "running" | "succeeded" | "failed" | "cancelled" | "uncertain"; error_code: string | null } | null;
  created_at: string;
};
export type ChatList = { schema_version: typeof CHAT_LIST_VERSION; items: ChatSummary[]; next_offset: number | null };
export type ChatTurnRequest = { operation_id: string; expected_revision: number; text: string };

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const REQUIREMENT_KEYS = ["destination_id", "start_date", "end_date", "travelers", "budget_cny", "accommodation_hint", "required_place_hints", "preferences", "transport_mode"];
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
const exact = (value: Record<string, unknown>, keys: string[]) => Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const integer = (value: unknown, maximum = Number.MAX_SAFE_INTEGER): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0 && value <= maximum;
const text = (value: unknown, maximum: number, minimum = 1): value is string => typeof value === "string" && Array.from(value).length >= minimum && Array.from(value).length <= maximum && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(value);
const timestamp = (value: unknown): value is string => typeof value === "string" && value.length <= 40 && /(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value));
function date(value: unknown): value is string {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value && value >= "2000-01-01" && value <= "2100-12-31";
}
function stringList(value: unknown, count: number, length: number): value is string[] {
  return Array.isArray(value) && value.length <= count && value.every(item => text(item, length) && item.trim() === item) && new Set(value).size === value.length;
}
function budget(value: unknown): boolean {
  if (value === null) return true;
  if (typeof value !== "string" || !/^(?:0|[1-9]\d{0,12})(?:\.\d{1,2})?$/.test(value)) return false;
  const [whole, decimal = ""] = value.split(".");
  const cents = BigInt(whole) * BigInt(100) + BigInt(decimal.padEnd(2, "0"));
  return cents > BigInt(0) && cents <= BigInt(Number.MAX_SAFE_INTEGER);
}
export function isChatRequirements(value: unknown): value is ChatRequirements {
  if (!record(value) || !exact(value, REQUIREMENT_KEYS) ||
      !(value.destination_id === null || isChatCityId(value.destination_id)) ||
      !(value.start_date === null || date(value.start_date)) || !(value.end_date === null || date(value.end_date)) ||
      !(value.travelers === null || integer(value.travelers, 100) && value.travelers >= 1) || !budget(value.budget_cny) ||
      !(value.accommodation_hint === null || text(value.accommodation_hint, 120) && value.accommodation_hint.trim().length > 0) ||
      !stringList(value.required_place_hints, 6, 120) || !stringList(value.preferences, 8, 120) ||
      ![null, "walking", "transit"].includes(value.transport_mode as string | null)) return false;
  if (value.start_date !== null && value.end_date !== null) {
    const days = (Date.parse(`${value.end_date}T00:00:00Z`) - Date.parse(`${value.start_date}T00:00:00Z`)) / 86_400_000;
    if (days < 0 || days > 6) return false;
  }
  return true;
}
function isSummary(value: unknown): value is ChatSummary {
  return record(value) && exact(value, ["id", "title", "revision", "updated_at"]) &&
    typeof value.id === "string" && UUID.test(value.id) && text(value.title, 80) && integer(value.revision, 1000) && timestamp(value.updated_at);
}
export function isChatConversation(value: unknown): value is ChatConversation {
  if (!record(value) || !exact(value, ["schema_version", "id", "title", "revision", "requirements", "confirmed_revision", "sources", "issues", "messages", "last_turn", "created_at", "updated_at"]) ||
      value.schema_version !== CHAT_VERSION || typeof value.id !== "string" || !UUID.test(value.id) || !text(value.title, 80) || !integer(value.revision, 1000) ||
      !isChatRequirements(value.requirements) || !(value.confirmed_revision === null || integer(value.confirmed_revision) && value.confirmed_revision === value.revision) ||
      !timestamp(value.created_at) || !timestamp(value.updated_at) || !Array.isArray(value.messages) || value.messages.length > 40 ||
      !value.messages.every(message => record(message) && exact(message, ["id", "role", "text", "created_at"]) && typeof message.id === "string" && UUID.test(message.id) &&
        ["user", "assistant"].includes(String(message.role)) && text(message.text, 3000) && timestamp(message.created_at)) ||
      new Set(value.messages.map(message => message.id)).size !== value.messages.length ||
      !Array.isArray(value.sources) || value.sources.length > 9 || !value.sources.every(source => record(source) && exact(source, ["field", "quote"]) &&
        typeof source.field === "string" && REQUIREMENT_KEYS.includes(source.field) && text(source.quote, 300)) ||
      new Set(value.sources.map(source => source.field)).size !== value.sources.length ||
      !Array.isArray(value.issues) || value.issues.length > 4 || new Set(value.issues).size !== value.issues.length || !value.issues.every(issue => ["ambiguous", "unsupported_scope", "unsupported_action", "unimplemented_preference"].includes(String(issue)))) return false;
  return value.last_turn === null || record(value.last_turn) && exact(value.last_turn, ["operation_id", "state", "error_code"]) &&
    typeof value.last_turn.operation_id === "string" && UUID.test(value.last_turn.operation_id) &&
    ["running", "succeeded", "failed", "cancelled", "uncertain"].includes(String(value.last_turn.state)) &&
    (value.last_turn.error_code === null || typeof value.last_turn.error_code === "string" && /^[a-z0-9_]{1,80}$/.test(value.last_turn.error_code));
}
export function isChatList(value: unknown): value is ChatList {
  return record(value) && exact(value, ["schema_version", "items", "next_offset"]) && value.schema_version === CHAT_LIST_VERSION &&
    Array.isArray(value.items) && value.items.length <= 20 && value.items.every(isSummary) && new Set(value.items.map(item => item.id)).size === value.items.length &&
    (value.next_offset === null || integer(value.next_offset, 1000));
}

const ERROR_MESSAGES: Record<string, string> = {
  configuration: "对话模型尚未配置好。没有生成替代回复，也没有修改已确认需求。",
  authentication: "对话模型权限校验失败，请联系管理员。",
  quota: "当前模型账户额度不足，本次不会自动重试。",
  rate_limit: "当前调用受到限流，请按提示等待后主动操作。",
  timeout: "对话等待超时。请求可能已经发送，请先重新读取会话，不要重复发送同一句。",
  structure: "模型回复未通过结构校验，未应用本次提议。",
  references: "模型提议未通过本轮原文依据核对，未应用本次提议。",
  truncated: "模型回复不完整，未应用本次提议。",
  refused: "模型未能处理本次输入，未应用本次提议。",
  chat_unavailable: "聊天存储尚未就绪，请联系管理员按独立迁移流程准备；不会自动改写旧库。",
  chat_storage_unavailable: "聊天存储尚未就绪，请联系管理员按独立迁移流程准备；不会自动改写旧库。",
  migration_required: "聊天存储尚未升级，请管理员按独立迁移手册准备。不会自动改写旧库。",
  storage_unavailable: "聊天存储暂不可用，本次未取得有效结果。不会自动迁移或改用其他库。",
  invalid_chat_schema: "聊天存储版本不匹配，请联系管理员核对迁移；不会自动改写旧库。",
  not_found: "这份会话不存在或不属于当前浏览器工作区。",
  turn_busy: "这份会话仍有请求处理中，请取消或重新读取后确认状态。",
  operation_already_used: "这次操作已经登记，不能重复发送。请重新读取核对之前的结果。",
  turn_obsolete: "这一轮已经取消或过期，请重新读取当前会话。",
  turn_limit: "这份会话已达到20轮上限，请新建会话继续。",
  storage_limit: "当前浏览器工作区的聊天存储已达上限，请联系管理员；不会截断已有内容。",
  context_limit: "这份会话已达到输入上下文上限，请新建会话继续。不会自动截断旧消息或重新发送。",
  model_unavailable: "当前对话模型不可用，未切换其他型号。",
  model_invalid_output: "模型输出未通过校验，未应用本次提议。",
  model_timeout: "上次模型操作超时，请核对会话记录；不会自动重发。",
  model_error: "上次模型操作未完成，未应用本次提议。",
  request_uncertain: "上次请求结果不确定，请先重新读取核对；不会自动重发。",
  request_failed: "上次对话未完成，未应用本次提议。",
  conversation_not_found: "这份会话不存在或不属于当前浏览器工作区。",
  conversation_conflict: "会话已在其他操作中改变。请重新读取后核对，不会覆盖新内容。",
  revision_conflict: "会话版本已变化，请重新读取最新内容。",
  turn_running: "这份会话仍有请求处理中，请取消或重新读取后确认状态。",
  turn_uncertain: "上次请求结果尚未确定，请重新读取会话；不会自动再次调用模型。",
  conversation_limit: "已达到50份会话上限，可删除不再需要的会话后新建。",
  message_limit: "这份会话已达到40条消息上限，请新建会话继续。",
};

// Only these reviewed literals may be shown as diagnostic reason codes. Never
// interpolate an arbitrary server code, message, validator input or raw detail.
const EXECUTION_ERROR_MESSAGES: Record<string, string> = {
  tool_arguments: "模型选择的操作参数未通过校验，未执行该操作。",
  requirements_invalid: "需求提议与当前输入或本轮原话依据不一致，未应用该提议。",
  model_structure: "模型回复未通过原生工具结构校验，未应用该回复。",
  ungrounded_answer: "模型没有返回本次需要的受控工具，未把自由回复当作业务结果。",
  tool_not_allowed: "模型选择的操作不在当前允许范围内，未执行该操作。",
  tool_schema: "工具契约检查未通过，本轮已停止，请联系管理员核对。",
  travel_requirements_invalid: "需求提议与当前输入或本轮原话依据不一致，未应用该提议。",
  travel_tool_required: "模型没有返回本次需要的旅行工具，未把自由回复当作业务结果。",
  travel_multiple_tools: "模型一次提出了多项工具调用，本次未派发这些操作。",
  travel_tool_failed: "旅行工具结果未通过校验，未发布为有效业务结果。",
  travel_role_arguments: "旅行角色选择的操作参数未通过校验，未执行该操作。",
  travel_role_forbidden: "这项操作不属于当前旅行角色的权限，未执行该操作。",
  intent_structure: "意图分类回复未通过结构校验，本轮已停止。",
};

export function chatErrorMessage(code: string | null): string {
  const normalized = code?.startsWith("chat_") ? code.slice(5) : code;
  const reason = code && Object.hasOwn(EXECUTION_ERROR_MESSAGES, code) ? code
    : normalized && Object.hasOwn(EXECUTION_ERROR_MESSAGES, normalized) ? normalized : null;
  if (reason) return `${EXECUTION_ERROR_MESSAGES[reason]}原因码：${reason}。不会自动重试。`;
  return code && Object.hasOwn(ERROR_MESSAGES, code) ? ERROR_MESSAGES[code]
    : normalized && Object.hasOwn(ERROR_MESSAGES, normalized) ? ERROR_MESSAGES[normalized]
    : "本次对话未完成，请重新读取会话核对状态；不会自动重试。";
}
const MAX_BODY_BYTES = 384 * 1024;
async function boundedJson(response: Response): Promise<unknown> {
  const declared = response.headers.get("content-length");
  if (declared && Number(declared) > MAX_BODY_BYTES) throw new Error("会话响应过大，未载入。");
  if (!response.body) throw new Error("会话响应为空，未载入。");
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const next = await reader.read();
      if (next.done) break;
      total += next.value.byteLength;
      if (total > MAX_BODY_BYTES) { await reader.cancel(); throw new Error("会话响应过大，未载入。"); }
      chunks.push(next.value);
    }
  } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(total);
  let position = 0;
  for (const chunk of chunks) { bytes.set(chunk, position); position += chunk.byteLength; }
  try { return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)); }
  catch { throw new Error("会话响应格式无效，未载入。"); }
}
async function chatRequest(path: string, signal: AbortSignal, body?: unknown): Promise<unknown> {
  if (signal.aborted) throw new DOMException("已取消", "AbortError");
  const controller = new AbortController();
  let timedOut = false;
  const abort = () => controller.abort();
  signal.addEventListener("abort", abort, { once: true });
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, 25_000);
  try {
    const response = await apiFetch(`${API_ENDPOINTS.chatConversations}${path}`, {
      method: body === undefined ? "GET" : "POST", signal: controller.signal,
      ...(body === undefined ? {} : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
    });
    if (response.status === 204 && body !== undefined) return undefined;
    const value = await boundedJson(response);
    if (controller.signal.aborted) throw new DOMException("已取消", "AbortError");
    if (!response.ok) {
      const code = record(value) && record(value.detail) && typeof value.detail.code === "string" ? value.detail.code : null;
      if (response.status === 429) throw new Error("调用频率或今日额度已达上限，请按额度提示等待；不会自动重试。");
      if (response.status === 409) throw new Error(chatErrorMessage(code ?? "revision_conflict"));
      throw new Error(chatErrorMessage(code));
    }
    return value;
  } catch (error) {
    if (signal.aborted) throw new DOMException("已取消", "AbortError");
    if (timedOut) throw new Error(ERROR_MESSAGES.timeout);
    if (error instanceof Error && error.name === "Error") throw error;
    throw new Error("连接中断，尚未确认本次对话结果。请重新读取会话，不会自动重试。");
  } finally { clearTimeout(timer); signal.removeEventListener("abort", abort); }
}
function parseConversation(value: unknown, id?: string): ChatConversation {
  if (!isChatConversation(value) || id && value.id !== id) throw new Error("会话内容或身份未通过校验，未载入。");
  return value;
}
export async function listChatConversations(offset: number, signal: AbortSignal): Promise<ChatList> {
  const value = await chatRequest(`?limit=20&offset=${offset}`, signal);
  if (!isChatList(value)) throw new Error("会话列表未通过校验，未载入。");
  return value;
}
export async function createChatConversation(operationId: string, signal: AbortSignal): Promise<ChatConversation> {
  return parseConversation(await chatRequest("", signal, { operation_id: operationId }));
}
export async function readChatConversation(id: string, signal: AbortSignal): Promise<ChatConversation> {
  if (!UUID.test(id)) throw new Error("会话标识无效。");
  return parseConversation(await chatRequest(`/${id}`, signal), id);
}
export async function sendChatTurn(id: string, request: ChatTurnRequest, signal: AbortSignal): Promise<ChatConversation> {
  const value = parseConversation(await chatRequest(`/${id}/turns`, signal, request), id);
  if (value.last_turn?.operation_id !== request.operation_id || value.revision <= request.expected_revision) throw new Error("回复不属于本次对话请求，未应用。");
  return value;
}
export async function cancelChatTurn(id: string, operationId: string, signal: AbortSignal): Promise<ChatConversation> {
  const value = parseConversation(await chatRequest(`/${id}/cancel`, signal, { operation_id: operationId }), id);
  if (value.last_turn && value.last_turn.operation_id !== operationId) throw new Error("取消回执不属于本次请求，请重新读取会话。");
  return value;
}
export async function confirmChatRequirements(id: string, revision: number, operationId: string, signal: AbortSignal): Promise<ChatConversation> {
  const value = parseConversation(await chatRequest(`/${id}/confirm`, signal, { operation_id: operationId, expected_revision: revision }), id);
  if (value.confirmed_revision === null || value.revision <= revision) throw new Error("未收到当前需求版本的确认回执，请重新读取会话。");
  return value;
}
export async function deleteChatConversation(id: string, revision: number, signal: AbortSignal): Promise<void> {
  const value = await chatRequest(`/${id}/delete`, signal, { expected_revision: revision });
  if (value !== undefined) throw new Error("删除结果尚未确认，请重新读取列表。");
}
