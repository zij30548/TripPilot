import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ProductShell from "../product-shell";
import ConversationWorkspace from "./conversation-workspace";
import { assistantPlanningFixture } from "@/lib/assistant-planning.test-fixtures";
import { CHAT_ID, OTHER_CHAT_ID, deferred, emptyConversation } from "@/lib/chat-test-fixtures";
import { batchFixture, succeeded } from "@/lib/chat-places-test-fixtures";
import { located } from "@/lib/destination.test-fixtures";
import { adjustmentResponse } from "@/lib/schedule-adjustment.test-fixtures";
import { optionals, stamp } from "@/lib/weather-schedule-check.test-fixtures";
import { candidateSearches, type CandidateRequest, type CandidateResponse } from "@/types/candidates";
import { isScheduleResponse, type ScheduleRequest, type ScheduleResponse } from "@/types/schedule";
import { calculateScheduleCosts } from "@/lib/schedule-costs";
import { costsDialogueText } from "@/lib/dialogue-travel-text";
import type { ChatConversation } from "@/lib/chat";
import type { PlacesRequest, PlacesWorkspace } from "@/lib/chat-places";
import type { DialogueAction } from "@/lib/dialogue-action";
import type { SaveOperation, SavedDetail } from "@/lib/saved-trips";
import { nationalCatalogFixture } from "@/lib/knowledge-national.test-fixtures";

// Only HTTP is controlled: actual UI, confirmations, request/response guards,
// place and planning controllers, costs, preview/adoption and save owner run.
// This is NOT live model/AMap, persistence, pixel layout or semantic acceptance.
const route = vi.hoisted(() => ({ pathname: "/" }));
vi.mock("next/navigation", () => ({ usePathname: () => route.pathname }));
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
const clone = <T,>(value: T): T => structuredClone(value);
const uid = () => crypto.randomUUID();

