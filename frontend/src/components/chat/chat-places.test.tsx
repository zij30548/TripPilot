import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import Home from "@/app/page";
import ChatPlaces from "./chat-places";
import { CHAT_ID, emptyConversation } from "@/lib/chat-test-fixtures";
import { batchFixture, placeFixture, placesConversation, placesFixture, succeeded } from "@/lib/chat-places-test-fixtures";
import type { PlacesWorkspace } from "@/lib/chat-places";
const reply = (v: unknown) => new Response(JSON.stringify(v), { headers: { "Content-Type": "application/json" } });

describe("compact real-place result card (controlled responses)", () => {
  it("local preview, one explicit search, then explicit ID confirmation; no automatic business queries", async () => {
    let server = placesFixture();
    const fetcher = vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        const request = JSON.parse(init.body as string);
        server = request.action === "search" ? placesFixture({ revision: 1, batch: batchFixture(), last_operation: succeeded(request.operation_id) }) :
          placesFixture({ revision: 2, batch: batchFixture(), required_places: [placeFixture()], last_operation: succeeded(request.operation_id) });
      }
      return reply(server);
    }); vi.stubGlobal("fetch", fetcher);
    const conversation = placesConversation(), view = render(<ChatPlaces conversation={conversation} open={false} blocked={false} />);
    expect(fetcher).not.toHaveBeenCalled();
    view.rerender(<ChatPlaces conversation={conversation} open blocked={false} />);
    await screen.findByText(/地点准备城市：苏州市/);
    fireEvent.change(screen.getByRole("textbox", { name: "地点关键词" }), { target: { value: "受控景点" } });
    expect(fetcher).toHaveBeenCalledTimes(1);
    fireEvent.submit(screen.getByRole("form", { name: "主动查找真实地点" }));
    fireEvent.click(await screen.findByRole("button", { name: /预览：受控景点/ }));
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("region", { name: "真实地点预览" }).textContent).toContain("320508");
    expect(server.required_places).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "明确确认，加入必去" }));
    await screen.findByText("1. 受控景点");
    expect(fetcher).toHaveBeenCalledTimes(3);
    expect(JSON.parse(fetcher.mock.calls[1][1]!.body as string).action).toBe("search");
    expect(JSON.parse(fetcher.mock.calls[2][1]!.body as string).action).toBe("confirm");
    const requests = fetcher.mock.calls.map(([url]) => url);
    expect(requests.every(url => url.endsWith("/places"))).toBe(true);
    view.rerender(<ChatPlaces conversation={{ ...conversation }} open blocked={false} />);
    expect(fetcher).toHaveBeenCalledTimes(3);
  });
  it("city selection is local until clear confirmation and never silently changes to a model proposal", async () => {
    const fetcher = vi.fn(async (_url: string, init?: RequestInit) => reply(init?.method === "POST" ? placesFixture({ city_id: "310000", revision: 2,
      last_operation: succeeded(JSON.parse(init.body as string).operation_id) }) : placesFixture({ revision: 1, proposed_city_id: "310000", clarification: "city_confirmation_required" })));
    vi.stubGlobal("fetch", fetcher); render(<ChatPlaces conversation={placesConversation()} open blocked={false} />);
    await screen.findByText("准备使用：上海市 · 上海市"); expect(fetcher).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "确认切换城市并清除旧地点" }));
    await screen.findByText(/地点准备城市：上海市/);
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(JSON.parse(fetcher.mock.calls[1][1]!.body as string)).toMatchObject({ action: "set_city", city_id: "310000" });
    expect(screen.queryByRole("button", { name: "明确确认，加入必去" })).toBeNull();
  });
  it("keeps multi-city intent as a clarification and distinguishes refusal from successful search", async () => {
    const fetcher = vi.fn(async () => reply(placesFixture({ clarification: "multi_city", request_text: "先上海再杭州" })));
    vi.stubGlobal("fetch", fetcher); render(<ChatPlaces conversation={placesConversation()} open blocked={false} />);
    await screen.findByText(/已保留你的跨城表达/);
    expect(screen.getByText(/本次地点要求：先上海再杭州/)).toBeTruthy();
    expect(screen.queryByText("高德候选 · 仅查看，不代表已确认")).toBeNull();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it("empty/no-match stays empty; same-name candidates are previewed by distinct IDs", async () => {
    let server = placesFixture({ batch: batchFixture([]) });
    vi.stubGlobal("fetch", vi.fn(async () => reply(server)));
    const conversation = placesConversation(); render(<ChatPlaces conversation={conversation} open blocked={false} />);
    await screen.findByText(/没有找到匹配地点/);
    server = placesFixture({ batch: batchFixture([placeFixture("a"), placeFixture("b")]) });
    fireEvent.click(screen.getByRole("button", { name: "重新读取地点准备" }));
    await waitFor(() => expect(screen.getAllByRole("button", { name: /预览：受控景点/ })).toHaveLength(2));
    fireEvent.click(screen.getAllByRole("button", { name: /预览：受控景点/ })[1]);
    expect(screen.getAllByRole("button", { name: /预览：受控景点/ })[0].getAttribute("aria-pressed")).toBe("false");
    expect(screen.getAllByRole("button", { name: /预览：受控景点/ })[1].getAttribute("aria-pressed")).toBe("true");
  });
  it("new blank chat can explicitly prepare places without making a model turn", async () => {
    const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.includes("?limit=")) return reply({ schema_version: "chat-list-v1", items: [], next_offset: null });
      if (url === "/api/chat/conversations" && init?.method === "POST") return reply(emptyConversation());
      if (url.endsWith("/places")) return reply(placesFixture({ city_id: null, intent_revision: 0 }));
      throw new Error(`Unexpected controlled request ${url}`);
    }); vi.stubGlobal("fetch", fetcher); render(<Home />);
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "准备真实地点" }));
    await screen.findByRole("region", { name: "真实地点准备" });
    await screen.findByText("地点准备城市：尚未确认");
    expect(fetcher).toHaveBeenCalledTimes(3);
    expect(fetcher.mock.calls.some(([url]) => url.endsWith("/turns"))).toBe(false);
    const section = screen.getByRole("region", { name: "真实地点准备" });
    expect(within(section).getByRole("button", { name: "主动搜索高德地点" }).hasAttribute("disabled")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "收起地点准备" }));
    expect(fetcher).toHaveBeenCalledTimes(3);
    expect(CHAT_ID).toBe(emptyConversation().id);
  });
  it("renders returned names as text, not executable HTML", async () => {
    const server: PlacesWorkspace = placesFixture({ batch: batchFixture([placeFixture("a", "<script>unsafe</script>")]) });
    vi.stubGlobal("fetch", vi.fn(async () => reply(server)));
    const view = render(<ChatPlaces conversation={placesConversation()} open blocked={false} />);
    await screen.findByRole("button", { name: /预览：<script>/ });
    expect(view.container.querySelector("script")).toBeNull();
  });
});
