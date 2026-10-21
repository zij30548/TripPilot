import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ProductShell from "./product-shell";

const route = vi.hoisted(() => ({ pathname: "/" }));
vi.mock("next/navigation", () => ({ usePathname: () => route.pathname }));
vi.mock("./chat/conversation-workspace", () => ({ default: () => <section aria-label="纯聊天"><textarea aria-label="未发送内容" /></section> }));
vi.mock("./knowledge-search", () => ({ default: () => <section aria-label="资料页"><input aria-label="资料问题" /></section> }));
vi.mock("./evaluation-workspace", () => ({ default: ({ active }: { active: boolean }) => <section aria-label="评测页" data-active={active}>执行回执</section> }));

describe("three-surface persistent product shell", () => {
  beforeEach(() => { route.pathname = "/"; });
  it("has exactly three main destinations without travel or demo controls", () => {
    render(<ProductShell><p>route placeholder</p></ProductShell>);
    expect(screen.getByRole("navigation", { name: "主导航" }).querySelectorAll("a")).toHaveLength(3);
    expect(screen.getByRole("link", { name: "对话助手" }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("link", { name: "知识库" }).getAttribute("href")).toBe("/knowledge");
    expect(screen.getByRole("link", { name: "Agent 评测" }).getAttribute("href")).toBe("/evaluation");
    expect(screen.queryByText("route placeholder")).toBeNull();
    expect(screen.queryByRole("button", { name: /行程|执行记录|需求卡/ })).toBeNull();
  });
  it("navigation preserves unsent text and knowledge input without remounting", () => {
    const view = render(<ProductShell>{null}</ProductShell>);
    const chat = screen.getByRole("textbox", { name: "未发送内容" });
    fireEvent.change(chat, { target: { value: "先保留，不发送" } });
    route.pathname = "/knowledge"; view.rerender(<ProductShell>{null}</ProductShell>);
    expect(screen.queryByRole("textbox", { name: "未发送内容" })).toBeNull();
    fireEvent.change(screen.getByRole("textbox", { name: "资料问题" }), { target: { value: "预约规则" } });
    route.pathname = "/evaluation"; view.rerender(<ProductShell>{null}</ProductShell>);
    expect(screen.getByRole("region", { name: "评测页" }).getAttribute("data-active")).toBe("true");
    route.pathname = "/"; view.rerender(<ProductShell>{null}</ProductShell>);
    expect(screen.getByRole("textbox", { name: "未发送内容" })).toBe(chat);
    expect((chat as HTMLTextAreaElement).value).toBe("先保留，不发送");
    route.pathname = "/knowledge"; view.rerender(<ProductShell>{null}</ProductShell>);
    expect((screen.getByRole("textbox", { name: "资料问题" }) as HTMLInputElement).value).toBe("预约规则");
  });
  it("identity remount clears inputs; legacy direct routes still render separately", () => {
    const view = render(<ProductShell key="account-a">{null}</ProductShell>);
    fireEvent.change(screen.getByRole("textbox", { name: "未发送内容" }), { target: { value: "A的未发内容" } });
    view.rerender(<ProductShell key="account-b">{null}</ProductShell>);
    expect((screen.getByRole("textbox", { name: "未发送内容" }) as HTMLTextAreaElement).value).toBe("");
    route.pathname = "/legacy"; view.rerender(<ProductShell><p>旧兼容入口</p></ProductShell>);
    expect(screen.getByText("旧兼容入口")).toBeTruthy();
    expect(screen.queryByRole("navigation", { name: "主导航" })).toBeNull();
  });
});
