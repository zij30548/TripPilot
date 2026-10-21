import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AssistantPreferences from "./assistant-preferences";
import type { Preference, PreferenceView } from "@/lib/assistant-preferences";
import { CHAT_ID, OTHER_CHAT_ID, deferred } from "@/lib/chat-test-fixtures";

const api = vi.hoisted(() => ({ request: vi.fn(), epoch: 0 }));
vi.mock("@/lib/assistant-preferences", () => ({ requestPreferences: api.request }));
vi.mock("@/lib/auth-session", () => ({ authSnapshot: () => ({ epoch: api.epoch }) }));
const preference = (version = 1): Preference => ({ key: "travel_preferences", value: "喜欢慢慢走", version, source: "user", consent: true, created_at: "2026-10-22T00:00:00Z", updated_at: "2026-10-22T00:00:00Z" });
const empty = (): PreferenceView => ({ items: [], versions: {} });
const click = async (name: string) => act(async () => { fireEvent.click(screen.getByRole("button", { name })); });
function expand() { const details = screen.getByLabelText("用户明确保存的偏好") as HTMLDetailsElement; details.open = true; fireEvent(details, new Event("toggle")); return details; }
beforeEach(() => { vi.resetAllMocks(); api.epoch = 0; api.request.mockResolvedValue(empty()); });

describe("explicit preference consent and isolated lifecycle (controlled storage)", () => {
  it("opening, reading and typing do not write; explicit consent and deletion use exact stored versions including tombstones", async () => {
    render(<AssistantPreferences conversationId={CHAT_ID} />); expand();
    expect(api.request).not.toHaveBeenCalled();
    await click("读取已保存偏好");
    fireEvent.change(screen.getByRole("textbox", { name: "希望记住的旅行偏好" }), { target: { value: "喜欢慢慢走" } });
    expect(api.request).toHaveBeenCalledTimes(1); expect(api.request.mock.calls[0][2]).toBeUndefined();
    api.request.mockResolvedValueOnce({ items: [preference()], versions: { travel_preferences: 1 } });
    await click("同意保存为长期偏好");
    expect(api.request.mock.calls[1][2]).toEqual({ action: "set", key: "travel_preferences", value: "喜欢慢慢走", consent: true, expected_version: 0 });
    api.request.mockResolvedValueOnce({ items: [], versions: { travel_preferences: 2 } });
    await click("删除这项偏好");
    expect(api.request.mock.calls[2][2]).toEqual({ action: "delete", key: "travel_preferences", expected_version: 1 });
    expect(screen.queryByText("喜欢慢慢走")).toBeNull();
    fireEvent.change(screen.getByRole("textbox", { name: "希望记住的旅行偏好" }), { target: { value: "新的主动偏好" } });
    await click("同意保存为长期偏好");
    expect(api.request.mock.calls[3][2].expected_version).toBe(2);
  });
  it.each(["success", "error"])("cancelled write ignores late %s, exposes uncertain outcome and requires reread", async outcome => {
    render(<AssistantPreferences conversationId={CHAT_ID} />); expand(); await click("读取已保存偏好");
    const late = deferred<PreferenceView>(); api.request.mockReturnValueOnce(late.promise);
    fireEvent.change(screen.getByRole("textbox", { name: "希望记住的旅行偏好" }), { target: { value: "用户主动输入" } });
    await click("同意保存为长期偏好");
    expect(api.request).toHaveBeenCalledTimes(2);
    await click("停止偏好请求");
    expect(api.request.mock.calls[1][1].aborted).toBe(true);
    expect(screen.getByText(/保存或删除可能已提交/)).toBeTruthy();
    await act(async () => { if (outcome === "success") late.resolve({ items: [preference()], versions: { travel_preferences: 1 } }); else late.reject(new Error("old error")); });
    expect(screen.queryByText("喜欢慢慢走")).toBeNull(); expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("button", { name: "同意保存为长期偏好" })).toBeNull();
    await click("读取已保存偏好"); expect(api.request).toHaveBeenCalledTimes(3);
    expect((screen.getByRole("textbox", { name: "希望记住的旅行偏好" }) as HTMLTextAreaElement).value).toBe("用户主动输入");
  });
  it.each(["success", "error"])("A→B→A and account change reject old %s without showing previous input or preference", async outcome => {
    const late = deferred<PreferenceView>(); api.request.mockReturnValueOnce(late.promise);
    const view = render(<AssistantPreferences conversationId={CHAT_ID} />); expand(); await click("读取已保存偏好");
    view.rerender(<AssistantPreferences conversationId={OTHER_CHAT_ID} />);
    view.rerender(<AssistantPreferences conversationId={CHAT_ID} />);
    expect(api.request.mock.calls[0][1].aborted).toBe(true);
    await act(async () => { if (outcome === "success") late.resolve({ items: [preference()], versions: { travel_preferences: 1 } }); else late.reject(new Error("other conversation")); });
    expect(screen.queryByText("喜欢慢慢走")).toBeNull(); expect(screen.queryByRole("alert")).toBeNull();
    await click("读取已保存偏好");
    fireEvent.change(screen.getByRole("textbox", { name: "希望记住的旅行偏好" }), { target: { value: "旧账号未提交" } });
    api.epoch = 1; view.rerender(<AssistantPreferences conversationId={CHAT_ID} />);
    expect(screen.queryByRole("textbox", { name: "希望记住的旅行偏好" })).toBeNull();
    await click("读取已保存偏好");
    expect((screen.getByRole("textbox", { name: "希望记住的旅行偏好" }) as HTMLTextAreaElement).value).toBe("");
  });
  it("closing during read and unmount cancel without any automatic write or retry", async () => {
    const late = deferred<PreferenceView>(); api.request.mockReturnValueOnce(late.promise);
    const view = render(<AssistantPreferences conversationId={CHAT_ID} />), details = expand();
    await click("读取已保存偏好");
    act(() => { details.open = false; fireEvent(details, new Event("toggle")); });
    expect(api.request.mock.calls[0][1].aborted).toBe(true);
    await act(async () => late.resolve({ items: [preference()], versions: { travel_preferences: 1 } }));
    act(() => { details.open = true; fireEvent(details, new Event("toggle")); });
    expect(screen.queryByText("喜欢慢慢走")).toBeNull();
    expect(api.request).toHaveBeenCalledTimes(1);
    const other = deferred<PreferenceView>(); api.request.mockReturnValueOnce(other.promise);
    await click("读取已保存偏好"); view.unmount();
    expect(api.request.mock.calls[1][1].aborted).toBe(true);
    await act(async () => other.reject(new Error("unmounted")));
  });
  it("failure requires explicit reread and never produces a fabricated saved preference", async () => {
    api.request.mockRejectedValueOnce(new Error("version changed"));
    render(<AssistantPreferences conversationId={CHAT_ID} />); expand(); await click("读取已保存偏好");
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("version changed"));
    expect(screen.queryByRole("textbox", { name: "希望记住的旅行偏好" })).toBeNull(); expect(api.request).toHaveBeenCalledTimes(1);
  });
});
