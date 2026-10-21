import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ConversationWorkspace from "./conversation-workspace";
import { CHAT_ID, conversationFixture, deferred, emptyConversation } from "@/lib/chat-test-fixtures";

const reply = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
const list = () => ({ schema_version: "chat-list-v1", items: [{ id: CHAT_ID, title: "杭州旅行", revision: 2, updated_at: "2026-10-20T12:00:01Z" }], next_offset: null });
let fetcher: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetcher = vi.fn().mockImplementation(async (url: string, init: RequestInit = {}) => {
    if (url.includes("?limit=")) return reply(list());
    if (url === "/api/chat/conversations" && init.method === "POST") return reply(emptyConversation());
    if (url.endsWith("/assistant-turns")) {
      const body = JSON.parse(init.body as string);
      return reply(conversationFixture({ last_turn: { operation_id: body.operation_id, state: "succeeded", error_code: null } }));
    }
    if (url.endsWith("/cancel")) {
      const body = JSON.parse(init.body as string);
      return reply(conversationFixture({ revision: 3, last_turn: { operation_id: body.operation_id, state: "cancelled", error_code: null } }));
    }
    if (url.endsWith("/delete")) return new Response(null, { status: 204 });
    if (url === `/api/chat/conversations/${CHAT_ID}`) return reply(conversationFixture());
    throw new Error(`Unexpected controlled request: ${url}`);
  });
  vi.stubGlobal("fetch", fetcher);
});

