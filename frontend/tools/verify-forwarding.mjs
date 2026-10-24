/**
 * Release-1C real-TCP addressing checks. Every backend response is CONTROLLED.
 * No FastAPI, AMap, model, weather, database or public network is used.
 *
 * Build/start Next separately with TRIPPILOT_BACKEND_ORIGIN=http://127.0.0.1:18081:
 *   node tools/verify-forwarding.mjs verify [--output /absolute/receipt.json]
 * For browser-only controlled smoke checks:
 *   node tools/verify-forwarding.mjs serve [--output /absolute/receipt.json]
 * Only the controlled HTTP server created here is closed by this tool.
 */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { writeFile } from "node:fs/promises";
import { createServer, request as httpRequest } from "node:http";
import { pathToFileURL } from "node:url";

export const MIB = 1024 * 1024;
export const BACKEND_PORT = 18081;
export const FRONTEND_ORIGIN = "http://127.0.0.1:3101";
const marker = "release-1c-controlled-only";
const CHAT_ID = "12345678-1234-4234-8234-123456789abc";
const hash = (value) => createHash("sha256").update(value).digest("hex");
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
// Synthetic transport fixtures only. The server records equality booleans,
// never Cookie, Origin or CSRF header values from a request.
export const SYNTHETIC_AUTH_HEADERS = Object.freeze({
  cookie: "forwarding_probe=controlled-not-a-session",
  origin: FRONTEND_ORIGIN,
  "x-csrf-token": "controlled-not-a-csrf-token",
});
export const CONTROLLED_ENDPOINTS = [
  ["GET", "/workspace/session"], ["POST", "/workspace/session"], ["GET", "/usage"],
  ["GET", "/places/search"], ["POST", "/places/candidates"],
  ["POST", "/trips/schedule-preview"], ["POST", "/weather/forecast"],
  ["POST", "/trips/schedule-explanation"], ["POST", "/trips/adjustment-intent"],
  ["POST", "/trips/itinerary-query"], ["POST", "/media/landmark"],
  ["POST", "/media/video-frames"], ["POST", "/saved-trips"],
  ["POST", "/knowledge/search"], ["POST", "/knowledge/answer"],
  ["GET", "/knowledge/catalog-v2"], ["POST", "/knowledge/search-v2"],
  ["POST", "/routes/walking"], ["POST", "/routes/transit"], ["POST", "/trips/plan"],
  ["GET", "/chat/conversations"], ["POST", "/chat/conversations"],
  ["GET", "/chat/cities"],
  ["GET", `/chat/conversations/${CHAT_ID}`],
  ...["turns", "cancel", "confirm", "delete"].map(action => ["POST", `/chat/conversations/${CHAT_ID}/${action}`]),
  ["GET", `/chat/conversations/${CHAT_ID}/places`], ["POST", `/chat/conversations/${CHAT_ID}/places`],
  ["POST", `/chat/conversations/${CHAT_ID}/assistant-turns`],
  ["GET", `/chat/conversations/${CHAT_ID}/assistant-state`],
  ["GET", `/chat/conversations/${CHAT_ID}/assistant-preferences`], ["POST", `/chat/conversations/${CHAT_ID}/assistant-preferences`],
  ["GET", `/chat/conversations/${CHAT_ID}/dialogue-action`],
];

