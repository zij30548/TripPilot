import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import ConversationWorkspace from "./conversation-workspace";
import { CHAT_ID, conversationFixture, deferred, emptyConversation } from "@/lib/chat-test-fixtures";

const reply = (value: unknown) => new Response(JSON.stringify(value), {
  headers: { "Content-Type": "application/json" },
});

describe("controlled composer submission diagnostics", () => {
  it.each(["creating", "sending"])("one button click does not resend during %s, completion, or an empty later submit", async delayed => {
    const gate = deferred<void>();
    const question = "想去杭州，带父母轻松玩几天";
    let operation = "", workspace = "";
    const fetcher = vi.fn(async (url: string, init: RequestInit = {}) => {
      if (url.includes("?limit=")) return reply({ schema_version: "chat-list-v1", items: [], next_offset: null });
      if (url === "/api/chat/conversations" && init.method === "POST") {
        if (delayed === "creating") await gate.promise;
        return reply(emptyConversation());
      }
      if (url.endsWith("/assistant-turns")) {
        const body = JSON.parse(String(init.body));
        operation = body.operation_id; workspace = body.dialogue_context.workspace_version;
        if (delayed === "sending") await gate.promise;
        return reply(conversationFixture({ messages: [
          { id: crypto.randomUUID(), role: "user", text: body.text, created_at: "2026-10-20T12:01:00Z" },
          { id: crypto.randomUUID(), role: "assistant", text: "已记录杭州；日期、人数与预算尚需确认。", created_at: "2026-10-20T12:01:01Z" },
        ], last_turn: { operation_id: operation, state: "succeeded", error_code: null } }));
      }
      if (url.endsWith("/dialogue-action")) return reply({ schema_version: "dialogue-actions-v2", conversation_id: CHAT_ID,
        revision: 2, operation_id: operation, workspace_version: workspace, actions: [], program_messages: [] });
      throw new Error("Unexpected controlled request");
    });
    vi.stubGlobal("fetch", fetcher);
    render(<ConversationWorkspace />);
    await waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
    const input = screen.getByRole("textbox", { name: "告诉我你的旅行想法" }) as HTMLTextAreaElement;
    const form = screen.getByRole("form", { name: "发送旅行需求" });
    Object.defineProperty(input.form, "requestSubmit", { configurable: true, value: () => fireEvent.submit(form) });
    fireEvent.change(input, { target: { value: question } });
    const button = screen.getByRole("button", { name: "发送旅行需求" });
    act(() => { fireEvent.click(button); fireEvent.click(button); fireEvent.submit(form); fireEvent.keyDown(input, { key: "Enter" }); });
    await screen.findByRole("button", { name: "停止本次旅行请求" });
    expect(input.value).toBe("");
    await act(async () => gate.resolve());
    await screen.findByText("已记录杭州；日期、人数与预算尚需确认。");
    await waitFor(() => expect(screen.getByRole("button", { name: "发送旅行需求" }).hasAttribute("disabled")).toBe(true));
    fireEvent.submit(form);
    fireEvent.keyDown(input, { key: "Enter" });
    await act(async () => { await Promise.resolve(); });
    expect(fetcher.mock.calls.filter(([url]) => url.endsWith("/assistant-turns"))).toHaveLength(1);
    expect(fetcher.mock.calls.filter(([url]) => url === "/api/chat/conversations")).toHaveLength(1);
    expect(fetcher.mock.calls.filter(([url]) => url.endsWith("/dialogue-action"))).toHaveLength(1);
    expect(screen.queryByText("正在发送")).toBeNull();
    expect(input.value).toBe("");
  });
});
