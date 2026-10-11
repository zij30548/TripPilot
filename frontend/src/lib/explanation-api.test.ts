import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { explanationFactCodes, explanationSnapshot, isExplanationResponse, queryExplanation, type ExplanationResponse } from "./explanation-api";
import { schedule } from "./weather-schedule-check.test-fixtures";
import type { ScheduleResponse } from "@/types/schedule";

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });
function response(): ExplanationResponse { return {
  contract_version: "schedule-explanation-v1", request_id: "test-1", model: "qwen3.7-flash-2026-07-15", schedule_generated_at: schedule().generated_at,
  explained_at: "2026-10-11T01:00:00Z", highlights: [{ message_code: "arrangement", fact_ids: ["f0"], text: "受控行程依据" }],
  limitations: ["基于提交快照", "营业未核验", "参考估时"], usage: { input_tokens: null, output_tokens: null },
}; }
describe("explanation boundary, no real credentials", () => {
  it("Python output and twelve compact schedule projections agree with actual TS guards", () => {
    const backend = resolve(process.cwd(), "../backend");
    const data: { name: string; schedule: ScheduleResponse; snapshot: unknown; explanation: ExplanationResponse; facts: { id: string; kind: string; text: string }[] }[] = JSON.parse(execFileSync(`${backend}/.venv/bin/python`, ["-B", "tests/export_explanation_contract.py"], {
      cwd: backend, env: { ...process.env, PYTHONPATH: backend, PYTHONDONTWRITEBYTECODE: "1", QWEN_API_KEY: "" }, encoding: "utf8", maxBuffer: 2 * 1024 * 1024,
    }));
    expect(data).toHaveLength(12);
    for (const item of data) {
      expect(explanationSnapshot(item.schedule), item.name).toEqual(item.snapshot);
      const codes = explanationFactCodes(item.schedule);
      expect(isExplanationResponse(item.explanation, item.name, item.schedule.generated_at, codes), item.name).toBe(true);
      expect(codes).toHaveLength(item.facts.length);
      const mapping: Record<string, string> = { summary: "arrangement", visit: "visits", movement: "movement", lunch: "reserved_time", wait: "reserved_time", return: "return_to_stay", unarranged: "not_arranged", optional: "optional_attempts", empty: "empty_day" };
      item.facts.forEach((fact, index) => { expect(fact.id).toBe(`f${index}`); expect(codes[index]).toBe(mapping[fact.kind]); });
      const corrupt = structuredClone(item.explanation); corrupt.highlights[0].fact_ids = ["f999"];
      expect(isExplanationResponse(corrupt, item.name, item.schedule.generated_at, codes)).toBe(false);
    }
  });
  it("projection excludes arbitrary descriptions, costs and geometry; original is immutable", () => {
    const value = schedule(); const original = structuredClone(value);
    const projected = JSON.stringify(explanationSnapshot(value));
    for (const forbidden of ["rules", "unknowns", "address", "category", "distance_meters", "message", "transit_route", "fare_cny", "segments"]) expect(projected).not.toContain(`"${forbidden}"`);
    expect(value).toEqual(original);
    value.days[0].return_time = "23:59";
    expect(() => explanationSnapshot(value)).toThrow();
  });
  it.each([
    (v: ExplanationResponse) => { v.request_id = "old"; },
    (v: ExplanationResponse) => { v.schedule_generated_at = "2020-01-01T00:00:00Z"; },
    (v: ExplanationResponse) => { v.highlights[0].fact_ids = ["f0", "f0"]; },
    (v: ExplanationResponse) => { v.highlights = []; },
    (v: ExplanationResponse) => { v.usage.output_tokens = 1025; },
    (v: ExplanationResponse) => { v.model = "more-expensive"; },
    (v: ExplanationResponse) => { v.limitations = []; },
  ])("rejects mismatched or malformed responses", (change) => {
    const value = response(); change(value); expect(isExplanationResponse(value, "test-1", schedule().generated_at)).toBe(false);
  });
  it("makes exactly one POST; never sends full schedule, settings secrets or retry", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify(response()))); vi.stubGlobal("fetch", fetcher);
    await queryExplanation(schedule(), "test-1", new AbortController().signal);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toBe("http://127.0.0.1:8000/trips/schedule-explanation");
    const init = fetcher.mock.calls[0][1]; expect(init.headers).toEqual({ "Content-Type": "application/json" });
    expect(Object.keys(JSON.parse(init.body))).toEqual(["contract_version", "request_id", "snapshot"]);
  });
  it("maps safe error codes, ignores arbitrary server text, and never retries", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: { code: "quota", message: "raw sensitive text" } }), { status: 502 })); vi.stubGlobal("fetch", fetcher);
    await expect(queryExplanation(schedule(), "test-1", new AbortController().signal)).rejects.toThrow("额度");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it("25-second deadline and external cancellation do not retry", async () => {
    vi.useFakeTimers(); const fetcher = vi.fn((_url, init) => new Promise((_yes, no) => init.signal.addEventListener("abort", () => no(new DOMException("aborted", "AbortError"))))); vi.stubGlobal("fetch", fetcher);
    const first = queryExplanation(schedule(), "test-1", new AbortController().signal);
    const assertion = expect(first).rejects.toThrow("超时"); await vi.advanceTimersByTimeAsync(25_000); await assertion;
    const controller = new AbortController(); const second = queryExplanation(schedule(), "test-2", controller.signal); controller.abort();
    await expect(second).rejects.toThrow("已取消"); expect(fetcher).toHaveBeenCalledTimes(2);
  });
});
