import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { captureFixture } from "./schedule-adjustment.test-fixtures";
import { isIntentResponse, queryAdjustmentIntent, type IntentResponse } from "./adjustment-intent-api";
import type { ScheduleCapture } from "./schedule-adjustment";
import { optionals, required, stamp } from "./weather-schedule-check.test-fixtures";

export function intent(base: ScheduleCapture, id: string, target = optionals[0].id): IntentResponse {
  return { contract_version: "adjustment-intent-v1", request_id: id, draft_version: base.version, schedule_generated_at: base.original.generated_at,
    interpreted_at: stamp, model: "qwen3.7-flash-2026-07-15", usage: { input_tokens: null, output_tokens: null },
    selection: { decision: "execute", action: "exclude_optional", target_id: target, reason_code: "single_optional" } };
}
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });
describe("bounded intent request and strict cross-language response", () => {
  it("accepts actual Python outputs for all three decisions", () => {
    const backend = resolve(process.cwd(), "../backend");
    const data = JSON.parse(execFileSync(`${backend}/.venv/bin/python`, ["-B", "tests/export_intent_contract.py"], { cwd: backend, env: { ...process.env, PYTHONPATH: backend, PYTHONDONTWRITEBYTECODE: "1", QWEN_API_KEY: "" }, encoding: "utf8" }));
    const base = { ...captureFixture(), original: data.schedule };
    expect(data.responses).toHaveLength(3);
    data.responses.forEach((value: unknown) => expect(isIntentResponse(value, base, "contract-1")).toBe(true));
  });
  it.each(["request", "version", "time", "required", "unarranged", "action", "extra", "reason", "usage"])("rejects %s corruption", kind => {
    const base = captureFixture(), value = intent(base, "i1");
    if (kind === "request") value.request_id = "old";
    if (kind === "version") value.draft_version++;
    if (kind === "time") value.schedule_generated_at = "2020-01-01T00:00:00Z";
    if (kind === "required") value.selection.target_id = required.id;
    if (kind === "unarranged") value.selection.target_id = optionals[2].id;
    if (kind === "action") Object.assign(value.selection, { action: "replan" });
    if (kind === "extra") Object.assign(value.selection, { text: "injected" });
    if (kind === "reason") value.selection.reason_code = "multiple_changes";
    if (kind === "usage") value.usage.output_tokens = 1025;
    expect(isIntentResponse(value, base, "i1")).toBe(false);
  });
  it("one POST, bounded compact snapshot, no retry or arbitrary provider text", async () => {
    const base = captureFixture(), fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(intent(base, "i1")))); vi.stubGlobal("fetch", fetcher);
    await queryAdjustmentIntent(base, "排除一个地点", "i1", new AbortController().signal);
    expect(fetcher).toHaveBeenCalledTimes(1);
    const payload = JSON.parse(fetcher.mock.calls[0][1].body);
    expect(Object.keys(payload)).toEqual(["contract_version", "request_id", "draft_version", "instruction", "snapshot"]);
    expect(JSON.stringify(payload)).not.toContain('"segments"'); expect(JSON.stringify(payload)).not.toContain('"fare_cny"');
    fetcher.mockResolvedValueOnce(new Response(JSON.stringify({ detail: { code: "quota", message: "sensitive" } }), { status: 502 }));
    await expect(queryAdjustmentIntent(base, "调整", "i2", new AbortController().signal)).rejects.toThrow("额度");
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it("invalid input and pre-aborted signals never fetch; 25s timeout never retries", async () => {
    vi.useFakeTimers(); const fetcher = vi.fn((_url, init) => new Promise((_yes, no) => init.signal.addEventListener("abort", () => no(new DOMException("abort", "AbortError"))))); vi.stubGlobal("fetch", fetcher);
    const base = captureFixture(), abort = new AbortController(); abort.abort();
    await expect(queryAdjustmentIntent(base, "", "i1", abort.signal)).rejects.toThrow();
    await expect(queryAdjustmentIntent(base, "x".repeat(401), "i1", abort.signal)).rejects.toThrow();
    await expect(queryAdjustmentIntent(base, "调整", "i1", abort.signal)).rejects.toThrow();
    expect(fetcher).not.toHaveBeenCalled();
    const pending = queryAdjustmentIntent(base, "调整", "i1", new AbortController().signal);
    const assertion = expect(pending).rejects.toThrow("超时"); await vi.advanceTimersByTimeAsync(25000); await assertion;
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