describe("plain conversational workspace", () => {
  it("only reads the conversation list initially; no planner, places, preferences or execution controllers mount", async () => {
    render(<ConversationWorkspace />);
    await screen.findByRole("button", { name: "杭州旅行" });
    expect(screen.getByRole("heading", { name: "下一站，想去哪里？" })).toBeTruthy();
    expect(screen.getByText("从一个想法开始，慢慢把旅行聊清楚。")).toBeTruthy();
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toBe("/api/chat/conversations?limit=20&offset=0");
    for (const name of [/需求卡/, /地点/, /行程工具/, /执行记录/, /偏好/, /生成行程/, /查询天气/]) expect(screen.queryByRole("button", { name })).toBeNull();
    expect(screen.getByText("AI内容请核实 · 当前对话不会自动生成或修改行程")).toBeTruthy();
  });

  it("suggestions only fill the composer; one explicit submit uses the dialogue header once", async () => {
    render(<ConversationWorkspace />);
    await screen.findByRole("button", { name: "杭州旅行" });
    fireEvent.click(screen.getByRole("button", { name: /想去杭州，带父母轻松玩几天/ }));
    const input = screen.getByRole("textbox", { name: "告诉我你的旅行想法" }) as HTMLTextAreaElement;
    expect(input.value).toBe("想去杭州，带父母轻松玩几天");
    expect(fetcher).toHaveBeenCalledTimes(1);
    fireEvent.submit(screen.getByRole("form", { name: "发送旅行需求" }));
    await screen.findByRole("list", { name: "对话消息" });
    const sent = fetcher.mock.calls.filter(([url]) => String(url).endsWith("/assistant-turns"));
    expect(sent).toHaveLength(1);
    expect(new Headers(sent[0][1].headers).get("X-TripPilot-Surface")).toBe("dialogue-v1");
    expect(JSON.parse(sent[0][1].body).text).toBe("想去杭州，带父母轻松玩几天");
    expect(fetcher.mock.calls.every(([url]) => String(url).startsWith("/api/chat/conversations"))).toBe(true);
    expect(fetcher.mock.calls.some(([url]) => /places|preferences|assistant-state|schedule|saved-trips/.test(String(url)))).toBe(false);
  });

  it("Enter sends the synchronous draft; Shift+Enter and composition Enter do not", async () => {
    render(<ConversationWorkspace />);
    await screen.findByRole("button", { name: "杭州旅行" });
    const input = screen.getByRole("textbox", { name: "告诉我你的旅行想法" }) as HTMLTextAreaElement;
    Object.defineProperty(input.form, "requestSubmit", { configurable: true, value: () => fireEvent.submit(input.form!) });
    fireEvent.change(input, { target: { value: "先聊杭州" } });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
    fireEvent.keyDown(input, { key: "Enter", isComposing: true, keyCode: 229 });
    expect(fetcher).toHaveBeenCalledTimes(1);
    await act(async () => { fireEvent.change(input, { target: { value: "北京周末怎么逛" } }); fireEvent.keyDown(input, { key: "Enter" }); });
    const sent = fetcher.mock.calls.filter(([url]) => String(url).endsWith("/assistant-turns"));
    expect(sent).toHaveLength(1);
    expect(JSON.parse(sent[0][1].body).text).toBe("北京周末怎么逛");
  });

  it("shows model HTML as ordinary text with no executable images, links or cards", async () => {
    const full = conversationFixture();
    full.messages[1].text = "<img src=x onerror=alert(1)> https://example.com **普通文字**";
    fetcher.mockImplementation(async (url: string) => url.includes("?limit=") ? reply(list()) : reply(full));
    const view = render(<ConversationWorkspace />);
    fireEvent.click(await screen.findByRole("button", { name: "杭州旅行" }));
    expect(await screen.findByText(full.messages[1].text)).toBeTruthy();
    expect(view.container.querySelector("img, a, script")).toBeNull();
    expect(screen.queryByRole("region", { name: "旅行需求意向卡" })).toBeNull();
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it.each(["success", "error"])("cancel keeps a late %s from replacing the cancelled conversation", async outcome => {
    const late = deferred<Response>();
    fetcher.mockImplementation(async (url: string, init: RequestInit = {}) => {
      if (url.includes("?limit=")) return reply(list());
      if (url.endsWith("/assistant-turns")) return late.promise;
      if (url.endsWith("/cancel")) {
        const body = JSON.parse(init.body as string);
        return reply(conversationFixture({ revision: 3, last_turn: { operation_id: body.operation_id, state: "cancelled", error_code: null } }));
      }
      return reply(conversationFixture());
    });
    render(<ConversationWorkspace />);
    fireEvent.click(await screen.findByRole("button", { name: "杭州旅行" }));
    await screen.findByRole("list", { name: "对话消息" });
    fireEvent.change(screen.getByRole("textbox", { name: "告诉我你的旅行想法" }), { target: { value: "继续聊" } });
    fireEvent.submit(screen.getByRole("form", { name: "发送旅行需求" }));
    await screen.findByRole("button", { name: "停止本次旅行请求" });
    const sent = fetcher.mock.calls.find(([url]) => String(url).endsWith("/assistant-turns"))!;
    fireEvent.click(screen.getByRole("button", { name: "停止本次旅行请求" }));
    await screen.findByText(/已取消本页等待/);
    expect(sent[1].signal.aborted).toBe(true);
    await act(async () => {
      if (outcome === "error") late.reject(new Error("late private error"));
      else late.resolve(reply(conversationFixture({ title: "旧结果", messages: [{ ...conversationFixture().messages[1], text: "不该显示的旧回复" }] })));
    });
    expect(screen.queryByText("不该显示的旧回复")).toBeNull();
    expect(screen.queryByText("late private error")).toBeNull();
    expect(fetcher.mock.calls.filter(([url]) => String(url).endsWith("/assistant-turns"))).toHaveLength(1);
  });

  it("uncertain results require explicit readback and never cause automatic resend", async () => {
    fetcher.mockImplementation(async (url: string) => {
      if (url.includes("?limit=")) return reply(list());
      if (url.endsWith("/assistant-turns")) throw new Error("受控连接中断");
      return reply(conversationFixture());
    });
    render(<ConversationWorkspace />);
    fireEvent.click(await screen.findByRole("button", { name: "杭州旅行" }));
    await screen.findByRole("list", { name: "对话消息" });
    fireEvent.change(screen.getByRole("textbox", { name: "告诉我你的旅行想法" }), { target: { value: "接着说" } });
    fireEvent.submit(screen.getByRole("form", { name: "发送旅行需求" }));
    await screen.findByRole("alert");
    expect(screen.getByRole("button", { name: "发送旅行需求" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByText(/上次请求结果待核对/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "对话操作" }));
    fireEvent.click(screen.getByRole("button", { name: "重新读取" }));
    await screen.findByText(/已读取服务端保存的最新会话/);
    expect(fetcher.mock.calls.filter(([url]) => String(url).endsWith("/assistant-turns"))).toHaveLength(1);
  });

  it("deletion is explicit and the mobile drawer closes with Escape without requests", async () => {
    render(<ConversationWorkspace />);
    await screen.findByRole("button", { name: "杭州旅行" });
    fireEvent.click(screen.getByRole("button", { name: "打开旅行对话列表" }));
    expect(screen.getByRole("dialog", { name: "我的对话" })).toBeTruthy();
    fireEvent.keyDown(screen.getByRole("dialog", { name: "我的对话" }), { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(fetcher).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "杭州旅行" }));
    await screen.findByRole("list", { name: "对话消息" });
    fireEvent.click(screen.getByRole("button", { name: "对话操作" }));
    fireEvent.click(screen.getByRole("button", { name: "删除对话" }));
    expect(fetcher.mock.calls.some(([url]) => String(url).endsWith("/delete"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "保留对话" }));
    expect(screen.queryByRole("group", { name: "确认删除对话" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "对话操作" }));
    fireEvent.click(screen.getByRole("button", { name: "删除对话" }));
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(screen.queryByRole("list", { name: "对话消息" })).toBeNull());
    expect(fetcher.mock.calls.filter(([url]) => String(url).endsWith("/delete"))).toHaveLength(1);
    expect(fetcher.mock.calls.some(([url]) => String(url).includes("saved-trips"))).toBe(false);
  });
});
