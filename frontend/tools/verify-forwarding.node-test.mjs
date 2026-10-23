import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { request } from "node:http";
import { test } from "node:test";
import { CONTROLLED_ENDPOINTS, MIB, SYNTHETIC_AUTH_HEADERS, startControlledBackend, until, verifyForwarding } from "./verify-forwarding.mjs";

const digest = (body) => createHash("sha256").update(body).digest("hex");

test("current resources include cookie-scoped workspace bootstrap and usage without account routes; transport evidence contains only booleans", async () => {
  const identityAndUsage = [
    ["GET", "/workspace/session"], ["POST", "/workspace/session"], ["GET", "/usage"],
  ];
  assert.equal(CONTROLLED_ENDPOINTS.length, 35);
  assert.equal(new Set(CONTROLLED_ENDPOINTS.map(([method, path]) => `${method} ${path}`)).size, 35);
  assert.equal(CONTROLLED_ENDPOINTS.some(([, path]) => path.startsWith("/auth/")), false);
  assert.ok(CONTROLLED_ENDPOINTS.some(([method, path]) => method === "POST" && path.endsWith("/turns")));
  assert.ok(CONTROLLED_ENDPOINTS.some(([method, path]) => method === "GET" && path === "/chat/cities"));
  for (const method of ["GET", "POST"]) assert.ok(CONTROLLED_ENDPOINTS.some(([verb, path]) => verb === method && path.endsWith("/places")));
  for (const endpoint of identityAndUsage) assert.ok(CONTROLLED_ENDPOINTS.some(([method, path]) => method === endpoint[0] && path === endpoint[1]));
  const backend = await startControlledBackend({ port: 0 });
  try {
    for (const [method, path] of identityAndUsage) {
      const body = method === "POST" ? "{}" : undefined;
      const response = await fetch(`http://127.0.0.1:${backend.port}${path}`, { method, body, headers: SYNTHETIC_AUTH_HEADERS });
      assert.equal(response.status, 200);
      const result = await response.json();
      assert.equal(result.method, method);
      assert.equal(result.path, path);
      assert.equal(result.sha256, digest(body ?? ""));
      assert.deepEqual(result.authentication_headers, { cookie_matches: true, origin_matches: true, csrf_matches: true });
    }
    const unexpected = "private-value-must-not-be-recorded";
    const response = await fetch(`http://127.0.0.1:${backend.port}/workspace/session`, {
      headers: { cookie: unexpected, origin: "http://different.invalid", "x-csrf-token": unexpected },
    });
    assert.deepEqual((await response.json()).authentication_headers, { cookie_matches: false, origin_matches: false, csrf_matches: false });
    const receipt = JSON.stringify(backend.records);
    for (const value of [...Object.values(SYNTHETIC_AUTH_HEADERS), unexpected]) assert.equal(receipt.includes(value), false);
    for (const record of backend.records) {
      assert.deepEqual(Object.keys(record.authentication_headers).sort(), ["cookie_matches", "csrf_matches", "origin_matches"]);
      assert.ok(Object.values(record.authentication_headers).every(value => typeof value === "boolean"));
      assert.equal(Object.hasOwn(record, "headers"), false);
    }
  } finally { await backend.close(); }
});

test("national knowledge has two exact readonly resource paths with method/body/query/auth preserved", async () => {
  const backend = await startControlledBackend({ port: 0 });
  try {
    const endpoints = CONTROLLED_ENDPOINTS.filter(([, path]) => path === "/knowledge/catalog-v2" || path === "/knowledge/search-v2");
    assert.deepEqual(endpoints, [["GET", "/knowledge/catalog-v2"], ["POST", "/knowledge/search-v2"]]);
    for (const [method, path] of endpoints) {
      const body = method === "POST" ? JSON.stringify({ city_id: "110000", venue_id: "controlled-museum", question: "预约事项", limit: 5, visit_date: null }) : undefined;
      const response = await fetch(`http://127.0.0.1:${backend.port}${path}?probe_echo=1`, { method, body, headers: SYNTHETIC_AUTH_HEADERS });
      const record = await response.json();
      assert.equal(response.status, 200); assert.equal(record.path, path); assert.equal(record.method, method);
      assert.equal(record.sha256, digest(body ?? "")); assert.deepEqual(record.query, [["probe_echo", "1"]]);
      assert.deepEqual(record.authentication_headers, { cookie_matches: true, origin_matches: true, csrf_matches: true });
    }
    assert.equal(backend.records.length, 2);
  } finally { await backend.close(); }
});