/** Bounded metadata only: no request/response bodies or credentials are retained. */
export async function startControlledBackend({ port = BACKEND_PORT } = {}) {
  const records = [];
  const sockets = new Set();
  const server = createServer((req, res) => {
    const url = new URL(req.url, "http://controlled.invalid");
    const record = {
      id: req.headers["x-forwarding-probe"] ?? url.searchParams.get("probe_id") ?? "browser",
      path: url.pathname, method: req.method, query: [...url.searchParams],
      bytes: 0, sha256: null, completed_upload: false, response_finished: false,
      response_close_count: 0, upload_aborted: false,
      dialogue_surface: req.headers["x-trippilot-surface"] === undefined ? "absent"
        : ["dialogue-v1", "dialogue-v2", "dialogue-v3"].includes(req.headers["x-trippilot-surface"]) ? req.headers["x-trippilot-surface"] : "invalid",
      authentication_headers: {
        cookie_matches: req.headers.cookie === SYNTHETIC_AUTH_HEADERS.cookie,
        origin_matches: req.headers.origin === SYNTHETIC_AUTH_HEADERS.origin,
        csrf_matches: req.headers["x-csrf-token"] === SYNTHETIC_AUTH_HEADERS["x-csrf-token"],
      },
    };
    records.push(record);
    const digest = createHash("sha256");
    const jsonParts = [];
    let responseTimer;
    req.on("data", (part) => {
      record.bytes += part.length;
      digest.update(part);
      if (record.bytes <= 64 * 1024) jsonParts.push(part);
    });
    req.on("aborted", () => { record.upload_aborted = true; });
    req.on("error", () => {});
    res.on("finish", () => { record.response_finished = true; });
    res.on("close", () => {
      record.response_close_count += 1;
      clearTimeout(responseTimer);
    });
    req.on("end", () => {
      record.completed_upload = true;
      record.sha256 = digest.digest("hex");
      if (res.destroyed) return;
      res.setHeader("x-controlled-forwarding", marker);
      res.setHeader("cache-control", "no-store");
      res.setHeader("content-type", "application/json; charset=utf-8");
      const sendJson = (status, value) => {
        res.statusCode = status;
        res.end(JSON.stringify(value));
      };
      const max = url.pathname === "/media/landmark" ? 5 * MIB
        : url.pathname === "/media/video-frames" ? 20 * MIB : Infinity;
      if (record.bytes > max) return sendJson(413, { detail: { code: "controlled_upload_size" } });
      if (url.searchParams.get("probe_redirect") === "1") {
        res.statusCode = 302;
        res.setHeader("location", "/controlled-redirect-target");
        res.end("controlled redirect");
        return;
      }
      if (url.searchParams.get("probe_response") === "large") {
        const body = Buffer.alloc(5 * MIB, 0x6b);
        res.setHeader("content-type", "application/octet-stream");
        res.setHeader("content-length", String(body.length));
        res.end(body);
        return;
      }
      const status = Number(url.searchParams.get("probe_status") ?? 200);
      if ([422, 429, 503].includes(status)) return sendJson(status, { detail: { code: `controlled_${status}` } });
      if (url.pathname === "/knowledge/answer") {
        return sendJson(503, { detail: { code: "knowledge_answer_disabled", message: "CONTROLLED: AI问答暂未开放" } });
      }
      // Browser smoke has an explicitly controlled empty result, never real corpus evidence.
      if (url.pathname === "/knowledge/search" && url.searchParams.get("probe_echo") !== "1") {
        let input;
        try { input = JSON.parse(Buffer.concat(jsonParts).toString()); } catch { return sendJson(422, { detail: { code: "controlled_bad_json" } }); }
        const names = { museum_people: "上海博物馆人民广场馆", museum_east: "上海博物馆东馆", natural: "上海自然博物馆" };
        if (!names[input.venue_id]) return sendJson(422, { detail: { code: "controlled_bad_venue" } });
        return sendJson(200, {
          schema_version: "knowledge-search-v1", corpus_version: "2026-10-14.1", corpus_hash: "0".repeat(64),
          document_count: 6, chunk_count: 6, request: input,
          venue: { id: input.venue_id, name: names[input.venue_id], aliases: ["受控寻址验证，非真实资料检索"] },
          status: "no_match", matches: [],
        });
      }
      const finish = () => sendJson(200, { controlled: marker, ...record });
      const wait = Number(url.searchParams.get("probe_wait_ms") ?? 0);
      if (wait > 0 && wait <= 40_000) responseTimer = setTimeout(finish, wait);
      else finish();
    });
  });
  server.on("connection", (socket) => { sockets.add(socket); socket.on("close", () => sockets.delete(socket)); });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, "127.0.0.1", resolve);
  });
  return {
    records, port: server.address().port,
    async close() {
      for (const socket of sockets) socket.destroy();
      await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
    },
  };
}

