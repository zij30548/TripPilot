import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Home from "@/app/page";
import { CHAT_ID, conversationFixture, emptyConversation } from "@/lib/chat-test-fixtures";

const reply = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
const list = (full = conversationFixture()) => ({ schema_version: "chat-list-v1", items: [{ id: full.id, title: full.title, revision: full.revision, updated_at: full.updated_at }], next_offset: null });
let fetcher: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetcher = vi.fn().mockImplementation(async (url, init: RequestInit = {}) => {
    if (String(url).includes("?")) return reply(list());
    if (url === "/api/chat/conversations" && init.method === "POST") return reply(emptyConversation());
    if (String(url).endsWith("/turns")) {
      const body = JSON.parse(init.body as string);
      return reply(conversationFixture({ last_turn: { operation_id: body.operation_id, state: "succeeded", error_code: null } }));
    }
    if (String(url).endsWith("/confirm")) return reply(conversationFixture({ revision: 3, confirmed_revision: 3 }));
    if (String(url).endsWith("/delete")) return new Response(null, { status: 204 });
    if (url === `/api/chat/conversations/${CHAT_ID}`) return reply(conversationFixture());
    throw new Error(`Unexpected request: ${url}`);
  });
  vi.stubGlobal("fetch", fetcher);
});

describe("default chat-only composition", () => {
  it("opens without old planning controllers or automatic model/business calls", async () => {
    render(<Home />);
    await screen.findByRole("button", { name: "杭州旅行" });
    expect(screen.getByRole("heading", { name: /想去哪里/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "生成行程草案" })).toBeNull();
    expect(screen.queryByRole("button", { name: "查询天气" })).toBeNull();
    expect(screen.queryByRole("button", { name: "获取候选地点" })).toBeNull();
    expect(screen.getByRole("link", { name: "旧版规划工作区" }).getAttribute("href")).toBe("/legacy");
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toBe("/api/chat/conversations?limit=20&offset=0");
  });
  it("suggestions only fill composer; Enter sends once, Shift/IME Enter don't", async () => {
    render(<Home />); await screen.findByRole("button", { name: "杭州旅行" });
    fireEvent.click(screen.getByRole("button", { name: /想去杭州，带父母轻松玩几天/ }));
    const input = screen.getByRole("textbox", { name: "告诉我你的旅行想法" });
    expect((input as HTMLTextAreaElement).value).toContain("杭州"); expect(fetcher).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
    fireEvent.keyDown(input, { key: "Enter", isComposing: true, keyCode: 229 });
    expect(fetcher).toHaveBeenCalledTimes(1);
    // jsdom does not implement requestSubmit's native form action. Submit the
    // same form event directly; keyboard handler is separately exercised below.
    fireEvent.submit(screen.getByRole("form", { name: "发送旅行需求" }));
    await screen.findByRole("region", { name: "旅行需求意向卡" });
    const requests = fetcher.mock.calls.map(([url]) => String(url));
    expect(requests.filter(url => url.endsWith("/turns"))).toHaveLength(1);
    expect(requests.every(url => url.startsWith("/api/chat/conversations"))).toBe(true);
    expect(screen.getByText(/地点仍需后续真实搜索与确认/)).toBeTruthy();
    expect(screen.getByText("待你核对 · v2")).toBeTruthy();
  });
  it("plain-text safe rendering, explicit intent confirmation and continued correction", async () => {
    const full = conversationFixture(); full.messages[1].text = "<img src=x onerror=alert(1)>模型不是事实来源";
    fetcher.mockImplementation(async (url: string) => url.includes("?") ? reply(list(full)) : url.endsWith("/confirm") ? reply({ ...full, revision: 3, confirmed_revision: 3 }) : reply(full));
    const view = render(<Home />);
    fireEvent.click(await screen.findByRole("button", { name: "杭州旅行" }));
    const card = await screen.findByRole("region", { name: "旅行需求意向卡" });
    expect(screen.getByText(/<img src=x/)).toBeTruthy(); expect(view.container.querySelector("img")).toBeNull();
    expect(within(card).getAllByText(/尚未核实POI/)).toHaveLength(2);
    expect(fetcher.mock.calls.some(([url]) => String(url).endsWith("/confirm"))).toBe(false);
    fireEvent.click(within(card).getByRole("button", { name: "核对无误，确认需求" }));
    await screen.findByRole("button", { name: "这一版需求已确认" });
    expect(fetcher.mock.calls.filter(([url]) => String(url).endsWith("/confirm"))).toHaveLength(1);
    expect(screen.getByText(/可以继续发消息修正/)).toBeTruthy();
  });
  it("blocks confirmation on unresolved ambiguity and incomplete required fields", async () => {
    const full = conversationFixture({ issues: ["ambiguous"] }); full.requirements.budget_cny = null;
    fetcher.mockImplementation(async (url: string) => url.includes("?") ? reply(list(full)) : reply(full));
    render(<Home />); fireEvent.click(await screen.findByRole("button", { name: "杭州旅行" }));
    const card = await screen.findByRole("region", { name: "旅行需求意向卡" });
    expect(within(card).getByRole("button", { name: "核对无误，确认需求" }).hasAttribute("disabled")).toBe(true);
    expect(within(card).getByText(/还需补充：全程总预算/)).toBeTruthy();
    expect(within(card).getByText(/仍有含糊/)).toBeTruthy();
  });
  it("deletion needs a second explicit confirmation and never touches saved-trip APIs", async () => {
    render(<Home />); fireEvent.click(await screen.findByRole("button", { name: "杭州旅行" }));
    await screen.findByRole("region", { name: "旅行需求意向卡" });
    fireEvent.click(screen.getByRole("button", { name: "删除这份对话" }));
    expect(fetcher.mock.calls.some(([url]) => String(url).endsWith("/delete"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "保留对话" }));
    fireEvent.click(screen.getByRole("button", { name: "删除这份对话" }));
    fireEvent.click(screen.getByRole("button", { name: "确认删除这份对话" }));
    await screen.findByText(/这份会话和需求意向已删除/);
    expect(fetcher.mock.calls.filter(([url]) => String(url).endsWith("/delete"))).toHaveLength(1);
    expect(fetcher.mock.calls.some(([url]) => String(url).includes("saved-trips"))).toBe(false);
  });
  it("drawer and keyboard are display-only; return to blank keeps session record available", async () => {
    render(<Home />); await screen.findByRole("button", { name: "杭州旅行" });
    fireEvent.click(screen.getByRole("button", { name: "打开旅行对话列表" }));
    expect(screen.getByRole("button", { name: "打开旅行对话列表" }).getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    fireEvent.click(screen.getByRole("button", { name: "新建旅行对话" }));
    expect(fetcher).toHaveBeenCalledTimes(1);
    const input = screen.getByRole("textbox", { name: "告诉我你的旅行想法" }) as HTMLTextAreaElement;
    expect(document.activeElement).toBe(input);
    fireEvent.change(input, { target: { value: "想去杭州" } });
    const submit = vi.fn(); Object.defineProperty(input.form, "requestSubmit", { configurable: true, value: submit });
    fireEvent.keyDown(input, { key: "Enter" }); expect(submit).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(input, { key: "Enter", shiftKey: true }); expect(submit).toHaveBeenCalledTimes(1);
  });
  it("failure is honest and doesn't produce a fixed fallback assistant message", async () => {
    fetcher.mockImplementation(async (url: string) => url.includes("?") ? reply(list()) : url.endsWith("/turns") ? reply({ detail: { code: "configuration", message: "SECRET" } }, 503) : reply(emptyConversation()));
    render(<Home />); await screen.findByRole("button", { name: "杭州旅行" });
    fireEvent.change(screen.getByRole("textbox", { name: "告诉我你的旅行想法" }), { target: { value: "杭州" } });
    await act(async () => { fireEvent.submit(screen.getByRole("form", { name: "发送旅行需求" })); });
    expect(screen.getByRole("alert").textContent).toContain("没有生成替代回复");
    expect(screen.queryByText("SECRET")).toBeNull();
    expect(screen.queryByRole("region", { name: "旅行需求意向卡" })).toBeNull();
    await waitFor(() => expect(fetcher.mock.calls.filter(([url]) => String(url).endsWith("/turns"))).toHaveLength(1));
  });
});