test("assistant and dialogue readback transport retains version header and auth without recording arbitrary header values", async () => {
  const backend = await startControlledBackend({ port: 0 });
  const origin = `http://127.0.0.1:${backend.port}`;
  try {
    const endpoints = CONTROLLED_ENDPOINTS.filter(([, path]) => /\/(assistant-turns|assistant-state|assistant-preferences|dialogue-action)$/.test(path));
    assert.equal(endpoints.length, 5);
    for (const [method, path] of endpoints) {
      const body = method === "POST" ? JSON.stringify({ operation_id: "controlled", expected_revision: 2, text: "核对当前行程" }) : undefined;
      const response = await fetch(`${origin}${path}`, {
        method, body, headers: { ...SYNTHETIC_AUTH_HEADERS, "content-type": "application/json", "x-trippilot-surface": "dialogue-v2" },
      });
      assert.equal(response.status, 200);
      const result = await response.json();
      assert.equal(result.path, path);
      assert.equal(result.method, method);
      assert.equal(result.sha256, digest(body ?? ""));
      assert.equal(result.dialogue_surface, "dialogue-v2");
      assert.deepEqual(result.authentication_headers, { cookie_matches: true, origin_matches: true, csrf_matches: true });
    }
    const postPath = endpoints.find(([method, path]) => method === "POST" && path.endsWith("/assistant-turns"))[1];
    for (const [value, expected] of [["dialogue-v1", "dialogue-v1"], ["private-unrecognized-surface", "invalid"], ["dialogue-v2,dialogue-v2", "invalid"]]) {
      const response = await fetch(`${origin}${postPath}`, { method: "POST", headers: { ...SYNTHETIC_AUTH_HEADERS, "x-trippilot-surface": value }, body: "{}" });
      assert.equal((await response.json()).dialogue_surface, expected);
    }
    const serialized = JSON.stringify(backend.records);
    assert.equal(serialized.includes("private-unrecognized-surface"), false);
    assert.equal(serialized.includes("核对当前行程"), false);
    for (const secret of Object.values(SYNTHETIC_AUTH_HEADERS)) assert.equal(serialized.includes(secret), false);
  } finally { await backend.close(); }
});

test("cancelled workspace bootstrap sends one empty command with Origin/CSRF/cookie preserved and never retries", async () => {
  const backend = await startControlledBackend({ port: 0 });
  try {
    const req = request(`http://127.0.0.1:${backend.port}/workspace/session?probe_wait_ms=40000`, {
      method: "POST", headers: { ...SYNTHETIC_AUTH_HEADERS, "content-type": "application/json" },
    });
    req.on("error", () => {});
    req.end("{}");
    const record = await until(() => backend.records.find(row => row.completed_upload), "workspace command did not reach controlled backend");
    req.destroy();
    await until(() => record.response_close_count === 1, "backend did not observe workspace cancellation");
    assert.equal(record.response_finished, false);
    assert.equal(record.sha256, digest("{}"));
    assert.equal(record.bytes, 2);
    assert.deepEqual(record.authentication_headers, { cookie_matches: true, origin_matches: true, csrf_matches: true });
    assert.deepEqual(backend.records.map(row => [row.method, row.path]), [["POST", "/workspace/session"]]);
  } finally { await backend.close(); }
});

test("cancelled dialogue receipt read observes backend close once and does not send an execution request", async () => {
  const backend = await startControlledBackend({ port: 0 });
  try {
    const path = CONTROLLED_ENDPOINTS.find(([method, path]) => method === "GET" && path.endsWith("/dialogue-action"))[1];
    const req = request(`http://127.0.0.1:${backend.port}${path}?probe_wait_ms=40000`, { method: "GET", headers: SYNTHETIC_AUTH_HEADERS });
    req.on("error", () => {});
    req.end();
    const record = await until(() => backend.records.find(row => row.completed_upload), "read did not reach controlled backend");
    req.destroy();
    await until(() => record.response_close_count === 1, "backend did not observe read cancellation");
    assert.equal(record.response_finished, false);
    assert.deepEqual(record.authentication_headers, { cookie_matches: true, origin_matches: true, csrf_matches: true });
    assert.deepEqual(backend.records.map(row => [row.method, row.path]), [["GET", path]]);
  } finally { await backend.close(); }
});

