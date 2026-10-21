import { describe, expect, it } from "vitest";
import { bindDialogueSelection } from "./dialogue-selection";
import type { DialogueSelectionContext } from "./assistant";

const context: DialogueSelectionContext = { workspace_version: "10000000-0000-4000-8000-000000000001", display: "schedule", items: [
  { ordinal: 1, id: "required", name: "同名地点", type: "place" },
  { ordinal: 2, id: "optional", name: "同名地点", type: "place" },
  { ordinal: 3, id: "2026-10-22", name: "2026-10-22", type: "date" },
] };
describe("frozen displayed ordinal binding", () => {
  it("never reinterprets first visited required as first optional, even when names collide", () => {
    for (const name of ["preview_exclusion", "exclude_candidate", "restore_candidate", "set_stay", "set_visit_mode", "remove_place"]) {
      const action = { name, arguments: { selector: "1" } };
      expect(bindDialogueSelection(action, context)?.arguments.selector).toBe("required");
      expect(action.arguments.selector).toBe("1");
    }
    expect(bindDialogueSelection({ name: "set_cost", arguments: { kind: "ticket", selector: "2", value: "50" } }, context)?.arguments.selector).toBe("optional");
  });
  it("rejects absent, wrong-kind, duplicated or out-of-range display identity", () => {
    const action = { name: "preview_exclusion", arguments: { selector: "3" } };
    expect(bindDialogueSelection(action, context)).toBeNull();
    expect(bindDialogueSelection(action)).toBeNull();
    expect(bindDialogueSelection({ ...action, arguments: { selector: "9" } }, context)).toBeNull();
    expect(bindDialogueSelection({ ...action, arguments: { selector: "1" } }, { ...context, items: [...context.items, context.items[0]] })).toBeNull();
    expect(bindDialogueSelection({ name: "read_saved", arguments: { selector: "1" } }, context)).toBeNull();
  });
  it("binds history to its frozen page and keeps exact IDs and full dates", () => {
    const c = { ...context, display: "history" as const, items: [{ ordinal: 1, type: "saved" as const, id: "second-page-record", name: "行程" }] };
    expect(bindDialogueSelection({ name: "restore_saved", arguments: { selector: "1" } }, c)?.arguments.selector).toBe("second-page-record");
    const action = { name: "set_cost", arguments: { kind: "meal", selector: "1", value: "0" } };
    expect(bindDialogueSelection(action, context)).toEqual(action);
    const exact = { name: "preview_exclusion", arguments: { selector: "optional" } };
    expect(bindDialogueSelection(exact, context)).toEqual(exact);
  });
});
