import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Home from "@/app/page";
import { CHAT_ID, OP_ID, conversationFixture, deferred, emptyConversation } from "@/lib/chat-test-fixtures";
import { assistantPlanningFixture } from "@/lib/assistant-planning.test-fixtures";
import { BATCH_ID, succeeded } from "@/lib/chat-places-test-fixtures";
import { adjustmentResponse } from "@/lib/schedule-adjustment.test-fixtures";
import type { PlacesWorkspace } from "@/lib/chat-places";

const reply = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
const list = (full = conversationFixture()) => ({ schema_version: "chat-list-v1", items: [{ id: full.id, title: full.title, revision: full.revision, updated_at: full.updated_at }], next_offset: null });
const receipt = () => ({ schema_version: "assistant-state-v1", conversation_id: CHAT_ID, revision: 2, migration_ready: true, preferences: [], skills: [{ id: "travel", name: "旅行准备", version: "1.0.0" }], task: { id: OP_ID, state: "succeeded", model_calls: 1, tool_calls: 1, stop_reason: null, steps: [{ name: "核对需求", state: "succeeded", elapsed_ms: 150 }] } });
async function openRequirements() { fireEvent.click(await screen.findByRole("button", { name: "查看需求卡" })); return screen.findByRole("region", { name: "旅行需求意向卡" }); }
let fetcher: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetcher = vi.fn().mockImplementation(async (url, init: RequestInit = {}) => {
    if (String(url).includes("?")) return reply(list());
    if (url === "/api/chat/conversations" && init.method === "POST") return reply(emptyConversation());
    if (String(url).endsWith("/assistant-turns")) {
      const body = JSON.parse(init.body as string);
      return reply(conversationFixture({ last_turn: { operation_id: body.operation_id, state: "succeeded", error_code: null } }));
    }
    if (String(url).endsWith("/assistant-state")) return reply(receipt());
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
    expect(screen.queryByRole("link", { name: "旧版规划工作区" })).toBeNull();
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
    await openRequirements();
    const requests = fetcher.mock.calls.map(([url]) => String(url));
    expect(requests.filter(url => url.endsWith("/assistant-turns"))).toHaveLength(1);
    expect(requests.some(url => url.endsWith("/turns"))).toBe(false);
    expect(requests.every(url => url.startsWith("/api/chat/conversations"))).toBe(true);
    expect(screen.getByText(/地点仍需后续真实搜索与确认/)).toBeTruthy();
    expect(screen.getByText("待你核对 · v2")).toBeTruthy();
  });
  it("fill then Enter in one event batch sends the current text once without waiting for another click", async () => {
    render(<Home />); await screen.findByRole("button", { name: "杭州旅行" });
    const input = screen.getByRole("textbox", { name: "告诉我你的旅行想法" }) as HTMLTextAreaElement;
    Object.defineProperty(input.form, "requestSubmit", { configurable: true, value: () => fireEvent.submit(input.form!) });
    await act(async () => {
      fireEvent.change(input, { target: { value: "杭州，周末两天" } });
      fireEvent.keyDown(input, { key: "Enter" });
    });
    const sent = fetcher.mock.calls.filter(([url]) => String(url).endsWith("/assistant-turns"));
    expect(sent).toHaveLength(1);
    expect(JSON.parse(sent[0][1].body as string).text).toBe("杭州，周末两天");
  });
  it("plain-text safe rendering, explicit intent confirmation and continued correction", async () => {
    const full = conversationFixture(); full.messages[1].text = "<img src=x onerror=alert(1)>模型不是事实来源";
    fetcher.mockImplementation(async (url: string) => url.includes("?") ? reply(list(full)) : url.endsWith("/confirm") ? reply({ ...full, revision: 3, confirmed_revision: 3 }) : reply(full));
    const view = render(<Home />);
    fireEvent.click(await screen.findByRole("button", { name: "杭州旅行" }));
    const card = await openRequirements();
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
    const card = await openRequirements();
    expect(within(card).getByRole("button", { name: "核对无误，确认需求" }).hasAttribute("disabled")).toBe(true);
    expect(within(card).getByText(/还需补充：全程总预算/)).toBeTruthy();
    expect(within(card).getByText(/仍有含糊/)).toBeTruthy();
  });
  it("deletion needs a second explicit confirmation and never touches saved-trip APIs", async () => {
    render(<Home />); fireEvent.click(await screen.findByRole("button", { name: "杭州旅行" }));
    await openRequirements();
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
    fetcher.mockImplementation(async (url: string) => url.includes("?") ? reply(list()) : url.endsWith("/assistant-turns") ? reply({ detail: { code: "configuration", message: "SECRET" } }, 503) : reply(emptyConversation()));
    render(<Home />); await screen.findByRole("button", { name: "杭州旅行" });
    fireEvent.change(screen.getByRole("textbox", { name: "告诉我你的旅行想法" }), { target: { value: "杭州" } });
    await act(async () => { fireEvent.submit(screen.getByRole("form", { name: "发送旅行需求" })); });
    expect(screen.getByRole("alert").textContent).toContain("没有生成替代回复");
    expect(screen.queryByText("SECRET")).toBeNull();
    expect(screen.queryByRole("region", { name: "旅行需求意向卡" })).toBeNull();
    await waitFor(() => expect(fetcher.mock.calls.filter(([url]) => String(url).endsWith("/assistant-turns"))).toHaveLength(1));
    expect(fetcher.mock.calls.some(([url]) => String(url).endsWith("/turns"))).toBe(false);
  });
  it("keeps chat and draft while panels toggle; execution is a safe explicit read, not another model call", async () => {
    render(<Home />); fireEvent.click(await screen.findByRole("button", { name: "杭州旅行" }));
    await screen.findByRole("button", { name: "查看需求卡" });
    expect(screen.queryByRole("region", { name: "旅行需求意向卡" })).toBeNull();
    expect(screen.queryByRole("region", { name: "旅行助手执行状态" })).toBeNull();
    const input = screen.getByRole("textbox", { name: "告诉我你的旅行想法" }) as HTMLTextAreaElement;
    fireEvent.change(input, { target: { value: "未发送的修改" } });
    const before = fetcher.mock.calls.length;
    await openRequirements();
    fireEvent.click(screen.getByRole("button", { name: "收起需求卡" }));
    expect(fetcher).toHaveBeenCalledTimes(before);
    expect(input.value).toBe("未发送的修改");
    fireEvent.click(screen.getByRole("button", { name: "查看执行记录" }));
    const panel = await screen.findByRole("region", { name: "本轮执行记录" });
    expect(panel.textContent).toContain("模型调用1次");
    expect(panel.textContent).toContain("核对需求");
    expect(panel.textContent).toContain("不展示模型内部思考");
    fireEvent.click(screen.getByRole("button", { name: "收起执行记录" }));
    expect(input.value).toBe("未发送的修改");
    expect(fetcher.mock.calls.filter(([url]) => String(url).endsWith("/assistant-state"))).toHaveLength(1);
    expect(fetcher.mock.calls.some(([url]) => String(url).endsWith("/assistant-turns"))).toBe(false);
  });
  it("publishes a real ChatPlaces workspace into its parent without duplicate sibling instances or repeated reads", async () => {
    const fixture = assistantPlanningFixture(), full = fixture.conversation;
    fetcher.mockImplementation(async (url: string) => {
      if (url.includes("?")) return reply(list(full));
      if (url.endsWith("/places")) return reply(fixture.places);
      if (url === `/api/chat/conversations/${CHAT_ID}`) return reply(full);
      throw new Error(`Unexpected controlled request: ${url}`);
    });
    const view = render(<Home />);
    fireEvent.click(await screen.findByRole("button", { name: full.title }));
    await screen.findByRole("button", { name: "查看需求卡" });
    fireEvent.click(screen.getByRole("button", { name: "准备真实地点" }));
    await screen.findByText(/地点准备城市：杭州市/);
    // This is the actual component and its onWorkspace effect: its parent now
    // re-renders with confirmed POIs, alongside the always-mounted preferences.
    await waitFor(() => expect(view.container.querySelectorAll('[aria-label="真实地点准备"]')).toHaveLength(1));
    expect(view.container.querySelectorAll('[aria-label="用户明确保存的偏好"]')).toHaveLength(1);
    fireEvent.change(screen.getByRole("textbox", { name: "地点关键词" }), { target: { value: "另一个地点" } });
    await openRequirements();
    fireEvent.click(screen.getByRole("button", { name: "收起需求卡" }));
    await act(async () => { await Promise.resolve(); });
    expect(view.container.querySelectorAll('[aria-label="真实地点准备"]')).toHaveLength(1);
    expect(view.container.querySelectorAll('[aria-label="用户明确保存的偏好"]')).toHaveLength(1);
    expect(fetcher.mock.calls.filter(([url]) => String(url).endsWith("/places"))).toHaveLength(1);
    expect(fetcher.mock.calls.some(([url]) => String(url).includes("assistant-turns") || String(url).includes("assistant-preferences"))).toBe(false);
  });
  it("confirmed-on-server but interrupted POI acknowledgement cannot prepare an old empty snapshot; only explicit readback unlocks", async () => {
    const fixture = assistantPlanningFixture(), full = fixture.conversation, target = fixture.places.required_places[0], acknowledgement = deferred<Response>();
    let server: PlacesWorkspace = { ...fixture.places, revision: 8, required_places: [], batch: { id: BATCH_ID, city_id: fixture.places.city_id!, keyword: target.name, role: "required", queried_at: "2026-10-22T00:00:00Z", places: [target] } };
    fetcher.mockImplementation(async (url: string, init: RequestInit = {}) => {
      if (url.includes("?")) return reply(list(full));
      if (url.endsWith("/places")) {
        if (init.method === "POST") {
          const command = JSON.parse(init.body as string);
          expect(command.action).toBe("confirm");
          server = { ...server, revision: 9, required_places: [target], last_operation: succeeded(command.operation_id) };
          return acknowledgement.promise;
        }
        return reply(server);
      }
      if (url === `/api/chat/conversations/${CHAT_ID}`) return reply(full);
      throw new Error(`Unexpected controlled request: ${url}`);
    });
    render(<Home />); fireEvent.click(await screen.findByRole("button", { name: full.title }));
    await screen.findByRole("button", { name: "准备真实地点" });
    fireEvent.click(screen.getByRole("button", { name: "准备真实地点" }));
    fireEvent.click(await screen.findByRole("button", { name: new RegExp(`预览：${target.name}`) }));
    fireEvent.click(screen.getByRole("button", { name: "明确确认，加入必去" }));
    await waitFor(() => expect(server.required_places).toHaveLength(1));
    fireEvent.click(screen.getByRole("button", { name: "收起地点准备" }));
    fireEvent.click(screen.getByRole("button", { name: "打开行程工具" }));
    expect(screen.getByRole("button", { name: "核对并准备行程" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByText(/请打开地点准备，主动重新读取/)).toBeTruthy();
    await act(async () => acknowledgement.resolve(reply(server)));
    expect(screen.queryByRole("button", { name: "生成行程草案" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "准备真实地点" }));
    await act(async () => { await Promise.resolve(); });
    expect(fetcher.mock.calls.filter(([url, init]) => String(url).endsWith("/places") && init?.method === "GET")).toHaveLength(1);
    fireEvent.click(await screen.findByRole("button", { name: "重新读取地点准备" }));
    await screen.findByText(`1. ${target.name}`);
    await waitFor(() => expect(screen.getByRole("button", { name: "核对并准备行程" }).hasAttribute("disabled")).toBe(false));
    fireEvent.click(screen.getByRole("button", { name: "核对并准备行程" }));
    expect(screen.getByRole("spinbutton", { name: `停留分钟：${target.name}` })).toBeTruthy();
    expect(fetcher.mock.calls.filter(([url, init]) => String(url).endsWith("/places") && init?.method === "POST")).toHaveLength(1);
    expect(fetcher.mock.calls.some(([url]) => String(url).includes("schedule-preview") || String(url).includes("assistant-turns"))).toBe(false);
  });
  it("an uncertain place write blocks operations without unmounting a same-input draft, fee or save controller", async () => {
    const fixture = assistantPlanningFixture(), full = fixture.conversation, acknowledgement = deferred<Response>();
    let server = fixture.places;
    fetcher.mockImplementation(async (url: string, init: RequestInit = {}) => {
      if (url.includes("?limit=")) return reply(list(full));
      if (url.includes("schedule-preview")) return reply(adjustmentResponse(JSON.parse(init.body as string)));
      if (url.endsWith("/places")) {
        if (init.method === "POST") return acknowledgement.promise;
        return reply(server);
      }
      if (url === `/api/chat/conversations/${CHAT_ID}`) return reply(full);
      throw new Error(`Unexpected controlled request: ${url}`);
    });
    render(<Home />); fireEvent.click(await screen.findByRole("button", { name: full.title }));
    await screen.findByRole("button", { name: "准备真实地点" }); fireEvent.click(screen.getByRole("button", { name: "准备真实地点" }));
    await screen.findByText(/地点准备城市：杭州市/);
    fireEvent.click(screen.getByRole("button", { name: "打开行程工具" }));
    fireEvent.click(screen.getByRole("button", { name: "核对并准备行程" }));
    fireEvent.click(screen.getByRole("button", { name: "生成行程草案" }));
    const budget = await screen.findByRole("region", { name: "正式草案费用与预算" });
    const cost = within(budget).getByRole("textbox", { name: /全程其他/ }) as HTMLInputElement;
    fireEvent.change(cost, { target: { value: "100" } });
    const timeline = screen.getByRole("list", { name: "草案时间线" });
    fireEvent.click(within(screen.getByRole("region", { name: "真实地点准备" })).getByRole("button", { name: `移除${fixture.places.required_places[0].name}` }));
    fireEvent.click(screen.getByRole("button", { name: "收起地点准备" }));
    expect(timeline.isConnected).toBe(true); expect(cost.value).toBe("100");
    expect(timeline.closest("[inert]")).not.toBeNull();
    server = { ...server, revision: 9, last_operation: { ...succeeded(), state: "failed", error_code: "request_failed" } };
    fireEvent.click(screen.getByRole("button", { name: "准备真实地点" }));
    fireEvent.click(await screen.findByRole("button", { name: "重新读取地点准备" }));
    await waitFor(() => expect(timeline.closest("[inert]")).toBeNull());
    await act(async () => acknowledgement.reject(new Error("old acknowledgement lost")));
    expect(timeline.isConnected).toBe(true); expect(cost.value).toBe("100");
    expect(fetcher.mock.calls.filter(([url]) => String(url).includes("schedule-preview"))).toHaveLength(1);
  });
  it.each(["generate", "save"])("an already-mounted %s callback consults place authority before the parent can render inert", async action => {
    const fixture = assistantPlanningFixture(), full = fixture.conversation, acknowledgement = deferred<Response>();
    fetcher.mockImplementation(async (url: string, init: RequestInit = {}) => {
      if (url.includes("?limit=")) return reply(list(full));
      if (url.includes("schedule-preview")) return reply(adjustmentResponse(JSON.parse(init.body as string)));
      if (url.endsWith("/places")) return init.method === "POST" ? acknowledgement.promise : reply(fixture.places);
      if (url === `/api/chat/conversations/${CHAT_ID}`) return reply(full);
      throw new Error(`Unexpected controlled request: ${url}`);
    });
    render(<Home />); fireEvent.click(await screen.findByRole("button", { name: full.title }));
    fireEvent.click(await screen.findByRole("button", { name: "准备真实地点" }));
    await screen.findByText(/地点准备城市：杭州市/);
    fireEvent.click(screen.getByRole("button", { name: "打开行程工具" }));
    fireEvent.click(screen.getByRole("button", { name: "核对并准备行程" }));
    if (action === "save") {
      fireEvent.click(screen.getByRole("button", { name: "生成行程草案" }));
      await screen.findByRole("region", { name: "正式草案费用与预算" });
    }
    const oldButton = screen.getByRole("button", { name: action === "save" ? "保存当前正式草案" : "生成行程草案" });
    const originalCalls = fetcher.mock.calls.length;
    act(() => {
      fireEvent.click(within(screen.getByRole("region", { name: "真实地点准备" })).getByRole("button", { name: `移除${fixture.places.required_places[0].name}` }));
      // One React batch: the old DOM and its captured handler still exist.
      fireEvent.click(oldButton);
    });
    await act(async () => { await Promise.resolve(); });
    expect(fetcher.mock.calls.slice(originalCalls).map(([url]) => url)).toEqual([`/api/chat/conversations/${CHAT_ID}/places`]);
    expect(fetcher.mock.calls.some(([url]) => String(url).endsWith("/saved-trips"))).toBe(false);
    if (action === "save") expect(screen.getByRole("list", { name: "草案时间线" })).toBeTruthy();
  });
});
