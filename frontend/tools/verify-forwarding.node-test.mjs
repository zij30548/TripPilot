import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { request } from "node:http";
import { test } from "node:test";
import { MIB, startControlledBackend, until, verifyForwarding } from "./verify-forwarding.mjs";

const digest = (body) => createHash("sha256").update(body).digest("hex");

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
