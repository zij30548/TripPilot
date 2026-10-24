import type { NextConfig } from "next";
import { PHASE_DEVELOPMENT_SERVER, PHASE_PRODUCTION_SERVER } from "next/constants";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { assertBuiltBackend, backendOrigin, backendRewrites, FORWARDING_BODY_BYTES, FORWARDING_TIMEOUT_MS } from "./src/lib/server/backend-routing";
import { buildDirectory } from "./src/lib/server/build-directory";

export default function nextConfig(phase: string): NextConfig {
  const distDir = buildDirectory(process.env.TRIPPILOT_NEXT_DIST_DIR);
  const origin = backendOrigin(process.env.TRIPPILOT_BACKEND_ORIGIN, phase === PHASE_DEVELOPMENT_SERVER);
  const rules = backendRewrites(origin);
  if (phase === PHASE_PRODUCTION_SERVER) {
    let manifest: unknown;
    try {
      manifest = JSON.parse(readFileSync(join(__dirname, distDir, "routes-manifest.json"), "utf8"));
    } catch {
      throw new Error("生产转发清单缺失或损坏，请先使用 TRIPPILOT_BACKEND_ORIGIN 构建，再以相同配置启动。");
    }
    assertBuiltBackend(manifest, rules);
  }
  return {
    distDir,
    // This pinned Next version otherwise truncates cloned bodies at 10 MiB
    // and defaults external rewrites to 30s. Backend limits stay 5/20 MiB;
    // media deadlines remain 35/60s; only dialogue-v3 waits at most 105s.
    // No proxy.ts or body parsing is added; disconnects still propagate.
    experimental: {
      proxyTimeout: FORWARDING_TIMEOUT_MS,
      proxyClientMaxBodySize: FORWARDING_BODY_BYTES,
    },
    rewrites: () => ({ beforeFiles: rules, afterFiles: [], fallback: [] }),
  };
}
