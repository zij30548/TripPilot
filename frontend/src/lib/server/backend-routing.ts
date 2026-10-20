// Next configuration only: never import the trusted upstream into client code.
import { API_ENDPOINTS, AMAP_SERVICE_PATH } from "../api-endpoints";

export type BackendRewrite = { source: string; destination: string };

export const FORWARDING_TIMEOUT_MS = 75_000;
export const FORWARDING_BODY_BYTES = 21 * 1024 * 1024;

export function backendOrigin(value: string | undefined, development: boolean): string {
  if (value === undefined && development) return "http://127.0.0.1:8000";
  const message = "请配置服务端 TRIPPILOT_BACKEND_ORIGIN：使用规范 HTTP(S) origin，不含凭证、路径、查询或片段；生产环境不能省略。";
  if (!value || /[\s\\]/.test(value)) throw new Error(message);
  try {
    const url = new URL(value);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password ||
      url.pathname !== "/" || url.search || url.hash ||
      (value !== url.origin && value !== `${url.origin}/`)) throw new Error(message);
    return url.origin;
  } catch {
    // Never echo an invalid configuration value; it may contain credentials.
    throw new Error(message);
  }
}

export function backendRewrites(origin: string): BackendRewrite[] {
  const target = backendOrigin(origin, false);
  return [
    ...Object.values(API_ENDPOINTS).map(source => ({ source, destination: `${target}${source.slice("/api".length)}` })),
    { source: "/api/health", destination: `${target}/health` },
    { source: "/api/saved-trips/:id([0-9a-f]{32})", destination: `${target}/saved-trips/:id` },
    ...["", "/turns", "/cancel", "/confirm", "/delete"].map(suffix => ({
      source: `/api/chat/conversations/:id([0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})${suffix}`,
      destination: `${target}/chat/conversations/:id${suffix}`,
    })),
    { source: `${AMAP_SERVICE_PATH}/v3/config/district`, destination: `${target}/_AMapService/v3/config/district` },
  ];
}

function record(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

export function assertBuiltBackend(manifest: unknown, expected: BackendRewrite[]): void {
  const rules = record(manifest) && record(manifest.rewrites) ? manifest.rewrites.beforeFiles : null;
  if (!Array.isArray(rules) || rules.length !== expected.length ||
    !expected.every((rule, index) => record(rules[index]) &&
      rules[index].source === rule.source && rules[index].destination === rule.destination)) {
    throw new Error("生产转发清单与当前配置不一致。请使用相同 TRIPPILOT_BACKEND_ORIGIN 重新构建并启动，不能仅在启动时更换后端。合并发布代码与构建产物。");
  }
}
