import { beforeEach, describe, expect, it, vi } from "vitest";
import { isDialogueActionBatch, readDialogueActions } from "./dialogue-action";
import { readFullDialogueActionReceipt } from "./assistant";
import { CHAT_ID, OP_ID } from "./chat-test-fixtures";
vi.mock("./assistant", () => ({ readDialogueActionReceipt: vi.fn(), readFullDialogueActionReceipt: vi.fn() }));
const workspace = "10000000-0000-4000-8000-000000000010";
const pair = () => [{ name: "read_schedule", arguments: { date: null, day: 2 } }, { name: "read_budget", arguments: {} }];
const receipt = () => ({ schema_version: "dialogue-actions-v2", conversation_id: CHAT_ID, revision: 2, operation_id: OP_ID,
  workspace_version: workspace, actions: pair(), program_messages: [] as string[] });
beforeEach(() => vi.resetAllMocks());
describe("full candidate bounded proposal readback (not browser execution receipts)", () => {
  it("accepts a single original action or one exact independent read pair, never an arbitrary valid prefix", () => {
    expect(isDialogueActionBatch(pair())).toBe(true); expect(isDialogueActionBatch(pair().reverse())).toBe(true);
    expect(isDialogueActionBatch([{ name: "set_cost", arguments: { kind: "other", selector: null, value: "" } }])).toBe(true);
    for (const value of [[pair()[0], pair()[0]], [...pair(), pair()[0]], [pair()[0], { name: "set_cost", arguments: { kind: "other", selector: null, value: "3" } }],
      [{ name: "read_budget", arguments: { price: 0 } }]]) expect(isDialogueActionBatch(value)).toBe(false);
  });
  it("requires matching operation, conversation, revision and the exact request-time workspace UUID", async () => {
    vi.mocked(readFullDialogueActionReceipt).mockResolvedValue(receipt());
    expect((await readDialogueActions(CHAT_ID, 2, OP_ID, workspace, new AbortController().signal)).actions).toEqual(pair());
    for (const change of [{ revision: 3 }, { operation_id: workspace }, { conversation_id: workspace }, { workspace_version: OP_ID }, { repaired: true }, { action: pair()[0] }]) {
      vi.mocked(readFullDialogueActionReceipt).mockResolvedValue({ ...receipt(), ...change });
      await expect(readDialogueActions(CHAT_ID, 2, OP_ID, workspace, new AbortController().signal)).rejects.toThrow("不属于本次");
    }
  });
  it("keeps complete long program text bounded and bound, no truncation or source/schema loosening", async () => {
    const value = receipt(); value.actions = []; value.program_messages = ["整理摘要：" + "完整条件。".repeat(1800) + "<script>不执行</script>"];
    vi.mocked(readFullDialogueActionReceipt).mockResolvedValue(value);
    expect((await readDialogueActions(CHAT_ID, 2, OP_ID, workspace, new AbortController().signal)).program_messages).toEqual(value.program_messages);
    for (const messages of [["x".repeat(32001)], Array(5).fill("text"), ["汉".repeat(32000), "汉"], ["\u0000"], [""]]) {
      vi.mocked(readFullDialogueActionReceipt).mockResolvedValue({ ...value, program_messages: messages });
      await expect(readDialogueActions(CHAT_ID, 2, OP_ID, workspace, new AbortController().signal)).rejects.toThrow("未执行");
    }
  });
  it("accepts empty current receipts, and a cancelled read cannot publish an otherwise valid result", async () => {
    vi.mocked(readFullDialogueActionReceipt).mockResolvedValue({ ...receipt(), actions: [], workspace_version: null });
    expect((await readDialogueActions(CHAT_ID, 2, OP_ID, workspace, new AbortController().signal)).actions).toEqual([]);
    const signal = new AbortController(); signal.abort();
    await expect(readDialogueActions(CHAT_ID, 2, OP_ID, workspace, signal.signal)).rejects.toMatchObject({ name: "AbortError" });
  });
});
