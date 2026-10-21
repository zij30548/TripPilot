import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { BUSINESS_SERVICES, businessUsageNoticeText, clearBusinessUsageNotice, businessUsageNoticeSnapshot, recordBusinessUsageNotice } from "./business-usage-notices";
import { parseBusinessUsage, queryBusinessUsage } from "./business-usage";
import { apiFetch } from "./auth-session";

vi.mock("./auth-session", () => ({ apiFetch: vi.fn() }));
export function usageFixture() {
  return { version: "business-usage-v1", timezone: "Asia/Shanghai", now: "2026-10-18T09:00:01+08:00", reset_at: "2026-10-19T00:00:00+08:00",
    rate: { used: 3, limit: 60, window_seconds: 60, reset_at: "2026-10-18T09:01:00+08:00" },
    services: Object.fromEntries(BUSINESS_SERVICES.map(service => [service, { used: 1, limit: 10, remaining: 9 }])) };
}

describe("strict own-account business usage", () => {
  it("accepts actual Python own-usage output from an independently initialized disk ledger", () => {
    const backend = resolve(process.cwd(), "../backend");
    const output = execFileSync(`${backend}/.venv/bin/python`, ["-B", "-c", `
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from core.business_usage import SERVICES, UsageSettings, UsageStore
with TemporaryDirectory(prefix="trippilot-usage-contract-") as directory:
    os.environ["TRIPPILOT_DATABASE_PATH"] = str(Path(directory) / "unused-identity.sqlite3")
    settings = UsageSettings(_env_file=None, business_usage_path=str(Path(directory) / "usage.sqlite3"),
        business_rate_window_seconds=60, business_rate_account_limit=60, business_rate_global_limit=300,
        business_daily_limits={service: {"account":3, "global":9} for service in SERVICES})
    store = UsageStore(settings, clock=lambda: 1792285201)
    store.initialize()
    store.reserve("a" * 32, "amap_poi")
    print(json.dumps(store.own_usage("a" * 32)))
`], { cwd: backend, env: { ...process.env, PYTHONPATH: backend, PYTHONDONTWRITEBYTECODE: "1" }, encoding: "utf8", timeout: 15_000, maxBuffer: 32 * 1024 });
    const value = parseBusinessUsage(JSON.parse(output));
    expect(value.version).toBe("business-usage-v1");
    expect(Object.keys(value.services).sort()).toEqual([...BUSINESS_SERVICES].sort());
    expect(value.services.amap_poi).toEqual({ used: 1, limit: 3, remaining: 2 });
    expect(value.rate.used).toBe(1);
    for (const service of BUSINESS_SERVICES.filter(service => service !== "amap_poi")) expect(value.services[service].used).toBe(0);
  });
  it("accepts own-only exact counters, including disabled and limits lowered below existing use", () => {
    const value = usageFixture();
    value.services.amap_poi = { used: 4, limit: 2, remaining: 0 };
    value.services.qwen_vision = { used: 0, limit: 0, remaining: 0 };
    expect(parseBusinessUsage(value)).toEqual(value);
  });
  it("rejects extra users/globalcounts, missing services, unsafe counts and inconsistent resets/remaining", () => {
    const missing = usageFixture(); delete missing.services.media_video;
    for (const value of [
      { ...usageFixture(), users: [] }, { ...usageFixture(), global: {} }, { ...usageFixture(), timezone: "UTC" }, missing,
      { ...usageFixture(), services: { ...usageFixture().services, media_video: { used: -1, limit: 10, remaining: 11 } } },
      { ...usageFixture(), services: { ...usageFixture().services, amap_poi: { used: 1, limit: 10, remaining: 10 } } },
      { ...usageFixture(), rate: { ...usageFixture().rate, used: Number.MAX_SAFE_INTEGER + 1 } },
      { ...usageFixture(), reset_at: "2026-10-18T00:00:00+08:00" },
      { ...usageFixture(), now: "2026-10-18T09:00:01" },
    ]) expect(() => parseBusinessUsage(value)).toThrow("调用额度响应无效");
  });
  it("reads only the authenticated same-origin endpoint and bounds the response without retry", async () => {
    const caller = new AbortController();
    vi.mocked(apiFetch).mockResolvedValueOnce(new Response(JSON.stringify(usageFixture())));
    expect(await queryBusinessUsage(caller.signal)).toEqual(usageFixture());
    expect(apiFetch).toHaveBeenLastCalledWith("/api/usage", { signal: caller.signal });
    vi.mocked(apiFetch).mockResolvedValueOnce(new Response("x".repeat(32 * 1024 + 1)));
    await expect(queryBusinessUsage(caller.signal)).rejects.toThrow("调用额度响应无效");
    expect(apiFetch).toHaveBeenCalledTimes(2);
  });
  it("uses fixed safe errors, never backend exception text or automatic retries", async () => {
    vi.mocked(apiFetch).mockClear().mockResolvedValueOnce(new Response("private path or SQL", { status: 503 }));
    await expect(queryBusinessUsage(new AbortController().signal)).rejects.toThrow("额度暂时无法核验");
    expect(apiFetch).toHaveBeenCalledTimes(1);
  });
});

describe("allowlisted quota response notices", () => {
  it("reads only safe metadata, distinguishing daily/global, disabled and unavailable errors", () => {
    clearBusinessUsageNotice();
    const response = new Response("do not read this body", { status: 429, headers: { "X-Business-Usage-Code": "business_quota_exhausted",
      "X-Business-Usage-Service": "qwen_vision", "X-Business-Usage-Scope": "global", "Retry-After": "300", "X-Business-Usage-Reset-At": "2026-10-19T00:00:00+08:00" } });
    recordBusinessUsageNotice(response, 4);
    expect(response.bodyUsed).toBe(false);
    expect(businessUsageNoticeText(businessUsageNoticeSnapshot()!)).toContain("服务整体照片／选中帧识别的当日调用额度已用完");
    expect(businessUsageNoticeText(businessUsageNoticeSnapshot()!)).toContain("至少等待 300 秒");
    recordBusinessUsageNotice(new Response(null, { status: 429, headers: { "X-Business-Usage-Code": "business_service_disabled", "X-Business-Usage-Service": "media_video" } }), 4);
    expect(businessUsageNoticeText(businessUsageNoticeSnapshot()!)).toContain("视频抽帧暂未开放");
    recordBusinessUsageNotice(new Response(null, { status: 503, headers: { "X-Business-Usage-Code": "business_usage_unavailable" } }), 4);
    expect(businessUsageNoticeText(businessUsageNoticeSnapshot()!)).toContain("只读历史仍可使用");
  });
  it("does not render arbitrary headers, raw unknown names, malformed waits or mismatched statuses", () => {
    const invalidHeaders: Record<string, string>[] = [
      { "X-Business-Usage-Code": "unknown" },
      { "X-Business-Usage-Code": "business_quota_exhausted", "X-Business-Usage-Service": "private secret" },
      { "X-Business-Usage-Code": "business_rate_limited", "Retry-After": "never" },
      { "X-Business-Usage-Code": "business_rate_limited", "X-Business-Usage-Scope": "another-user" },
      { "X-Business-Usage-Code": "business_usage_unavailable" },
    ];
    for (const headers of invalidHeaders) {
      clearBusinessUsageNotice();
      recordBusinessUsageNotice(new Response(null, { status: 429, headers }), 1);
      expect(businessUsageNoticeSnapshot()).toBeNull();
    }
  });
});
