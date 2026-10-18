import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { isScheduleResponse, type ScheduleResponse } from "@/types/schedule";
import { scheduleBlocker } from "@/lib/schedule-blocker";
import { ScheduleResult } from "./schedule-preview";

const backend = resolve(process.cwd(), "../backend");
const examples: { label: string; response: ScheduleResponse; roads: string[][] }[] = JSON.parse(execFileSync(
  `${backend}/.venv/bin/python`, ["-B", "-m", "tests.export_schedule_blockers"], { cwd: backend, encoding: "utf8" },
));

describe("first schedule blocker, actual Python output and current strict TS contract", () => {
  it.each(examples)("$label: preserves validation and identifies only required failed directions", ({ response, roads }) => {
    expect(isScheduleResponse(response, response.request)).toBe(true);
    const before = JSON.stringify(response);
    const blocker = scheduleBlocker(response);
    expect(blocker?.roads.map(({ from, to }) => [from, to]) ?? []).toEqual(roads);
    expect(JSON.stringify(response)).toBe(before);
  });
  it("puts the first blocker, return direction and not-attempted relation above empty date content", () => {
    const response = examples.find(e => e.label === "return")!.response;
    render(<ScheduleResult response={response} />);
    const notice = screen.getByRole("region", { name: "首个排程阻断" });
    expect(notice.textContent).toContain("首先未能安排：测试地点-p1");
    expect(notice.textContent).toContain("测试地点-p1 → 测试地点-stay");
    expect(notice.textContent).not.toContain("测试地点-stay → 测试地点-p1");
    expect(notice.textContent).toContain("后续 2 个必去地点按固定顺序未继续尝试");
    expect(notice.textContent).toContain("本次没有试排可选地点");
    expect(notice.compareDocumentPosition(screen.getByRole("group", { name: "选择草案日期" })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
  it("does not promote an unused failed query to the blocker or show a blocker for a complete schedule", () => {
    const response = examples.find(e => e.label === "prefix")!.response;
    const { rerender } = render(<ScheduleResult response={response} />);
    const notice = screen.getByRole("region", { name: "首个排程阻断" });
    expect(within(notice).getByRole("list", { name: "所需失败路段" }).textContent).toContain("测试地点-p1 → 测试地点-p2");
    expect(within(notice).getByRole("list", { name: "所需失败路段" }).textContent).not.toContain("测试地点-stay → 测试地点-p2");
    rerender(<ScheduleResult response={examples[0].response} />);
    expect(screen.queryByRole("region", { name: "首个排程阻断" })).toBeNull();
  });
  it("shows missing edge evidence explicitly without inventing duration or claiming upstream failure", () => {
    const response = structuredClone(examples.find(e => e.label === "data_error")!.response);
    response.edges.shift();
    render(<ScheduleResult response={response} />);
    const notice = screen.getByRole("region", { name: "首个排程阻断" });
    expect(notice.textContent).toContain("缺少路线记录");
    expect(notice.textContent).toContain("不能据此推算交通时间");
  });
});