function controlledServer({ confirmed = false, populated = false } = {}) {
  const fixture = assistantPlanningFixture();
  let conversation = populated ? clone(fixture.conversation) : emptyConversation();
  if (!confirmed) conversation.confirmed_revision = null;
  let places: PlacesWorkspace = { ...clone(fixture.places), intent_revision: conversation.revision,
    accommodation: populated ? clone(fixture.places.accommodation) : null,
    required_places: populated ? clone(fixture.places.required_places) : [] };
  const home = clone(fixture.places.accommodation!);
  const visit = clone(fixture.places.required_places[0]);
  const optional = optionals.slice(0, 2).map((place, index) => located({ ...place, name: `受控可选${index + 1}` }, "hangzhou"));
  const actions = new Map<string, DialogueAction | null>();
  const schedules: ScheduleResponse[] = [], saved: SavedDetail[] = [], writes: SaveOperation[] = [];
  let saveUncertain = false;
  const byText = new Map<string, DialogueAction>([
    ["把其他费用估算写为25元", { name: "set_cost", arguments: { kind: "other", selector: null, value: "25" } }],
    ["核对这天的全部费用", { name: "read_budget", arguments: {} }],
    ["预览排除受控可选1", { name: "preview_exclusion", arguments: { selector: optional[0].id } }],
  ]);
  const fetcher = vi.fn(async (url: string, init: RequestInit = {}) => {
    const body = init.body ? JSON.parse(String(init.body)) : null;
    if (url === "/api/knowledge/catalog-v2") return json(nationalCatalogFixture());
    if (url.includes("/chat/conversations?")) return json({ schema_version: "chat-list-v1", items: conversation.revision ? [{
      id: conversation.id, title: conversation.title, revision: conversation.revision, updated_at: conversation.updated_at,
    }] : [], next_offset: null });
    if (url === "/api/chat/conversations" && init.method === "POST") return json(conversation);
    if (url.endsWith("/assistant-turns")) {
      const prior = conversation, requirements = prior.revision ? prior.requirements : fixture.conversation.requirements;
      conversation = { ...clone(prior), title: "受控杭州旅行", revision: prior.revision + 2, requirements: clone(requirements),
        confirmed_revision: prior.confirmed_revision === prior.revision ? prior.revision + 2 : null,
        issues: [], sources: [], last_turn: succeeded(body.operation_id),
        messages: [...prior.messages,
          { id: uid(), role: "user", text: body.text, created_at: "2026-10-23T00:00:00Z" },
          { id: uid(), role: "assistant", text: prior.revision ? "已准备本轮操作提议，尚未执行。" : "已整理待确认需求草稿，尚未正式确认或生成。", created_at: "2026-10-23T00:00:01Z" }],
      };
      actions.set(body.operation_id, byText.get(body.text) ?? null);
      return json(conversation);
    }
    if (url.endsWith("/dialogue-action")) return json({ schema_version: "dialogue-action-v1", conversation_id: CHAT_ID,
      revision: conversation.revision, operation_id: conversation.last_turn?.operation_id, action: actions.get(conversation.last_turn!.operation_id) ?? null });
    if (url.endsWith("/confirm")) {
      expect(body.expected_revision).toBe(conversation.revision);
      const revision = conversation.revision + 1;
      conversation = { ...conversation, revision, confirmed_revision: revision };
      return json(conversation);
    }
    if (url.endsWith("/cancel")) {
      conversation = { ...conversation, revision: conversation.revision + 1, confirmed_revision: null,
        last_turn: { operation_id: body.operation_id, state: "cancelled", error_code: null } };
      return json(conversation);
    }
    if (url.endsWith("/places")) {
      if (!body) return json(places.intent_revision === conversation.revision ? places : { ...places, stale: true, batch: null });
      expect(body.expected_intent_revision).toBe(conversation.revision);
      const command = body as PlacesRequest;
      places = { ...places, revision: places.revision + 1, intent_revision: conversation.revision,
        stale: false, last_operation: succeeded(command.operation_id) };
      if (command.action === "sync") places = { ...places, batch: null };
      if (command.action === "search") places = { ...places, batch: { ...batchFixture([command.role === "accommodation" ? home : visit]),
        id: uid(), city_id: places.city_id!, keyword: command.keyword, role: command.role } };
      if (command.action === "confirm") {
        const chosen = places.batch!.places.find(place => place.id === command.place_id)!;
        expect(command.batch_id).toBe(places.batch!.id);
        places = command.role === "accommodation" ? { ...places, accommodation: chosen } : { ...places, required_places: [...places.required_places, chosen] };
      }
      return json(places);
    }
    if (url.startsWith("/api/places/candidates")) {
      const request = body as CandidateRequest, searches = candidateSearches(request.interests);
      const response: CandidateResponse = { destination_id: request.destination_id, status: "success", queried_at: stamp,
        keywords: searches.map(row => row.keyword), queries: searches.map(row => ({ ...row, status: "success", result_count: optional.length, message: null })),
        candidates: [...request.must_visit_places.map(place => ({ place, role: "must_visit" as const, retrieval_sources: [] })),
          ...optional.map(place => ({ place, role: "optional" as const, retrieval_sources: searches.slice(0, 1) }))] };
      return json(response);
    }
    if (url.startsWith("/api/trips/schedule-preview")) {
      const response = adjustmentResponse(body as ScheduleRequest);
      expect(isScheduleResponse(response, body)).toBe(true);
      schedules.push(clone(response));
      return json(response);
    }
    if (url === "/api/saved-trips" && body) {
      writes.push(clone(body));
      if (saveUncertain) { saveUncertain = false; throw new Error("controlled lost save response"); }
      const schedule = body.snapshot.schedule as ScheduleResponse;
      const existing = saved.find(row => row.name === body.name);
      const record: SavedDetail = existing ?? { id: "a".repeat(32), name: body.name, schema_version: body.snapshot.schema_version,
        saved_at: "2026-10-23T01:00:00Z", start_date: schedule.request.start_date, end_date: schedule.request.end_date,
        transport_mode: schedule.request.transport_mode!, status: schedule.status, snapshot: clone(body.snapshot) };
      if (!existing) saved.push(record);
      return json(record);
    }
    if (url.startsWith("/api/saved-trips?")) return json({ items: saved.map(row => ({ id: row.id, name: row.name, schema_version: row.schema_version,
      saved_at: row.saved_at, start_date: row.start_date, end_date: row.end_date, transport_mode: row.transport_mode, status: row.status })), offset: 0, limit: 10, has_more: false });
    if (url === `/api/saved-trips/${"a".repeat(32)}`) return json(saved[0]);
    if (url === `/api/chat/conversations/${CHAT_ID}`) return json(conversation);
    throw new Error(`Unexpected controlled request: ${url}`);
  });
  vi.stubGlobal("fetch", fetcher);
  return { fetcher, fixture, home, visit, optional, schedules, saved, writes, byText,
    conversation: () => conversation, places: () => places,
    uncertainSave: () => { saveUncertain = true; } };
}
async function send(text: string) {
  await act(async () => {
    fireEvent.change(screen.getByRole("textbox", { name: "告诉我你的旅行想法" }), { target: { value: text } });
    fireEvent.submit(screen.getByRole("form", { name: "发送旅行需求" }));
  });
}
const messages = () => within(screen.getByRole("list", { name: "对话消息" })).getAllByRole("listitem");
const last = () => messages().at(-1)!.textContent ?? "";
async function expectLast(text: string) { await waitFor(() => expect(last()).toContain(text)); }
const sent = (server: ReturnType<typeof controlledServer>, part: string) => server.fetcher.mock.calls.filter(([url]) => String(url).includes(part));