export async function until(predicate, message, timeout = 5000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const result = predicate();
    if (result) return result;
    await sleep(20);
  }
  throw new Error(message);
}

/** Observe backend disconnect, not merely the browser/client AbortError. */
async function cancellationProbe(origin, backend, phase, media) {
  const id = `cancel-${media}-${phase}`;
  const maxBytes = media === "landmark" ? 5 * MIB : 20 * MIB;
  const req = httpRequest(`${origin}/api/media/${media}?probe_id=${id}&probe_wait_ms=40000`, {
    method: "POST", headers: { ...SYNTHETIC_AUTH_HEADERS, "content-type": "application/octet-stream", "content-length": String(maxBytes), "x-forwarding-probe": id },
  });
  req.on("error", () => {});
  req.on("response", (res) => res.resume());
  // The upload case deliberately never completes; completed case sends all bytes first.
  const chunk = Buffer.alloc(MIB, 0x37);
  if (phase === "upload") req.write(chunk);
  else req.end(Buffer.alloc(maxBytes, 0x37));
  const record = await until(() => backend.records.find((x) => x.id === id && (phase === "upload" ? x.bytes > 0 : x.completed_upload)), `${id}: request did not reach controlled backend`, 10000);
  req.destroy();
  await until(() => record.response_close_count === 1, `${id}: backend connection did not close on client cancellation`, 5000);
  assert.equal(record.response_finished, false);
  assert.deepEqual(record.authentication_headers, { cookie_matches: true, origin_matches: true, csrf_matches: true });
  assert.equal(backend.records.filter((x) => x.id === id).length, 1, `${id}: duplicate upstream dispatch`);
  if (phase === "upload") assert.equal(record.completed_upload, false);
  return { id, observed_backend_close_count: record.response_close_count, completed_upload: record.completed_upload, received_bytes: record.bytes };
}

