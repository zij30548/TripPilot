import { CHAT_VERSION, type ChatConversation } from "./chat";

export const CHAT_ID = "10000000-0000-4000-8000-000000000001";
export const OP_ID = "10000000-0000-4000-8000-000000000002";
export const OTHER_CHAT_ID = "10000000-0000-4000-8000-000000000005";
export function conversationFixture(overrides: Partial<ChatConversation> = {}): ChatConversation {
  return {
    schema_version: CHAT_VERSION, id: CHAT_ID, title: "杭州旅行", revision: 2,
    requirements: { destination_id: "hangzhou", start_date: "2026-10-21", end_date: "2026-10-23", travelers: 2, budget_cny: "3000", accommodation_hint: "龙翔桥附近", required_place_hints: ["西湖"], preferences: ["带父母，想轻松一些"], transport_mode: "walking" },
    confirmed_revision: null, sources: [{ field: "destination_id", quote: "想去杭州" }], issues: ["unimplemented_preference"],
    messages: [{ id: "10000000-0000-4000-8000-000000000003", role: "user", text: "想去杭州", created_at: "2026-10-20T12:00:00Z" }, { id: "10000000-0000-4000-8000-000000000004", role: "assistant", text: "请核对下方需求意向。", created_at: "2026-10-20T12:00:01Z" }],
    last_turn: { operation_id: OP_ID, state: "succeeded", error_code: null }, created_at: "2026-10-20T12:00:00Z", updated_at: "2026-10-20T12:00:01Z", ...overrides,
  };
}
export function emptyConversation(id = CHAT_ID): ChatConversation {
  return conversationFixture({ id, title: "新的旅行", revision: 0, requirements: { destination_id: null, start_date: null, end_date: null, travelers: null, budget_cny: null, accommodation_hint: null, required_place_hints: [], preferences: [], transport_mode: null }, sources: [], issues: [], messages: [], last_turn: null });
}
export function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
