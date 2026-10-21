import { describe, expect, it, vi } from "vitest";
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import {
  CHAT_LIST_VERSION, confirmChatRequirements, createChatConversation, deleteChatConversation,
  isChatConversation, isChatList, isChatRequirements, readChatConversation, sendChatTurn,
} from "./chat";
import { CHAT_ID, OP_ID, conversationFixture } from "./chat-test-fixtures";
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });

describe("chat intent response contract", () => {
  it("accepts actual Python-generated strict conversation/list contracts across all six destinations", () => {
    const backend = resolve(process.cwd(), "../backend");
    const exported = JSON.parse(execFileSync(resolve(backend, ".venv/bin/python"), ["-B", "-c", `
import json
from core.schemas.chat import Conversation, ConversationList
from core.destinations import DESTINATIONS
rows = []
for destination_id in DESTINATIONS:
    row = Conversation.model_validate({
      "id":"10000000-0000-4000-8000-000000000001", "title":"受控契约样本", "revision":2,
      "requirements":{"destination_id":destination_id, "start_date":"2028-02-28", "end_date":"2028-03-05", "travelers":100,
        "budget_cny":"9999999999999.99", "accommodation_hint":"待核对名称", "required_place_hints":[], "preferences":[], "transport_mode":"transit"},
      "confirmed_revision":None, "sources":[], "issues":[], "messages":[{"id":"10000000-0000-4000-8000-000000000002", "role":"assistant", "text":"仅为受控契约，不是模型结果。", "created_at":"2026-10-20T00:00:00Z"}],
      "last_turn":{"operation_id":"10000000-0000-4000-8000-000000000003", "state":"succeeded", "error_code":None},
      "created_at":"2026-10-20T00:00:00Z", "updated_at":"2026-10-20T00:00:00Z"})
    rows.append(row.model_dump(mode="json"))
listing = ConversationList.model_validate({"items":[{key:rows[0][key] for key in ("id","title","revision","updated_at")}], "next_offset":None})
print(json.dumps({"rows":rows,"listing":listing.model_dump(mode="json")}))
`], { cwd: backend, encoding: "utf8", env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1", QWEN_API_KEY: "" } }));
    expect(exported.rows).toHaveLength(6);
    for (const row of exported.rows) expect(isChatConversation(row)).toBe(true);
    expect(isChatList(exported.listing)).toBe(true);
  });
  it("accepts incomplete intent without constructing a Place or schedulable request", () => {
    const full = conversationFixture();
    expect(isChatConversation(full)).toBe(true);
    expect(isChatRequirements({ ...full.requirements, destination_id: null, accommodation_hint: null, start_date: null })).toBe(true);
    expect(isChatRequirements({ ...full.requirements, travelers: 100, budget_cny: "9999999999999.99" })).toBe(true);
    expect(isChatRequirements({ ...full.requirements, start_date: "2028-02-28", end_date: "2028-03-05" })).toBe(true);
  });
  it("rejects extra business facts, invalid calendar dates, unsupported cities and unsafe money", () => {
    const requirements = conversationFixture().requirements;
    for (const patch of [{ destination_id: "paris" }, { travelers: 0 }, { travelers: 101 }, { start_date: "2026-02-29" }, { start_date: "2026-10-21", end_date: "2026-10-28" }, { budget_cny: "0" }, { budget_cny: "01" }, { budget_cny: "1.001" }, { budget_cny: "99999999999999" }, { poi_id: "fake" }, { required_place_hints: ["西湖", "西湖"] }, { preferences: Array(9).fill("偏好") }]) {
      expect(isChatRequirements({ ...requirements, ...patch })).toBe(false);
    }
  });
  it("rejects duplicate messages, wrong versions, unknown sources/issues and unbounded text", () => {
    const full = conversationFixture();
    for (const patch of [{ schema_version: "next" }, { id: "unbound" }, { revision: 1001 }, { confirmed_revision: 3 }, { sources: [{ field: "owner_id", quote: "x" }] }, { issues: ["model_will_book"] }, { messages: [...full.messages, full.messages[0]] }, { title: "x".repeat(81) }, { messages: [{ ...full.messages[0], text: "x".repeat(3001) }] }, { owner_id: "forged" }]) expect(isChatConversation({ ...full, ...patch })).toBe(false);
  });
  it("strictly validates bounded list summaries without exposing conversation bodies", () => {
    const full = conversationFixture(), item = { id: full.id, title: full.title, revision: full.revision, updated_at: full.updated_at };
    const list = { schema_version: CHAT_LIST_VERSION, items: [item], next_offset: 20 };
    expect(isChatList(list)).toBe(true);
    expect(isChatList({ ...list, items: [{ ...item, messages: [] }] })).toBe(false);
    expect(isChatList({ ...list, items: [item, item] })).toBe(false);
    expect(isChatList({ ...list, next_offset: 1001 })).toBe(false);
  });
});

describe("same-origin authenticated chat transport", () => {
  it("sends one explicit request, binds revision/op, and does not trust raw error text", async () => {
    const full = conversationFixture();
    const fetcher = vi.fn().mockResolvedValueOnce(response(full)).mockResolvedValueOnce(response({ detail: { code: "structure", message: "UNSAFE_RAW_MODEL_BODY" } }, 502));
    vi.stubGlobal("fetch", fetcher);
    const request = { operation_id: OP_ID, expected_revision: 0, text: "想去杭州" };
    expect(await sendChatTurn(CHAT_ID, request, new AbortController().signal)).toEqual(full);
    expect(fetcher.mock.calls[0][0]).toBe(`/api/chat/conversations/${CHAT_ID}/turns`);
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual(request);
    expect(fetcher.mock.calls[0][1].credentials).toBe("same-origin");
    await expect(sendChatTurn(CHAT_ID, request, new AbortController().signal)).rejects.toThrow("结构校验");
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it("rejects wrong conversation, operation, confirmation and old version", async () => {
    const full = conversationFixture();
    const fetcher = vi.fn().mockResolvedValueOnce(response({ ...full, id: OP_ID })).mockResolvedValueOnce(response({ ...full, last_turn: { ...full.last_turn, operation_id: CHAT_ID } })).mockResolvedValueOnce(response({ ...full, revision: 0 })).mockResolvedValueOnce(response(full));
    vi.stubGlobal("fetch", fetcher);
    await expect(readChatConversation(CHAT_ID, new AbortController().signal)).rejects.toThrow("身份");
    const request = { operation_id: OP_ID, expected_revision: 0, text: "杭州" };
    await expect(sendChatTurn(CHAT_ID, request, new AbortController().signal)).rejects.toThrow("本次");
    await expect(sendChatTurn(CHAT_ID, request, new AbortController().signal)).rejects.toThrow("本次");
    await expect(confirmChatRequirements(CHAT_ID, 1, OP_ID, new AbortController().signal)).rejects.toThrow("确认回执");
  });
  it("checks abort both before send and during body read; cancellation never retries", async () => {
    const before = new AbortController(); before.abort();
    await expect(createChatConversation(OP_ID, before.signal)).rejects.toHaveProperty("name", "AbortError");
    expect(fetch).not.toHaveBeenCalled();
    const controller = new AbortController();
    const stream = new ReadableStream<Uint8Array>({ start: output => { output.enqueue(new TextEncoder().encode("{")); }, cancel: () => undefined });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(stream)));
    const pending = readChatConversation(CHAT_ID, controller.signal);
    await Promise.resolve(); await Promise.resolve();
    controller.abort();
    await expect(pending).rejects.toHaveProperty("name", "AbortError");
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("caps actual streamed response bytes, rejects unknown markup fields and handles204delete", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(response({ ...conversationFixture(), html: "<script>" })).mockResolvedValueOnce(new Response("x".repeat(384 * 1024 + 1))).mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetcher);
    await expect(readChatConversation(CHAT_ID, new AbortController().signal)).rejects.toThrow("校验");
    await expect(readChatConversation(CHAT_ID, new AbortController().signal)).rejects.toThrow("过大");
    await expect(deleteChatConversation(CHAT_ID, 2, new AbortController().signal)).resolves.toBeUndefined();
    expect(fetcher.mock.calls[2][0]).toBe(`/api/chat/conversations/${CHAT_ID}/delete`);
  });
});