export async function verifyForwarding(backend, origin = FRONTEND_ORIGIN) {
  // Deliberately fixed, non-default loopback ports; never probe the user's port 8000.
  assert.equal(origin, FRONTEND_ORIGIN);
  assert.equal(backend.port, BACKEND_PORT);
  const checks = [];
  const probe = async (id, path, { method = "GET", body, status = 200, headers = {} } = {}) => {
    const separator = path.includes("?") ? "&" : "?";
    const res = await fetch(`${origin}${path}${separator}probe_id=${id}`, {
      method, body, redirect: "manual", cache: "no-store",
      headers: { ...SYNTHETIC_AUTH_HEADERS, "content-type": body instanceof Uint8Array ? "application/octet-stream" : "application/json", "x-forwarding-probe": id, ...headers },
    });
    assert.equal(res.status, status, id);
    assert.equal(res.headers.get("x-controlled-forwarding"), marker, `${id}: wrong/uncontrolled backend`);
    assert.match(res.headers.get("cache-control") ?? "", /no-store/, `${id}: no-store missing`);
    const record = backend.records.findLast((entry) => entry.id === id);
    assert.deepEqual(record.authentication_headers, { cookie_matches: true, origin_matches: true, csrf_matches: true }, `${id}: authentication headers changed`);
    return res;
  };
  for (const [index, [method, path]] of CONTROLLED_ENDPOINTS.entries()) {
    const id = `endpoint-${index + 1}`;
    const body = method === "POST" ? JSON.stringify(path === "/workspace/session" ? {} : { controlled: true }) : undefined;
    const response = await probe(id, `/api${path}${path === "/knowledge/search" ? "?probe_echo=1" : ""}`, {
      method, body,
      status: path === "/knowledge/answer" ? 503 : 200,
      headers: path.endsWith("/assistant-turns") ? { "x-trippilot-surface": "dialogue-v2" } : {},
    });
    const result = await response.json();
    const record = backend.records.find((x) => x.id === id);
    assert.equal(record.path, path);
    assert.equal(record.method, method);
    if (method === "POST") assert.equal(record.sha256, hash(body));
    if (path === "/knowledge/answer") assert.equal(result.detail.code, "knowledge_answer_disabled");
    if (path.endsWith("/assistant-turns")) assert.equal(record.dialogue_surface, "dialogue-v2");
    checks.push({ id, method, path, status: response.status, authentication_headers_verified: true });
  }
  for (const [id, path] of [
    ["health", "/api/health"], ["saved-list", "/api/saved-trips"],
    ["saved-detail", `/api/saved-trips/${"a".repeat(32)}`],
    ["map", "/_AMapService/v3/config/district?keywords=上海&subdistrict=0"],
  ]) {
    const response = await probe(id, path);
    await response.arrayBuffer();
    checks.push({ id, status: response.status });
  }
  const query = await probe("query", "/api/places/search?keywords=静安寺&tag=甲&tag=乙&url=http%3A%2F%2F127.0.0.1%3A1&host=attacker.invalid");
  const queried = await query.json();
  assert.deepEqual(queried.query.filter(([key]) => key === "tag"), [["tag", "甲"], ["tag", "乙"]]);
  assert.equal(queried.query.find(([key]) => key === "keywords")[1], "静安寺");
  checks.push({ id: "query", duplicate_and_chinese_preserved: true, query_cannot_retarget: true });
  for (let index = 0; index < 2; index += 1) {
    const response = await probe("same-url-no-cache", "/api/places/search?keywords=controlled");
    await response.arrayBuffer();
  }
  assert.equal(backend.records.filter((record) => record.id === "same-url-no-cache").length, 2);
  checks.push({ id: "same-url-no-cache", identical_gets: 2, backend_dispatches: 2 });
  for (const status of [422, 429, 503]) {
    const res = await probe(`status-${status}`, `/api/trips/schedule-preview?probe_status=${status}`, { method: "POST", body: "{}", status });
    assert.equal((await res.json()).detail.code, `controlled_${status}`);
    checks.push({ id: `status-${status}`, status });
  }
  const redirect = await probe("redirect", "/api/places/search?probe_redirect=1", { status: 302 });
  assert.equal(redirect.headers.get("location"), "/controlled-redirect-target");
  await redirect.arrayBuffer();
  assert.equal(backend.records.some((x) => x.path === "/controlled-redirect-target"), false);
  checks.push({ id: "redirect", status: 302, server_followed: false });
  const before = backend.records.length;
  const rejectedPaths = ["/api/not-allowed", "/api/auth/session", "/api/auth/login", "/api/auth/register", "/api/auth/logout",
    "/api/workspace/session/reset", "/_AMapService/v3/place/text", `/api/saved-trips/${"g".repeat(32)}`,
    "/api/chat/conversations/not-a-uuid/places", `/api/chat/conversations/${CHAT_ID}/places/unapproved`, "/api/chat/cities/unapproved",
    "/api/chat/conversations/not-a-uuid/dialogue-action", `/api/chat/conversations/${CHAT_ID}/dialogue-action/execute`,
    `/api/chat/conversations/${CHAT_ID}/dialogue-actions`];
  for (const path of rejectedPaths) {
    const res = await fetch(`${origin}${path}`, { redirect: "manual" });
    assert.equal(res.status, 404, `unlisted path ${path}`);
    await res.arrayBuffer();
  }
  for (const action of ["login", "register", "logout"]) {
    const res = await fetch(`${origin}/api/auth/${action}`, { method: "POST", body: "{}", redirect: "manual" });
    assert.equal(res.status, 404, `removed account action ${action}`);
    await res.arrayBuffer();
  }
  assert.equal(backend.records.length, before, "unlisted routes reached backend");
  checks.push({ id: "allowlist", rejected_paths: rejectedPaths.length, removed_account_posts: 3 });
  for (const [id, path, size] of [
    ["photo-limit", "/api/media/landmark", 5 * MIB],
    ["video-limit", "/api/media/video-frames", 20 * MIB],
  ]) {
    const body = Buffer.alloc(size, 0xa7);
    const res = await probe(id, path, { method: "POST", body });
    const result = await res.json();
    assert.equal(result.bytes, size); assert.equal(result.sha256, hash(body));
    checks.push({ id, bytes: size, sha256: hash(body), status: res.status });
    const over = Buffer.alloc(size + 1, 0x19);
    const rejected = await probe(`${id}-oversize`, path, { method: "POST", body: over, status: 413 });
    assert.equal((await rejected.json()).detail.code, "controlled_upload_size");
    const observed = backend.records.find((x) => x.id === `${id}-oversize`);
    assert.equal(observed.bytes, over.length); assert.equal(observed.sha256, hash(over));
    checks.push({ id: `${id}-oversize`, bytes: over.length, status: 413, backend_decided: true });
  }
  const large = await probe("large-response", "/api/media/video-frames?probe_response=large", { method: "POST", body: "{}" });
  const actual = Buffer.from(await large.arrayBuffer());
  assert.equal(actual.length, 5 * MIB); assert.equal(hash(actual), hash(Buffer.alloc(5 * MIB, 0x6b)));
  checks.push({ id: "large-response", bytes: actual.length, sha256: hash(actual) });
  const started = performance.now();
  const slow = await probe("long-processing", "/api/media/video-frames?probe_wait_ms=31000", { method: "POST", body: "{}" });
  await slow.arrayBuffer();
  const elapsed = Math.round(performance.now() - started);
  assert.ok(elapsed >= 30_000 && elapsed < 60_000, `controlled wait ${elapsed}ms`);
  checks.push({ id: "long-processing", wait_target_ms: 31000, elapsed_ms: elapsed, status: 200 });
  for (const media of ["landmark", "video-frames"]) {
    for (const phase of ["upload", "after-upload"]) checks.push(await cancellationProbe(origin, backend, phase, media));
  }
  return {
    kind: "CONTROLLED real-TCP production-forwarding verification; NOT real AMap/model/business acceptance",
    created_at: new Date().toISOString(), frontend_origin: origin, backend_origin: `http://127.0.0.1:${backend.port}`,
    outcome: "passed", checks, backend_dispatch_count: backend.records.length,
    external_business_calls: 0, model_calls: 0, database_writes: 0,
    records: backend.records,
  };
}

