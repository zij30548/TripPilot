import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import KnowledgeSearch from "./knowledge-search";
import type { KnowledgeResponse } from "@/lib/knowledge";

const backend = resolve(process.cwd(), "../backend");
const result = JSON.parse(execFileSync(resolve(backend, ".venv/bin/python"), ["-B", "-c", `import json
from tooling.travel.knowledge import build_index
from core.schemas.knowledge import KnowledgeRequest
print(build_index().search(KnowledgeRequest(venue_id="natural",question="学校团体预约")).model_dump_json())`], { cwd: backend, encoding: "utf8" })) as KnowledgeResponse;

let fetcher: ReturnType<typeof vi.fn>;
beforeEach(() => { fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher); });

function fillSearch() {
  fireEvent.change(screen.getByLabelText("场馆／馆区"), { target: { value: result.request.venue_id } });
  fireEvent.change(screen.getByLabelText("想查询的问题"), { target: { value: result.request.question } });
}

describe("minimal knowledge-library view", () => {
  it("starts with a plain search and truthful collapsed coverage, not automatic retrieval or answer actions", () => {
    render(<KnowledgeSearch />);
    expect(screen.getByRole("heading", { name: "知识库", level: 1 })).toBeTruthy();
    expect(screen.getByRole("region", { name: "开始资料查询" })).toBeTruthy();
    const coverage = screen.getByText("收录范围与使用提示").closest("details")!;
    expect(coverage.open).toBe(false);
    expect(coverage.textContent).toContain("图书馆、游乐园及其他地区尚未收录");
    fireEvent.click(screen.getByText("收录范围与使用提示"));
    fillSearch();
    expect(fetcher).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: /生成|识别|AI/ })).toBeNull();
  });

  it("keeps complete summaries and excerpts distinct, with source times discoverable without a second request", async () => {
    fetcher.mockResolvedValueOnce({ ok: true, text: async () => JSON.stringify(result) });
    render(<KnowledgeSearch />);
    fillSearch();
    await act(async () => fireEvent.submit(screen.getByRole("button", { name: "查询资料" }).closest("form")!));
    const matches = screen.getAllByRole("listitem");
    expect(matches).toHaveLength(result.matches.length);
    for (const [index, match] of result.matches.entries()) {
      const item = matches[index];
      expect(within(item).getByText("整理摘要")).toBeTruthy();
      expect(within(item).getByText(match.section.summary)).toBeTruthy();
      if (match.section.excerpt) {
        expect(within(item).getByText("原文摘录")).toBeTruthy();
        expect(item.querySelector("blockquote")?.textContent).toContain(match.section.excerpt);
      }
      const link = within(item).getByRole("link", { name: /打开官方原文/ });
      expect(link.getAttribute("href")).toBe(match.source.url);
      expect(link.getAttribute("target")).toBe("_blank");
      expect(link.getAttribute("rel")).toBe("noopener noreferrer");
      const details = within(item).getByText("日期与适用性").closest("details")!;
      expect(details.open).toBe(false);
      fireEvent.click(within(item).getByText("日期与适用性"));
      expect(details.textContent).toContain(match.source.fetched_at);
      expect(details.textContent).toContain(match.source.applicability);
    }
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toBe("/api/knowledge/search");
  });

  it("input changes remove prior results without sending another request", async () => {
    fetcher.mockResolvedValueOnce({ ok: true, text: async () => JSON.stringify(result) });
    render(<KnowledgeSearch />);
    fillSearch();
    await act(async () => fireEvent.submit(screen.getByRole("button", { name: "查询资料" }).closest("form")!));
    expect(screen.getByRole("region", { name: "资料检索结果" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("场馆／馆区"), { target: { value: "museum_east" } });
    expect(screen.queryByRole("region", { name: "资料检索结果" })).toBeNull();
    expect(screen.getByRole("region", { name: "开始资料查询" })).toBeTruthy();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
