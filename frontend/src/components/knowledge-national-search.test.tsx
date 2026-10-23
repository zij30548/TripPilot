import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import KnowledgeNationalSearch from "./knowledge-national-search";
import { nationalCatalogFixture, nationalResponseFixture } from "@/lib/knowledge-national.test-fixtures";

let fetcher: ReturnType<typeof vi.fn>;
const response = (value: unknown) => ({ ok: true, text: async () => JSON.stringify(value) }) as Response;
beforeEach(() => { fetcher = vi.fn().mockResolvedValueOnce(response(nationalCatalogFixture())); vi.stubGlobal("fetch", fetcher); });
async function start() { const view = render(<KnowledgeNationalSearch />); await act(async () => {}); return view; }
function fill() {
  fireEvent.change(screen.getByLabelText("城市"), { target: { value: "310000" } });
  fireEvent.change(screen.getByLabelText("场馆／馆区"), { target: { value: "museum_people" } });
  fireEvent.change(screen.getByLabelText("想查询的问题"), { target: { value: "预约说明" } });
}
describe("national reference page remains retrieval-only", () => {
  it("offers dynamic city/type/venue and optional date with clear limited coverage, no answer controls", async () => {
    await start(); expect(screen.getByRole("heading", { name: "知识库", level: 1 })).toBeTruthy();
    expect(screen.getByRole("option", { name: "北京" })).toBeTruthy(); expect(screen.getByRole("option", { name: "游乐园" })).toBeTruthy();
    fill(); expect(fetcher).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/AI问答暂未开放/)).toBeTruthy(); expect(screen.getByText(/图书馆尚未收录/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /AI|回答|生成/ })).toBeNull();
  });
  it("renders complete summary separately from excerpt, source dates and exact new-tab reviewed link", async () => {
    await start(); fill(); const value = nationalResponseFixture();
    value.matches[0].section.summary = "受控完整摘要<script>不能执行</script>，不是官方原话。";
    fetcher.mockResolvedValueOnce(response(value));
    await act(async () => fireEvent.submit(screen.getByRole("button", { name: "查询资料" }).closest("form")!));
    const item = screen.getByRole("listitem"), match = value.matches[0];
    expect(within(item).getByText(match.section.summary)).toBeTruthy(); expect(item.querySelector("script")).toBeNull();
    expect(within(item).getByText("整理摘要")).toBeTruthy(); expect(within(item).getByText("原文短摘录")).toBeTruthy();
    const link = within(item).getByRole("link", { name: /打开官方原文/ });
    expect(link.getAttribute("href")).toBe(match.source.url); expect(link.getAttribute("target")).toBe("_blank"); expect(link.getAttribute("rel")).toBe("noopener noreferrer");
    const details = within(item).getByText("来源日期与适用范围").closest("details")!;
    fireEvent.click(within(item).getByText("来源日期与适用范围")); expect(details.textContent).toContain(match.source.fetched_at); expect(details.textContent).toContain("未知");
    expect(screen.getByText(/尚未指定参观日期/)).toBeTruthy(); expect(fetcher.mock.calls.map(row => row[0])).toEqual(["/api/knowledge/catalog-v2", "/api/knowledge/search-v2"]);
  });
  it("city/type/date edits remove old results, never issue a replacement query", async () => {
    await start(); fill(); fetcher.mockResolvedValueOnce(response(nationalResponseFixture()));
    await act(async () => fireEvent.submit(screen.getByRole("button", { name: "查询资料" }).closest("form")!));
    fireEvent.change(screen.getByLabelText("参观日期（可选）"), { target: { value: "2026-11-01" } });
    expect(screen.queryByRole("region", { name: "资料检索结果" })).toBeNull();
    fireEvent.change(screen.getByLabelText("城市"), { target: { value: "110000" } });
    expect((screen.getByLabelText("场馆／馆区") as HTMLSelectElement).value).toBe("");
    expect(screen.getByRole("option", { name: "受控北京园" })).toBeTruthy(); expect(screen.queryByRole("option", { name: "受控上海馆" })).toBeNull();
    fireEvent.change(screen.getByLabelText("场馆类型"), { target: { value: "museum" } }); expect(screen.queryByRole("option", { name: "受控北京园" })).toBeNull(); expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it("no result is not admission advice and does not produce AI status/judgments", async () => {
    await start(); fill(); const value = nationalResponseFixture(); value.matches = []; value.status = "no_match"; fetcher.mockResolvedValueOnce(response(value));
    await act(async () => fireEvent.submit(screen.getByRole("button", { name: "查询资料" }).closest("form")!));
    expect(screen.getByText("未找到相关资料")).toBeTruthy(); expect(screen.queryByText("部分回答")).toBeNull(); expect(screen.queryByRole("button", { name: /回答/ })).toBeNull();
  });
  it("cancel remains keyboard reachable and late data cannot resurrect results", async () => {
    await start(); fill(); let complete!: (value: Response) => void;
    fetcher.mockReturnValueOnce(new Promise<Response>(resolve => { complete = resolve; }));
    fireEvent.submit(screen.getByRole("button", { name: "查询资料" }).closest("form")!);
    const cancel = screen.getByRole("button", { name: "取消查询" }); cancel.focus(); expect(document.activeElement).toBe(cancel); fireEvent.click(cancel);
    await act(async () => complete(response(nationalResponseFixture())));
    expect(screen.getByText("已取消本次资料查询。")).toBeTruthy(); expect(screen.queryByRole("region", { name: "资料检索结果" })).toBeNull(); expect(fetcher).toHaveBeenCalledTimes(2);
  });
});