async function main() {
  const args = process.argv.slice(2);
  const mode = args.shift();
  const outputIndex = args.indexOf("--output");
  const output = outputIndex >= 0 ? args[outputIndex + 1] : undefined;
  assert.ok(["serve", "verify"].includes(mode), "Use serve or verify [--output /absolute/receipt.json]");
  assert.ok(!output || output.startsWith("/"), "Evidence output must be an absolute path");
  const backend = await startControlledBackend();
  if (mode === "serve") {
    process.stdout.write(`${marker} listening on http://127.0.0.1:${backend.port}; no business APIs\n`);
    let stopping = false;
    const stop = async () => {
      if (stopping) return; stopping = true;
      await backend.close();
      if (output) await writeFile(output, `${JSON.stringify({ kind: marker, records: backend.records, external_business_calls: 0 }, null, 2)}\n`, { flag: "wx", mode: 0o600 });
    };
    process.once("SIGINT", stop); process.once("SIGTERM", stop);
    return;
  }
  try {
    const receipt = await verifyForwarding(backend);
    if (output) await writeFile(output, `${JSON.stringify(receipt, null, 2)}\n`, { flag: "wx", mode: 0o600 });
    process.stdout.write(`${JSON.stringify(receipt, null, 2)}\n`);
  } catch (error) {
    const receipt = { kind: marker, outcome: "failed", error: error instanceof Error ? error.message : "Unknown controlled verification failure", records: backend.records, external_business_calls: 0 };
    if (output) await writeFile(output, `${JSON.stringify(receipt, null, 2)}\n`, { flag: "wx", mode: 0o600 });
    throw error;
  } finally { await backend.close(); }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => { process.stderr.write(`${error.message}\n`); process.exitCode = 1; });
}
