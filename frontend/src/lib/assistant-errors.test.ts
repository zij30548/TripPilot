import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { sendFullDialogueTurn, sendToolDialogueTurn } from "./assistant";
import { chatErrorMessage } from "./chat";
import { CHAT_ID, OP_ID } from "./chat-test-fixtures";

const privateDetail = "PRIVATE_PROMPT_AND_AUTH_DO_NOT_DISPLAY";
const reply = (code: unknown) => new Response(JSON.stringify({ detail: {
  code, message: privateDetail, diagnostic: { input: privateDetail, path: "/private/credentials" },
  input: { user_text: privateDetail, Authorization: "Bearer private" },
} }), { status: 502, headers: { "Content-Type": "application/json" } });
const body = () => ({ operation_id: OP_ID, expected_revision: 0,
  text: "想去杭州，带父母轻松玩几天",
  dialogue_context: { workspace_version: crypto.randomUUID(), display: "none" as const, items: [] } });

let fetcher: ReturnType<typeof vi.fn>;
beforeEach(() => { fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher); });
afterEach(() => { vi.useRealTimers(); });

describe("finite assistant failure diagnostics", () => {
  it.each([
    ["tool_arguments", "操作参数未通过校验"],
    ["requirements_invalid", "原话依据不一致"],
    ["model_structure", "原生工具结构校验"],
    ["ungrounded_answer", "没有返回本次需要的受控工具"],
    ["tool_not_allowed", "不在当前允许范围"],
    ["tool_schema", "工具契约检查未通过"],
    ["travel_requirements_invalid", "原话依据不一致"],
    ["travel_tool_required", "没有返回本次需要的旅行工具"],
    ["travel_multiple_tools", "本次未派发这些操作"],
    ["travel_tool_failed", "未发布为有效业务结果"],
    ["travel_role_arguments", "操作参数未通过校验"],
    ["travel_role_forbidden", "不属于当前旅行角色的权限"],
    ["intent_structure", "意图分类回复未通过结构校验"],
  ])("maps a real 502 envelope %s to fixed text/code without a retry or private detail", async (code, expected) => {
    fetcher.mockResolvedValue(reply(`assistant_${code}`));
    const pending = sendFullDialogueTurn(CHAT_ID, body(), new AbortController().signal);
    const failure = await pending.catch(error => error);
    expect(failure).toBeInstanceOf(Error);
    expect(failure.message).toContain(expected);
    expect(failure.message).toContain(`原因码：${code}`);
    expect(failure.message).toContain("不会自动重试");
    expect(failure.message).not.toContain(privateDetail);
    expect(failure.message).not.toContain("/private/");
    expect(failure.message).not.toContain("Bearer");
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toBe(`/api/chat/conversations/${CHAT_ID}/assistant-turns`);
  });

  it("keeps v2 legacy requests diagnostic without applying a response or falling back", async () => {
    fetcher.mockResolvedValue(reply("assistant_requirements_invalid"));
    await expect(sendToolDialogueTurn(CHAT_ID, body(), new AbortController().signal))
      .rejects.toThrow("原因码：requirements_invalid");
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(new Headers(fetcher.mock.calls[0][1].headers).get("X-TripPilot-Surface")).toBe("dialogue-v2");
  });

  it.each([null, 7, {}, "assistant_new_private_failure", "assistant_" + privateDetail,
    "assistant___proto__", "assistant_constructor", "assistant_toString", "assistant_chat___proto__"])(
    "does not echo an unrecognized code or any backend details (%s)", async code => {
      fetcher.mockResolvedValue(reply(code));
      const failure = await sendFullDialogueTurn(CHAT_ID, body(), new AbortController().signal).catch(error => error);
      expect(failure).toBeInstanceOf(Error);
      expect(failure.message).toBe("本次对话未完成，请重新读取会话核对状态；不会自动重试。");
      expect(failure.message).not.toContain(privateDetail);
      expect(failure.message).not.toContain("private_failure");
      expect(fetcher).toHaveBeenCalledTimes(1);
    },
  );

  it("preserves existing known errors and guards direct chat readback prototype keys", () => {
    expect(chatErrorMessage("references")).toBe("模型提议未通过本轮原文依据核对，未应用本次提议。");
    expect(chatErrorMessage("chat_references")).toBe(chatErrorMessage("references"));
    expect(chatErrorMessage("request_failed")).toBe("上次对话未完成，未应用本次提议。");
    for (const code of ["__proto__", "constructor", "toString", "chat___proto__"])
      expect(chatErrorMessage(code)).toBe("本次对话未完成，请重新读取会话核对状态；不会自动重试。");
  });

  it("does not publish a received diagnostic after its request has been cancelled", async () => {
    const controller = new AbortController();
    fetcher.mockImplementation(async () => { controller.abort(); return reply("assistant_tool_arguments"); });
    await expect(sendFullDialogueTurn(CHAT_ID, body(), controller.signal)).rejects.toMatchObject({ name: "AbortError" });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