test("chat-2 city/read/command paths retain method, query, body and safe auth metadata", async () => {
  const backend = await startControlledBackend({ port: 0 });
  const origin = `http://127.0.0.1:${backend.port}`;
  try {
    const endpoints = CONTROLLED_ENDPOINTS.filter(([, path]) => path === "/chat/cities" || path.endsWith("/places"));
    for (const [method, path] of endpoints) {
      const body = method === "POST" ? JSON.stringify({ action: "search", keyword: "受控地点" }) : undefined;
      const response = await fetch(`${origin}${path}?query=哈尔滨&tag=甲&tag=乙`, {
        method, body, headers: { ...SYNTHETIC_AUTH_HEADERS, "content-type": "application/json" },
      });
      const record = await response.json();
      assert.equal(record.path, path);
      assert.equal(record.method, method);
      assert.equal(record.sha256, digest(body ?? ""));
      assert.deepEqual(record.query, [["query", "哈尔滨"], ["tag", "甲"], ["tag", "乙"]]);
      assert.deepEqual(record.authentication_headers, { cookie_matches: true, origin_matches: true, csrf_matches: true });
    }
    assert.equal(backend.records.length, 3);
  } finally { await backend.close(); }
});

test("controlled backend records exact bytes/hash and enforces media boundaries without retaining bodies", async () => {
  const backend = await startControlledBackend({ port: 0 });
  const origin = `http://127.0.0.1:${backend.port}`;
  try {
    for (const [path, max] of [["landmark", 5 * MIB], ["video-frames", 20 * MIB]]) {
      const body = Buffer.alloc(max, 0x3a);
      const res = await fetch(`${origin}/media/${path}`, { method: "POST", body });
      assert.equal(res.status, 200);
      const result = await res.json();
      assert.equal(result.bytes, max); assert.equal(result.sha256, digest(body));
      assert.equal(Object.hasOwn(backend.records.at(-1), "body"), false);
      const rejected = await fetch(`${origin}/media/${path}`, { method: "POST", body: Buffer.alloc(max + 1) });
      assert.equal(rejected.status, 413);
      assert.equal((await rejected.json()).detail.code, "controlled_upload_size");
      assert.equal(backend.records.at(-1).bytes, max + 1);
    }
  } finally { await backend.close(); }
});

test("controlled errors, redirects, empty knowledge result and 5 MiB response are explicit", async () => {
  const backend = await startControlledBackend({ port: 0 });
  const origin = `http://127.0.0.1:${backend.port}`;
  try {
    for (const status of [422, 429, 503]) {
      const res = await fetch(`${origin}/places/search?probe_status=${status}`);
      assert.equal(res.status, status); assert.equal((await res.json()).detail.code, `controlled_${status}`);
    }
    const disabled = await fetch(`${origin}/knowledge/answer`, { method: "POST" });
    assert.equal(disabled.status, 503); assert.equal((await disabled.json()).detail.code, "knowledge_answer_disabled");
    const redirect = await fetch(`${origin}/places/search?probe_redirect=1`, { redirect: "manual" });
    assert.equal(redirect.status, 302); assert.equal(redirect.headers.get("location"), "/controlled-redirect-target");
    const question = { venue_id: "natural", question: "受控寻址检查", limit: 3 };
    const knowledge = await fetch(`${origin}/knowledge/search`, { method: "POST", body: JSON.stringify(question) });
    const result = await knowledge.json();
    assert.deepEqual(result.request, question); assert.deepEqual(result.matches, []); assert.equal(result.status, "no_match");
    const large = await fetch(`${origin}/media/video-frames?probe_response=large`);
    const bytes = Buffer.from(await large.arrayBuffer());
    assert.equal(bytes.length, 5 * MIB); assert.equal(digest(bytes), digest(Buffer.alloc(5 * MIB, 0x6b)));
  } finally { await backend.close(); }
});

for (const complete of [false, true]) {
  test(`controlled cancellation metadata distinguishes ${complete ? "after" : "during"} upload`, async () => {
    const backend = await startControlledBackend({ port: 0 });
    try {
      const req = request(`http://127.0.0.1:${backend.port}/media/video-frames?probe_wait_ms=40000`, {
        method: "POST", headers: { "content-length": String(MIB) },
      });
      req.on("error", () => {});
      if (complete) req.end(Buffer.alloc(MIB));
      else req.write(Buffer.alloc(1024));
      const record = await until(() => backend.records.find((r) => complete ? r.completed_upload : r.bytes > 0), "stub did not receive upload");
      req.destroy();
      await until(() => record.response_close_count === 1, "stub did not observe disconnect");
      assert.equal(record.completed_upload, complete);
      assert.equal(record.response_finished, false);
      assert.equal(backend.records.length, 1);
    } finally { await backend.close(); }
  });
}

test("verifier refuses arbitrary frontend origins before sending any request", async () => {
  const backend = await startControlledBackend({ port: 0 });
  try {
    await assert.rejects(verifyForwarding(backend, "http://127.0.0.1:8000"));
    assert.equal(backend.records.length, 0);
  } finally { await backend.close(); }
});
