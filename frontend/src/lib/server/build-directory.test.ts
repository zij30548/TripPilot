import { describe, expect, it, vi } from "vitest";
import { buildDirectory } from "./build-directory";

describe("trusted isolated build directory", () => {
  it("keeps the normal build unchanged and accepts only bounded local names", () => {
    expect(buildDirectory(undefined)).toBe(".next");
    expect(buildDirectory(".next")).toBe(".next");
    expect(buildDirectory(".next-stage4-20261023")).toBe(".next-stage4-20261023");
  });

  it("rejects paths, whitespace, arbitrary output roots and unbounded values", () => {
    for (const value of ["", " ", "../.next", "/tmp/.next", ".next-x/../.next", ".next-x\\y", ".next-X", ".next-", ".next-" + "x".repeat(49), "build"]) {
      expect(() => buildDirectory(value)).toThrow("TRIPPILOT_NEXT_DIST_DIR");
    }
  });

  it("verifies the selected production artifact manifest, not the running default", async () => {
    const read = vi.fn((file: unknown) => { void file; return JSON.stringify({ rewrites: { beforeFiles: [] } }); });
    vi.doMock("node:fs", () => ({ readFileSync: read, default: { readFileSync: read } }));
    vi.doMock("next/constants", () => ({ PHASE_DEVELOPMENT_SERVER: "phase-development-server", PHASE_PRODUCTION_SERVER: "phase-production-server" }));
    vi.doMock("./backend-routing", () => ({
      backendOrigin: () => "http://127.0.0.1:8812", backendRewrites: () => [],
      assertBuiltBackend: vi.fn(), FORWARDING_BODY_BYTES: 30_000_000, FORWARDING_TIMEOUT_MS: 90_000,
    }));
    vi.stubEnv("TRIPPILOT_NEXT_DIST_DIR", ".next-stage4");
    try {
      vi.resetModules();
      const config = (await import("../../../next.config")).default("phase-production-server");
      expect(config.distDir).toBe(".next-stage4");
      expect(read.mock.calls[0][0]).toContain(".next-stage4/routes-manifest.json");
    } finally {
      vi.unstubAllEnvs();
      vi.doUnmock("node:fs");
      vi.doUnmock("next/constants");
      vi.doUnmock("./backend-routing");
      vi.resetModules();
    }
  });
});