beforeEach(() => { route.pathname = "/"; });

describe("opt-in full core retains existing three-surface dialogue wire and domain controllers", () => {
  it("draft confirmation, actual POI guards, generation, budget, preview cancel/adopt and save compose without model facts", async () => {
    const server = controlledServer(); render(<ConversationWorkspace />);
    await send("整理杭州一天旅行，2人预算3000，住测试住宿，去同名地点");
    await expectLast("待确认需求草稿");
    expect(server.conversation().confirmed_revision).toBeNull();
    expect(sent(server, "/confirm")).toHaveLength(0);
    expect(server.schedules).toHaveLength(0);
    await send("准备规划"); await expectLast("未确认前不执行"); await send("确认");
    await expectLast("待确认的需求草稿"); expect(server.schedules).toHaveLength(0);
    expect(sent(server, "/places")).toHaveLength(0);
    await send("确认需求"); expect(sent(server, "/confirm")).toHaveLength(0);
    await send("确认"); await screen.findByText(/已确认这一版需求意向/);
    expect(server.conversation().confirmed_revision).toBe(server.conversation().revision);

    for (const [role, keyword] of [["住宿", server.home.name], ["必去", server.visit.name]]) {
      const before = sent(server, "/places").filter(([, init]) => init?.body && JSON.parse(String(init.body)).action === "search").length;
      await send(`搜索${role} ${keyword}`);
      expect(sent(server, "/places").filter(([, init]) => init?.body && JSON.parse(String(init.body)).action === "search")).toHaveLength(before);
      await send("确认"); await expectLast("请先回复“预览地点 1”");
      const confirmedBefore = server.places().required_places.length;
      await send("确认地点 1"); await expectLast("请先预览");
      expect(server.places().required_places).toHaveLength(confirmedBefore);
      await send("预览地点 1"); await expectLast("尚未加入");
      await send("确认地点 1"); await expectLast("已确认的真实地点");
    }
    expect(server.places().accommodation?.id).toBe(server.home.id);
    expect(server.places().required_places.map(place => place.id)).toEqual([server.visit.id]);
    await send("准备规划"); await send("确认"); await expectLast("尚未生成路线");
    expect(server.schedules).toHaveLength(0);
    await send("生成行程"); expect(server.schedules).toHaveLength(0);
    await send("确认"); await expectLast("必去已安排 1/1");
    expect(server.schedules).toHaveLength(1);
    await send("确认"); await expectLast("没有与当前上下文匹配");
    expect(server.schedules).toHaveLength(1);
    await send("查看第1天"); await expectLast(server.schedules[0].request.start_date);
    await send("核对费用"); await expectLast("尚未核算");
    expect(last()).toContain("未知"); expect(server.schedules).toHaveLength(1);

    await send("把其他费用估算写为25元"); await expectLast("未确认前不执行");
    await send("确认"); await expectLast("当前可计算小计：¥25.00");
    expect(last()).toContain("用户估算");
    await send("准备可选景点"); await send("确认"); await expectLast("受控可选1");
    await send("生成行程"); await send("确认");
    const original = server.schedules.at(-1)!;
    expect(original.request.optional_places?.map(place => place.id)).toEqual(server.optional.map(place => place.id));
    await send("预览排除受控可选1"); await send("确认"); await expectLast("调整预览，尚未采用");
    expect(last()).toContain("未知"); const preview = server.schedules.at(-1)!;
    await send("取消预览"); await expectLast("原正式草案和主动填写的费用估算保留");
    await send("查看行程"); expect(last()).toContain("受控可选1");
    expect(server.schedules).toHaveLength(3);
    await send("预览排除受控可选1"); await send("确认"); await expectLast("调整预览，尚未采用");
    await send("采用预览"); expect(server.schedules).toHaveLength(4);
    await send("确认"); await expectLast("已采用刚才查看");
    expect(server.schedules).toHaveLength(4);
    expect(last()).not.toContain("受控可选1");
    await send("核对费用");
    const expected = calculateScheduleCosts(preview, { ...server.fixture.conversation.requirements,
      budget: Number(server.fixture.conversation.requirements.budget_cny), travelers: 2,
      start_date: preview.request.start_date, end_date: preview.request.end_date },
      { tickets: {}, meals: {}, accommodation: "", other: "25" });
    expect(last()).toContain(costsDialogueText(expected));
    await send("保存行程 受控完整核心接线"); expect(server.writes).toHaveLength(0);
    await send("确认"); await expectLast("已保存“受控完整核心接线”");
    expect(server.writes).toHaveLength(1); expect(server.saved).toHaveLength(1);
    expect(server.saved[0].snapshot.schedule).toEqual(server.schedules.at(-1));
    expect(server.saved[0].snapshot.costs.other).toBe("25");
    expect(server.saved[0].snapshot.schema_version).toBe("saved-trip-v3");
    await send("查看历史"); await expectLast("受控完整核心接线");
    expect(server.schedules).toHaveLength(4);
    expect(server.fetcher.mock.calls.every(([url]) => String(url).startsWith("/api/"))).toBe(true);
    expect(server.fetcher.mock.calls.some(([url]) => /knowledge\/answer|schedule-explanation|itinerary-query|adjustment-intent|media\/|trips\/plan/.test(String(url)))).toBe(false);
    expect(screen.queryByRole("button", { name: /执行记录|收起行程|打开行程工具/ })).toBeNull();
  });

  it("navigation retains pending frozen save identity, estimates and formal draft; retry is explicit and identical", async () => {
    const server = controlledServer({ confirmed: true, populated: true });
    const view = render(<ProductShell>{null}</ProductShell>);
    fireEvent.click(await screen.findByRole("button", { name: "杭州旅行" }));
    await screen.findByRole("list", { name: "对话消息" });
    await send("准备规划"); await send("确认"); await send("生成行程"); await send("确认");
    await expectLast("必去已安排 1/1");
    server.uncertainSave(); await send("保存行程 冻结内容"); await send("确认");
    await expectLast("controlled lost save response"); expect(server.writes).toHaveLength(1);
    const original = clone(server.writes[0]);
    fireEvent.change(screen.getByRole("textbox", { name: "告诉我你的旅行想法" }), { target: { value: "未发送的旅行想法" } });
    const beforeNavigation = server.fetcher.mock.calls.length;
    route.pathname = "/knowledge"; view.rerender(<ProductShell>{null}</ProductShell>);
    route.pathname = "/"; view.rerender(<ProductShell>{null}</ProductShell>);
    expect((screen.getByRole("textbox", { name: "告诉我你的旅行想法" }) as HTMLTextAreaElement).value).toBe("未发送的旅行想法");
    // The first reference-page visit reads its local reviewed directory only.
    // No keyword search, model, schedule or save is triggered by navigation.
    expect(server.fetcher).toHaveBeenCalledTimes(beforeNavigation + 1);
    expect(server.fetcher.mock.calls.slice(beforeNavigation).map(([url]) => url)).toEqual(["/api/knowledge/catalog-v2"]);
    await send("准备规划"); await send("确认"); await expectLast("保存结果尚未确认");
    await send("保存行程 新名字不得替换冻结内容"); await send("确认"); await expectLast("已保存“冻结内容”");
    expect(server.writes).toHaveLength(2); expect(server.writes[1]).toEqual(original);
    expect(server.schedules).toHaveLength(1);
    expect(screen.getByRole("navigation", { name: "主导航" }).querySelectorAll("a")).toHaveLength(3);
  });

  it.each(["success", "error"])("cancelled late %s cannot execute an old proposal or discard the formal draft", async outcome => {
    const server = controlledServer({ confirmed: true, populated: true }); render(<ConversationWorkspace />);
    fireEvent.click(await screen.findByRole("button", { name: "杭州旅行" }));
    await screen.findByRole("list", { name: "对话消息" });
    await send("准备规划"); await send("确认"); await send("生成行程"); await send("确认");
    await expectLast("必去已安排 1/1");
    const late = deferred<Response>(), base = server.fetcher.getMockImplementation()!;
    let pendingBody: { operation_id: string; expected_revision: number; text: string } | null = null;
    server.fetcher.mockImplementation(async (url, init = {}) => {
      if (url.endsWith("/assistant-turns")) { pendingBody = JSON.parse(String(init.body)); return late.promise; }
      return base(url, init);
    });
    await send("请帮我再考虑一种方案");
    await screen.findByRole("button", { name: "停止本次旅行请求" });
    const pending = sent(server, "/assistant-turns").at(-1)!;
    fireEvent.click(screen.getByRole("button", { name: "停止本次旅行请求" }));
    await screen.findByText(/已取消本页等待/);
    expect(pending[1]?.signal?.aborted).toBe(true);
    await act(async () => {
      if (outcome === "error") late.reject(new Error("private old response"));
      else late.resolve(json({ ...server.conversation(), revision: pendingBody!.expected_revision + 2,
        last_turn: succeeded(pendingBody!.operation_id),
        messages: [...server.conversation().messages, { id: uid(), role: "assistant", text: "旧结果不得展示或自动采用", created_at: "2026-10-23T00:00:02Z" }] }));
    });
    expect(screen.queryByText("旧结果不得展示或自动采用")).toBeNull();
    expect(screen.queryByText("private old response")).toBeNull();
    expect(sent(server, "/dialogue-action")).toHaveLength(0);
    expect(sent(server, "/assistant-turns")).toHaveLength(1);
    await send("查看行程"); await expectLast("必去已安排 1/1");
    expect(server.schedules).toHaveLength(1); expect(server.writes).toHaveLength(0);
  });

  it.each(["success", "error"])("A→B→A readback ignores obsolete %s and keyboard disclosure makes no business request", async outcome => {
    const server = controlledServer({ confirmed: true, populated: true }), late = deferred<Response>();
    const a = server.conversation(), b: ChatConversation = { ...clone(a), id: OTHER_CHAT_ID, title: "受控北京对话",
      messages: [{ id: uid(), role: "assistant", text: "B的受控消息", created_at: "2026-10-23T00:00:00Z" }] };
    const base = server.fetcher.getMockImplementation()!; let reads = 0;
    server.fetcher.mockImplementation(async (url, init = {}) => {
      if (url.includes("/chat/conversations?")) return json({ schema_version: "chat-list-v1", items: [a, b].map(row => ({
        id: row.id, title: row.title, revision: row.revision, updated_at: row.updated_at })), next_offset: null });
      if (url === `/api/chat/conversations/${CHAT_ID}` && ++reads === 1) return late.promise;
      if (url === `/api/chat/conversations/${OTHER_CHAT_ID}`) return json(b);
      return base(url, init);
    });
    render(<ConversationWorkspace />);
    fireEvent.click(await screen.findByRole("button", { name: "杭州旅行" }));
    fireEvent.click(screen.getByRole("button", { name: "受控北京对话" }));
    await screen.findByText("B的受控消息");
    fireEvent.click(screen.getByRole("button", { name: "杭州旅行" }));
    await screen.findByText(a.messages[1].text);
    await act(async () => {
      if (outcome === "error") late.reject(new Error("old A private failure"));
      else late.resolve(json({ ...a, messages: [{ ...a.messages[1], text: "旧A不得重现" }] }));
    });
    expect(screen.queryByText("旧A不得重现")).toBeNull();
    expect(screen.queryByText("old A private failure")).toBeNull();
    expect(screen.getByRole("button", { name: "杭州旅行" }).getAttribute("aria-current")).toBe("page");
    const before = server.fetcher.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "打开旅行对话列表" }));
    const drawer = screen.getByRole("dialog", { name: "我的对话" });
    fireEvent.keyDown(drawer, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "我的对话" })).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "打开旅行对话列表" }));
    expect(server.fetcher).toHaveBeenCalledTimes(before);
    expect(server.fetcher.mock.calls.some(([url]) => /schedule|weather|saved-trips|media|knowledge\/answer/.test(String(url)))).toBe(false);
  });
});
