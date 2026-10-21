import { describe, expect, it } from "vitest";
import { API_ENDPOINTS } from "../api-endpoints";
import { assertBuiltBackend, backendOrigin, backendRewrites, FORWARDING_BODY_BYTES, FORWARDING_TIMEOUT_MS } from "./backend-routing";

describe("trusted server-only same-origin forwarding", () => {
  it("allows a dev-only loopback default, never a production fallback", () => {
    expect(backendOrigin(undefined, true)).toBe("http://127.0.0.1:8000");
    expect(() => backendOrigin(undefined, false)).toThrow("TRIPPILOT_BACKEND_ORIGIN");
    expect(() => backendOrigin("", true)).toThrow();
    expect(backendOrigin("http://127.0.0.1:18081/", false)).toBe("http://127.0.0.1:18081");
    expect(backendOrigin("https://backend.example.test", false)).toBe("https://backend.example.test");
  });
  it.each([
    "//backend.test", "file:///tmp/backend", "https://user:private@backend.test", "http://backend.test/api",
    "http://backend.test/../", "http://backend.test?upstream=private", "http://backend.test/#private",
    " http://backend.test", "http://backend.test\n", "http://backend.test\\private", "http://127.1:8000",
  ])("rejects ambiguous or unsafe backend config without echoing it (%#)", value => {
    expect(() => backendOrigin(value, false)).toThrow("TRIPPILOT_BACKEND_ORIGIN");
    try { backendOrigin(value, false); } catch (error) {
      expect(String(error)).not.toContain(value);
      expect(String(error)).not.toContain("private");
    }
  });
  it("has an explicit resource allowlist, never a user-supplied URL or catch-all", () => {
    const target = "http://127.0.0.1:18081";
    const rules = backendRewrites(target);
    expect(Object.keys(API_ENDPOINTS)).toHaveLength(22);
    expect(new Set(rules.map(r => r.source)).size).toBe(rules.length);
    for (const source of Object.values(API_ENDPOINTS)) {
      expect(source.startsWith("/api/")).toBe(true);
      expect(rules).toContainEqual({ source, destination: `${target}${source.slice(4)}` });
    }
    expect(rules).toContainEqual({ source: "/_AMapService/v3/config/district", destination: `${target}/_AMapService/v3/config/district` });
    expect(rules).toContainEqual({ source: "/api/chat/cities", destination: `${target}/chat/cities` });
    for (const suffix of ["", "/turns", "/cancel", "/confirm", "/delete", "/places", "/assistant-turns", "/assistant-state", "/assistant-preferences", "/dialogue-action"]) {
      expect(rules).toContainEqual({
        source: `/api/chat/conversations/:id([0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})${suffix}`,
        destination: `${target}/chat/conversations/:id${suffix}`,
      });
    }
    expect(rules.some(r => r.source.includes(":path") || r.source.includes(":url"))).toBe(false);
    expect(rules.some(r => r.source === "/api/chat/conversations/:id/places" || r.source.includes("/places/:"))).toBe(false);
    expect(FORWARDING_BODY_BYTES).toBeGreaterThan(20 * 1024 * 1024);
    expect(FORWARDING_TIMEOUT_MS).toBeGreaterThan(60_000);
  });
  it("adds only a UUID-scoped dialogue receipt path and leaves identity, CSRF and legacy endpoints on the same trusted origin", () => {
    const target = "http://127.0.0.1:18081", rules = backendRewrites(target);
    const dialogue = rules.filter(rule => rule.source.includes("dialogue"));
    expect(dialogue).toEqual([{
      source: "/api/chat/conversations/:id([0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})/dialogue-action",
      destination: `${target}/chat/conversations/:id/dialogue-action`,
    }]);
    for (const path of ["/auth/session", "/auth/login", "/auth/register", "/auth/logout", "/knowledge/answer", "/media/landmark", "/media/video-frames", "/trips/schedule-preview"]) {
      expect(rules).toContainEqual({ source: `/api${path}`, destination: `${target}${path}` });
    }
    const missing = { rewrites: { beforeFiles: rules.filter(rule => !rule.source.endsWith("/dialogue-action")) } };
    expect(() => assertBuiltBackend(missing, rules)).toThrow("重新构建");
    for (const source of ["/api/chat/conversations/:id/dialogue-action", "/api/chat/conversations/:path*", "/api/dialogue-action"]) {
      const altered = rules.map(rule => rule.source.endsWith("/dialogue-action") ? { ...rule, source } : rule);
      expect(() => assertBuiltBackend({ rewrites: { beforeFiles: altered } }, rules)).toThrow();
    }
  });
  it("rejects stale/missing build routing rather than silently using the old backend", () => {
    const rules = backendRewrites("http://127.0.0.1:18081");
    const manifest = { rewrites: { beforeFiles: rules.map(r => ({ ...r, regex: "compiled by Next" })) } };
    expect(() => assertBuiltBackend(manifest, rules)).not.toThrow();
    for (const invalid of [null, {}, { rewrites: [] }, { rewrites: { beforeFiles: rules.slice(1) } }]) {
      expect(() => assertBuiltBackend(invalid, rules)).toThrow();
    }
    expect(() => assertBuiltBackend(manifest, backendRewrites("http://127.0.0.1:8000"))).toThrow("重新构建");
  });
});
