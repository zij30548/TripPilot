import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type AuthModule = typeof import("./auth-session");
let auth: AuthModule;
let stop: (() => void) | undefined;
const csrf = "c".repeat(43);
const empty = () => ({ authenticated: false, user: null, csrf_token: csrf, expires_at: null, session_id: null });
const session = (owner = "workspace-a", identity = "a".repeat(32), expires = Date.now() + 60_000) => ({
  authenticated: true, user: { id: owner, username: "当前工作区" }, csrf_token: csrf,
  expires_at: new Date(expires).toISOString(), session_id: identity,
});
const response = (body: unknown, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
const deferred = <T,>() => { let resolve!: (value: T) => void; let reject!: (error: Error) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };

beforeEach(async () => {
  vi.resetModules();
  vi.stubGlobal("BroadcastChannel", undefined);
  auth = await import("./auth-session");
});
afterEach(() => { stop?.(); stop = undefined; vi.useRealTimers(); });

describe("isolated browser-workspace transport", () => {
  it("validates the exact public workspace shape and refuses credential or inconsistent fields", () => {
    const value = session(); expect(auth.parseAuthSession(value)).toEqual(value);
    expect(auth.parseAuthSession(empty())).toEqual(empty());
    for (const value of [
      { ...session(), owner_id: "injected" }, { ...session(), session_id: null },
      { ...session(), expires_at: "2026-10-18T12:00:00" }, { ...empty(), user: session().user },
      { ...empty(), csrf_token: "" }, { ...session(), user: { ...session().user, password: "not-accepted" } },
    ]) expect(() => auth.parseAuthSession(value)).toThrow("工作区状态响应无效");
  });

  it("bootstraps once with the pre-session CSRF, no credentials, and no business request", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(response(empty())).mockResolvedValueOnce(response(session()));
    vi.stubGlobal("fetch", fetcher); stop = auth.startAuthLifecycle(); await flush();
    expect(auth.authSnapshot().phase).toBe("authenticated");
    expect(fetcher.mock.calls.map(call => call[0])).toEqual(["/api/workspace/session", "/api/workspace/session"]);
    const request = fetcher.mock.calls[1][1];
    expect(request.method).toBe("POST"); expect(request.body).toBe("{}");
    expect(new Headers(request.headers).get("X-CSRF-Token")).toBe(csrf);
    expect(request.credentials).toBe("same-origin"); expect(request.cache).toBe("no-store");
  });

  it("does not bootstrap an obsolete check and ignores its late response/finally", async () => {
    const old = deferred<ReturnType<typeof response>>(), current = deferred<ReturnType<typeof response>>();
    const fetcher = vi.fn().mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise);
    vi.stubGlobal("fetch", fetcher); stop = auth.startAuthLifecycle(); const latest = auth.checkSession();
    expect(fetcher.mock.calls[0][1].signal.aborted).toBe(true);
    old.resolve(response(empty())); await flush();
    expect(fetcher).toHaveBeenCalledTimes(2); expect(auth.authSnapshot().verifying).toBe(true);
    current.resolve(response(session())); await latest;
    expect(auth.authSnapshot().phase).toBe("authenticated"); expect(auth.authSnapshot().verifying).toBe(false);
  });

  it("does not retry an uncertain bootstrap, and recovers by reading the existing workspace", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(response(empty())).mockRejectedValueOnce(new TypeError("controlled transport loss"))
      .mockResolvedValueOnce(response(session()));
    vi.stubGlobal("fetch", fetcher); stop = auth.startAuthLifecycle(); await flush();
    expect(auth.authSnapshot().phase).toBe("error"); expect(fetcher).toHaveBeenCalledTimes(2);
    await auth.checkSession(); expect(auth.authSnapshot().phase).toBe("authenticated");
    expect(fetcher.mock.calls.filter(call => call[1].method === "POST")).toHaveLength(1);
  });

  it.each([403, 429])("keeps the controlled bootstrap failure reason for HTTP %s without displaying its body", async status => {
    const fetcher = vi.fn().mockResolvedValueOnce(response(empty())).mockResolvedValueOnce(new Response("private upstream text", {
      status, headers: { "Retry-After": "12" },
    }));
    vi.stubGlobal("fetch", fetcher); stop = auth.startAuthLifecycle(); await flush();
    expect(auth.authSnapshot().phase).toBe("error");
    expect(auth.authSnapshot().message).toContain(status === 403 ? "安全校验失败" : "至少等待12秒");
    expect(auth.authSnapshot().message).not.toContain("private"); expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it.each(["999", "https://private.invalid", "-1"])("does not display an untrusted or unbounded Retry-After (%s)", async retry => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 429, headers: { "Retry-After": retry } })));
    stop = auth.startAuthLifecycle(); await flush();
    expect(auth.authSnapshot().message).toBe("工作区连接过于频繁，请稍后手动重试。");
  });

  it.each(["success", "error"])("ignores an old bootstrap %s and its finally after a newer workspace check", async outcome => {
    const old = deferred<ReturnType<typeof response>>();
    const fetcher = vi.fn().mockResolvedValueOnce(response(empty())).mockReturnValueOnce(old.promise)
      .mockResolvedValueOnce(response(session("workspace-b", "b".repeat(32))));
    vi.stubGlobal("fetch", fetcher); stop = auth.startAuthLifecycle(); await flush();
    expect(fetcher).toHaveBeenCalledTimes(2);
    await auth.checkSession(); const epoch = auth.authSnapshot().epoch;
    expect(fetcher.mock.calls[1][1].signal.aborted).toBe(true);
    if (outcome === "success") old.resolve(response(session()));
    else old.reject(new Error("obsolete bootstrap failure"));
    await flush();
    expect(auth.authSnapshot().session?.session_id).toBe("b".repeat(32));
    expect(auth.authSnapshot().epoch).toBe(epoch); expect(auth.authSnapshot().verifying).toBe(false);
    expect(auth.authSnapshot().message).toBeNull(); expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("attaches in-memory CSRF to raw uploads, preserves bytes and propagates caller cancellation", async () => {
    const pending = deferred<ReturnType<typeof response>>();
    const fetcher = vi.fn().mockResolvedValueOnce(response(session())).mockReturnValueOnce(pending.promise);
    vi.stubGlobal("fetch", fetcher); stop = auth.startAuthLifecycle(); await flush();
    const controller = new AbortController(), file = new Blob(["controlled image bytes"]);
    const request = auth.apiFetch("/api/media/landmark", { method: "POST", body: file, headers: { "Content-Type": "application/octet-stream" }, signal: controller.signal });
    const init = fetcher.mock.calls[1][1]; expect(init.body).toBe(file); expect(init.credentials).toBe("same-origin");
    expect(new Headers(init.headers).get("X-CSRF-Token")).toBe(csrf);
    controller.abort(); expect(init.signal.aborted).toBe(true);
    pending.resolve(response({})); await expect(request).rejects.toMatchObject({ name: "AbortError" });
  });

  it("rejects old success, error responses and JSON bodies after workspace A → B → A", async () => {
    const success = deferred<ReturnType<typeof response>>(), failure = deferred<ReturnType<typeof response>>(), body = deferred<object>();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(response(session()))
      .mockReturnValueOnce(success.promise).mockReturnValueOnce(failure.promise).mockResolvedValueOnce({ ok: true, status: 200, json: () => body.promise })
      .mockResolvedValueOnce(response(session("workspace-b", "b".repeat(32)))).mockResolvedValueOnce(response(session("workspace-a", "c".repeat(32)))));
    stop = auth.startAuthLifecycle(); await flush(); const epoch = auth.authSnapshot().epoch;
    const oldSuccess = auth.apiFetch("/api/saved-trips"), oldFailure = auth.apiFetch("/api/places/search");
    const oldBody = (await auth.apiFetch("/api/saved-trips/id")).json();
    await auth.checkSession(); await auth.checkSession(); expect(auth.authSnapshot().epoch).toBeGreaterThan(epoch);
    success.resolve(response({ private: "old a" })); failure.resolve(response({}, 401)); body.resolve({ private: "old a" });
    await expect(oldSuccess).rejects.toMatchObject({ name: "AbortError" }); await expect(oldFailure).rejects.toMatchObject({ name: "AbortError" });
    await expect(oldBody).rejects.toMatchObject({ name: "AbortError" }); expect(auth.authSnapshot().session?.session_id).toBe("c".repeat(32));
  });

  it("invalidates old work before seed recovery, even if the recovered public session ID is unchanged", async () => {
    const oldResponse = deferred<ReturnType<typeof response>>(), oldBody = deferred<object>(), restored = deferred<ReturnType<typeof response>>();
    const fetcher = vi.fn().mockResolvedValueOnce(response(session())).mockReturnValueOnce(oldResponse.promise)
      .mockResolvedValueOnce({ ok: true, status: 200, json: () => oldBody.promise }).mockResolvedValueOnce(response(empty()))
      .mockReturnValueOnce(restored.promise);
    vi.stubGlobal("fetch", fetcher); stop = auth.startAuthLifecycle(); await flush();
    const epoch = auth.authSnapshot().epoch, oldRequest = auth.apiFetch("/api/places/search");
    const body = (await auth.apiFetch("/api/saved-trips/id")).json();
    const reconnect = auth.checkSession(); await flush();
    expect(auth.authSnapshot().epoch).toBeGreaterThan(epoch); expect(auth.authSnapshot().session).toBeNull();
    expect(auth.authSnapshot().verifying).toBe(true); expect(fetcher.mock.calls[1][1].signal.aborted).toBe(true);
    expect(fetcher.mock.calls[4][1].signal.aborted).toBe(false);
    oldResponse.resolve(response({ stale: true })); oldBody.resolve({ stale: true });
    await expect(oldRequest).rejects.toMatchObject({ name: "AbortError" }); await expect(body).rejects.toMatchObject({ name: "AbortError" });
    restored.resolve(response(session())); await reconnect;
    expect(auth.authSnapshot().phase).toBe("authenticated"); expect(auth.authSnapshot().session?.session_id).toBe("a".repeat(32));
    expect(auth.authSnapshot().epoch).toBeGreaterThan(epoch); expect(auth.authSnapshot().verifying).toBe(false);
  });

  it("clears an expired workspace at check start even when a suspended tab missed its expiry timer", async () => {
    vi.useFakeTimers(); const now = Date.now(), old = deferred<ReturnType<typeof response>>(), checked = deferred<ReturnType<typeof response>>();
    const fetcher = vi.fn().mockResolvedValueOnce(response(session("workspace-a", "a".repeat(32), now + 1000)))
      .mockReturnValueOnce(old.promise).mockReturnValueOnce(checked.promise);
    vi.stubGlobal("fetch", fetcher); stop = auth.startAuthLifecycle(); await flush();
    const request = auth.apiFetch("/api/places/search"), epoch = auth.authSnapshot().epoch;
    vi.setSystemTime(now + 2000); const checking = auth.checkSession();
    expect(auth.authSnapshot().session).toBeNull(); expect(auth.authSnapshot().epoch).toBeGreaterThan(epoch);
    expect(fetcher.mock.calls[1][1].signal.aborted).toBe(true);
    old.resolve(response({ stale: true })); await expect(request).rejects.toMatchObject({ name: "AbortError" });
    checked.resolve(response(session())); await checking; expect(auth.authSnapshot().phase).toBe("authenticated");
  });

  it("publishes bounded quota headers without consuming the body and clears stale-workspace notices", async () => {
    const notices = await import("./business-usage-notices"), old = deferred<Response>();
    const denied = () => new Response("controlled body", { status: 429, headers: { "X-Business-Usage-Code": "business_quota_exhausted", "X-Business-Usage-Service": "amap_route", "X-Business-Usage-Scope": "global", "Retry-After": "20" } });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(response(session())).mockResolvedValueOnce(denied()).mockReturnValueOnce(old.promise)
      .mockResolvedValueOnce(response(session("workspace-b", "b".repeat(32)))).mockResolvedValueOnce(response(session("workspace-a", "c".repeat(32)))));
    stop = auth.startAuthLifecycle(); await flush(); const result = await auth.apiFetch("/api/trips/schedule-preview");
    expect(result.bodyUsed).toBe(false); expect(notices.businessUsageNoticeSnapshot()?.service).toBe("amap_route");
    const late = auth.apiFetch("/api/places/search"); await auth.checkSession(); await auth.checkSession();
    expect(notices.businessUsageNoticeSnapshot()).toBeNull(); old.resolve(denied());
    await expect(late).rejects.toMatchObject({ name: "AbortError" }); expect(notices.businessUsageNoticeSnapshot()).toBeNull();
  });

  it("guards video stream chunks without eager reads and releases requests at normal EOF", async () => {
    let reads = 0;
    const stream = new ReadableStream<Uint8Array>({ pull(output) { if (++reads === 1) output.enqueue(new Uint8Array([1, 2, 3])); else output.close(); } }, { highWaterMark: 0 });
    const fetcher = vi.fn().mockResolvedValueOnce(response(session())).mockResolvedValueOnce(new Response(stream))
      .mockResolvedValueOnce(response(session("workspace-b", "b".repeat(32))));
    vi.stubGlobal("fetch", fetcher); stop = auth.startAuthLifecycle(); await flush();
    const result = await auth.apiFetch("/api/media/video-frames"); expect(result.body).toBe(result.body); expect(reads).toBe(0);
    const reader = result.body!.getReader(); expect(await reader.read()).toEqual({ done: false, value: new Uint8Array([1, 2, 3]) });
    expect(await reader.read()).toEqual({ done: true, value: undefined });
    await reader.cancel(); reader.releaseLock(); await auth.checkSession(); expect(fetcher.mock.calls[1][1].signal.aborted).toBe(false);
  });

  it("rejects buffered stream bytes after A → B → A", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(response(session())).mockResolvedValueOnce(new Response(new Uint8Array([7, 8, 9])))
      .mockResolvedValueOnce(response(session("workspace-b", "b".repeat(32)))).mockResolvedValueOnce(response(session("workspace-a", "c".repeat(32)))));
    stop = auth.startAuthLifecycle(); await flush(); const reader = (await auth.apiFetch("/api/media/video-frames")).body!.getReader();
    await auth.checkSession(); await auth.checkSession(); await expect(reader.read()).rejects.toMatchObject({ name: "AbortError" });
  });

  it("cancels a pending stream once on caller abort and cleans stream errors", async () => {
    const cancelled = vi.fn();
    const stream = new ReadableStream<Uint8Array>({ pull() { /* Controlled pending read. */ }, cancel: cancelled }, { highWaterMark: 0 });
    const fetcher = vi.fn().mockResolvedValueOnce(response(session())).mockResolvedValueOnce(new Response(stream))
      .mockResolvedValueOnce(new Response(new ReadableStream({ start(output) { output.error(new Error("controlled stream failure")); } })))
      .mockResolvedValueOnce(response(session("workspace-b", "b".repeat(32))));
    vi.stubGlobal("fetch", fetcher); stop = auth.startAuthLifecycle(); await flush();
    const controller = new AbortController(), result = await auth.apiFetch("/api/media/video-frames", { signal: controller.signal });
    const pending = result.body!.getReader().read(); controller.abort(); controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" }); expect(cancelled).toHaveBeenCalledTimes(1);
    const failure = await auth.apiFetch("/api/media/video-frames"); await expect(failure.body!.getReader().read()).rejects.toThrow("controlled stream failure");
    await auth.checkSession(); expect(fetcher.mock.calls[2][1].signal.aborted).toBe(false);
  });

  it("rebinds cancellation for a non-2xx video body without retaining unread photo errors", async () => {
    const cancelled = vi.fn(), stream = new ReadableStream<Uint8Array>({ pull() {}, cancel: cancelled }, { highWaterMark: 0 });
    const fetcher = vi.fn().mockResolvedValueOnce(response(session())).mockResolvedValueOnce(new Response("controlled error", { status: 502 }))
      .mockResolvedValueOnce(new Response(stream, { status: 403 })).mockResolvedValueOnce(response(session("workspace-b", "b".repeat(32))));
    vi.stubGlobal("fetch", fetcher); stop = auth.startAuthLifecycle(); await flush();
    const unread = await auth.apiFetch("/api/media/landmark"); expect(unread.status).toBe(502);
    const controller = new AbortController(), failedVideo = await auth.apiFetch("/api/media/video-frames", { signal: controller.signal });
    const pending = failedVideo.body!.getReader().read(); controller.abort(); controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" }); expect(cancelled).toHaveBeenCalledTimes(1);
    await auth.checkSession(); expect(fetcher.mock.calls[1][1].signal.aborted).toBe(false);
  });

  it("invalidates a current-session 401 and aborts outstanding work without automatic reconnection", async () => {
    const pending = deferred<ReturnType<typeof response>>();
    const fetcher = vi.fn().mockResolvedValueOnce(response(session())).mockReturnValueOnce(pending.promise).mockResolvedValueOnce(response({}, 401));
    vi.stubGlobal("fetch", fetcher); stop = auth.startAuthLifecycle(); await flush(); const old = auth.apiFetch("/api/media/video-frames");
    await expect(auth.apiFetch("/api/saved-trips")).rejects.toMatchObject({ name: "AbortError" });
    expect(auth.authSnapshot().phase).toBe("anonymous"); expect(fetcher.mock.calls[1][1].signal.aborted).toBe(true);
    pending.resolve(response({})); await expect(old).rejects.toMatchObject({ name: "AbortError" }); expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("expires locally and rearms 30-day workspace timers beyond the browser timer cap", async () => {
    vi.useFakeTimers(); const duration = 30 * 24 * 60 * 60 * 1000;
    const fetcher = vi.fn().mockResolvedValue(response(session("workspace-a", "a".repeat(32), Date.now() + duration)));
    vi.stubGlobal("fetch", fetcher); stop = auth.startAuthLifecycle(); await flush();
    await vi.advanceTimersByTimeAsync(2_147_483_647); expect(auth.authSnapshot().phase).toBe("authenticated");
    await vi.advanceTimersByTimeAsync(duration - 2_147_483_647);
    expect(auth.authSnapshot().phase).toBe("anonymous"); expect(auth.authSnapshot().message).toContain("过期"); expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("ends a connection timeout in a retryable error and aborts its transport", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn((_url, init) => new Promise((_resolve, reject) => init.signal.addEventListener("abort", () => reject(new DOMException("timeout", "AbortError"))))));
    stop = auth.startAuthLifecycle(); await vi.advanceTimersByTimeAsync(10_000);
    expect(auth.authSnapshot().phase).toBe("error"); expect(auth.authSnapshot().verifying).toBe(false);
  });

  it("retains the workspace epoch during recheck and resets it when its public session rotates", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(response(session())).mockResolvedValueOnce(response(session()))
      .mockResolvedValueOnce(response(session("workspace-a", "b".repeat(32)))));
    stop = auth.startAuthLifecycle(); await flush(); const epoch = auth.authSnapshot().epoch;
    await auth.checkSession(); expect(auth.authSnapshot().epoch).toBe(epoch);
    await auth.checkSession(); expect(auth.authSnapshot().epoch).toBeGreaterThan(epoch);
  });

  it("cross-tab invalidation rejects unchecked data, and cleanup ignores a late bootstrap", async () => {
    const channels: { onmessage: ((event: MessageEvent) => void) | null; postMessage: ReturnType<typeof vi.fn>; close: ReturnType<typeof vi.fn> }[] = [];
    vi.stubGlobal("BroadcastChannel", class { onmessage = null; postMessage = vi.fn(); close = vi.fn(); constructor() { channels.push(this); } });
    const pending = deferred<ReturnType<typeof response>>();
    const fetcher = vi.fn().mockResolvedValueOnce(response(session())).mockReturnValueOnce(pending.promise);
    vi.stubGlobal("fetch", fetcher); stop = auth.startAuthLifecycle(); await flush();
    channels[0].onmessage!(new MessageEvent("message", { data: { type: "invalidate", nonce: "non-secret" } }));
    expect(auth.authSnapshot().session).toBeNull(); expect(fetcher).toHaveBeenCalledTimes(1);
    channels[0].onmessage!(new MessageEvent("message", { data: { type: "changed", nonce: "non-secret-2" } }));
    stop(); stop = undefined; pending.resolve(response(empty())); await flush();
    expect(auth.authSnapshot().session).toBeNull(); expect(fetcher).toHaveBeenCalledTimes(2); expect(fetcher.mock.calls[1][1].signal.aborted).toBe(true);
  });
});
