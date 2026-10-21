import { describe, expect, it, vi } from "vitest";
import registry from "../../../shared/dialogue-actions.json";
import { isDialogueAction, readDialogueAction, confirmationReply } from "./dialogue-action";
import { DialogueConfirmation, localDialogueAction } from "./dialogue-confirmation";
import { readDialogueActionReceipt } from "./assistant";
vi.mock("./assistant", () => ({ readDialogueActionReceipt: vi.fn() }));

describe("dialogue actions and explicit confirmation", () => {
  it("uses the shared finite catalogue and rejects extra fields or arbitrary tools", () => {
    expect(new Set(registry.actions.map(a => a.name)).size).toBe(registry.actions.length);
    expect(isDialogueAction({ name: "read_budget", arguments: {} })).toBe(true);
    expect(isDialogueAction({ name: "read_budget", arguments: { owner_id: "another" } })).toBe(false);
    expect(isDialogueAction({ name: "fetch_url", arguments: { url: "https://example.org" } })).toBe(false);
    expect(isDialogueAction({ name: "read_budget", arguments: {}, execute: true })).toBe(false);
  });
  it("rejects impossible dates, mixed date selection, invalid times and unsafe numbers", () => {
    for (const args of [{ date: "2026-02-30", day: null }, { date: "2026-10-22", day: 1 }, { date: null, day: 8 }, { date: null, day: 1.5 }])
      expect(isDialogueAction({ name: "read_schedule", arguments: args })).toBe(false);
    expect(isDialogueAction({ name: "set_lunch", arguments: { enabled: true, start: "13:00", end: "12:00" } })).toBe(false);
    expect(isDialogueAction({ name: "set_stay", arguments: { selector: "公园", minutes: Infinity } })).toBe(false);
  });
  it("rejects blank or padded identifiers while preserving exact user cost text", () => {
    for (const keyword of [" ", "\t", " 外滩", "外滩 ", "上海\n外滩"])
      expect(isDialogueAction({ name: "search_places", arguments: { keyword, role: "required" } })).toBe(false);
    for (const selector of ["", " ", " 1", "1 ", "地点\u0000"])
      expect(isDialogueAction({ name: "remove_place", arguments: { selector } })).toBe(false);
    expect(isDialogueAction({ name: "remove_place", arguments: { selector: "1" } })).toBe(true);
    expect(isDialogueAction({ name: "set_cost", arguments: { kind: "other", selector: null, value: " 1.234 " } })).toBe(true);
  });
  it("preserves blank/zero/invalid cost text instead of repairing it", () => {
    for (const value of ["", "0", "-2", "abc", "1.234"])
      expect(isDialogueAction({ name: "set_cost", arguments: { kind: "other", selector: null, value } })).toBe(true);
    expect(isDialogueAction({ name: "set_cost", arguments: { kind: "ticket", selector: null, value: "0" } })).toBe(false);
  });
  it("requires a bounded integer history offset and resolves explicit pages without a model", () => {
    for (const offset of [0, 10, 10000]) expect(isDialogueAction({ name: "list_saved", arguments: { offset } })).toBe(true);
    for (const offset of [-1, 10001, 1.5, "10", true, null]) expect(isDialogueAction({ name: "list_saved", arguments: { offset } })).toBe(false);
    expect(isDialogueAction({ name: "list_saved", arguments: {} })).toBe(false);
    expect(localDialogueAction("查看历史")).toEqual({ name: "list_saved", arguments: { offset: 0 } });
    expect(localDialogueAction("查看第2页历史")).toEqual({ name: "list_saved", arguments: { offset: 10 } });
    expect(localDialogueAction("查看第1001页历史")).toEqual({ name: "list_saved", arguments: { offset: 10000 } });
    for (const command of ["查看第0页历史", "查看第1002页历史", "查看第1.5页历史", "查看第2页历史并生成行程"])
      expect(localDialogueAction(command)).toBeNull();
  });
  it("binds receipt to owner-scoped conversation, revision and exact turn", async () => {
    const value = { schema_version: "dialogue-action-v1", conversation_id: "one", revision: 5, operation_id: "op", action: { name: "read_budget", arguments: {} } };
    vi.mocked(readDialogueActionReceipt).mockResolvedValue(value);
    expect(await readDialogueAction("one", 5, "op", new AbortController().signal)).toEqual(value.action);
    for (const [id, revision, op] of [["two", 5, "op"], ["one", 4, "op"], ["one", 5, "old"]] as const)
      await expect(readDialogueAction(id, revision, op, new AbortController().signal)).rejects.toThrow("不属于当前回复");
  });
  it("consumes one frozen action once, rejecting stale versions and ABA tokens", () => {
    const gate = new DialogueConfirmation(), binding = { conversation: "a", revision: 1, epoch: 2, workspace: "v1" };
    const action = { name: "generate_schedule", arguments: {} };
    const first = gate.offer(action, binding);
    gate.clear(); const next = gate.offer(action, binding);
    expect(gate.take(first, binding)).toBeNull();
    expect(gate.take(next, binding)).toEqual(action);
    expect(gate.take(next, binding)).toBeNull();
    for (const changed of [{ ...binding, revision: 2 }, { ...binding, epoch: 3 }, { ...binding, workspace: "v2" }, { ...binding, conversation: "b" }]) {
      const token = gate.offer(action, binding);
      expect(gate.take(token, changed)).toBeNull();
    }
  });
  it("does not turn negation, quoted commands or mixed requests into confirmation", () => {
    for (const s of ["不要确认", "确认后再改成北京", "他说确认", "确认这个是什么意思"])
      expect(confirmationReply(s)).toBeNull();
    expect(confirmationReply(" 确认。 ")).toBe("confirm");
    expect(localDialogueAction("不要生成行程")).toBeNull();
    expect(localDialogueAction("查看费用并保存")).toBeNull();
    expect(localDialogueAction("确认地点 2")).toEqual({ name: "confirm_place", arguments: { number: 2 } });
    expect(localDialogueAction("查看第七天")).toEqual({ name: "read_schedule", arguments: { date: null, day: 7 } });
  });
});
