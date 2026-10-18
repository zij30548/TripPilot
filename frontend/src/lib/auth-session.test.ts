import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type AuthModule = typeof import("./auth-session");
let auth: AuthModule;
let stop: (() => void) | undefined;
const csrf = "c".repeat(43);
const anonymous = () => ({ authenticated: false, user: null, csrf_token: csrf, expires_at: null, session_id: null });
const session = (username = "invite-a", identity = "a".repeat(32), expires = Date.now() + 60_000) => ({
  authenticated: true, user: { id: username, username }, csrf_token: csrf, expires_at: new Date(expires).toISOString(), session_id: identity,
});
const response = (body: unknown, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
const deferred = <T,>() => { let resolve!: (value: T) => void; let reject!: (error: Error) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const flush = async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); };

beforeEach(async () => {
  vi.resetModules();
  vi.stubGlobal("BroadcastChannel", undefined);
  auth = await import("./auth-session");
});
afterEach(() => { stop?.(); stop = undefined; vi.useRealTimers(); });

describe("invitation identity transport", () => {
  it("validates exact public identity shapes without accepting credentials or inconsistent fields", () => {
    const current = session();
    expect(auth.parseAuthSession(current)).toEqual(current);
    expect(auth.parseAuthSession(anonymous())).toEqual(anonymous());
    for (const value of [
      { ...session(), owner_id: "injected" }, { ...session(), session_id: null }, { ...session(), expires_at: "2026-10-18T12:00:00" },
      { ...anonymous(), user: session().user }, { ...anonymous(), csrf_token: "" },
      { ...session(), user: { ...session().user, password: "not-accepted" } },
    ]) expect(() => auth.parseAuthSession(value)).toThrow("登录状态响应无效");
  });

  it("attaches in-memory CSRF to raw uploads, preserves bytes and propagates caller cancellation", async () => {
    const pending = deferred<ReturnType<typeof response>>();
    const fetcher = vi.fn().mockResolvedValueOnce(response(session())).mockImplementationOnce(() => pending.promise);
    vi.stubGlobal("fetch", fetcher);
    stop = auth.startAuthLifecycle(); await flush();
    const controller = new AbortController(); const file = new Blob(["controlled image bytes"]);
    const request = auth.apiFetch("/api/media/landmark", { method: "POST", body: file, headers: { "Content-Type": "application/octet-stream" }, signal: controller.signal });
    const init = fetcher.mock.calls[1][1];
    expect(init.body).toBe(file); expect(init.credentials).toBe("same-origin");
    expect(new Headers(init.headers).get("X-CSRF-Token")).toBe(csrf);
    controller.abort(); expect(init.signal.aborted).toBe(true);
    pending.resolve(response({})); await expect(request).rejects.toMatchObject({ name: "AbortError" });
  });

  it("rejects old success, error responses and JSON bodies after A → B → A", async () => {
    const pendingSuccess = deferred<ReturnType<typeof response>>();
    const pendingFailure = deferred<ReturnType<typeof response>>();
    const pendingBody = deferred<object>();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(response(session("a", "a".repeat(32))))
      .mockReturnValueOnce(pendingSuccess.promise).mockReturnValueOnce(pendingFailure.promise)
      .mockResolvedValueOnce({ ok: true, status: 200, json: () => pendingBody.promise })
      .mockResolvedValueOnce(response(session("b", "b".repeat(32))))
      .mockResolvedValueOnce(response(session("a", "c".repeat(32)))));
    stop = auth.startAuthLifecycle(); await flush();
    const firstEpoch = auth.authSnapshot().epoch;
    const oldSuccess = auth.apiFetch("/api/saved-trips"); const oldFailure = auth.apiFetch("/api/places/search");
    const body = (await auth.apiFetch("/api/saved-trips/id")).json();
    await auth.checkSession(); await auth.checkSession();
    expect(auth.authSnapshot().epoch).toBeGreaterThan(firstEpoch);
    pendingSuccess.resolve(response({ private: "old a" })); pendingFailure.resolve(response({}, 401)); pendingBody.resolve({ private: "old a" });
    await expect(oldSuccess).rejects.toMatchObject({ name: "AbortError" });
    await expect(oldFailure).rejects.toMatchObject({ name: "AbortError" });
    await expect(body).rejects.toMatchObject({ name: "AbortError" });
    expect(auth.authSnapshot().session?.session_id).toBe("c".repeat(32));
  });

  it("guards streamed video chunks without eager reads and releases fully consumed requests", async () => {
    let reads = 0;
    const stream = new ReadableStream<Uint8Array>({
      pull(output) { if (++reads === 1) output.enqueue(new Uint8Array([1, 2, 3])); else output.close(); },
    }, { highWaterMark: 0 });
    const fetcher = vi.fn().mockResolvedValueOnce(response(session())).mockResolvedValueOnce(new Response(stream)).mockResolvedValueOnce(response(anonymous()));
    vi.stubGlobal("fetch", fetcher); stop = auth.startAuthLifecycle(); await flush();
    const result = await auth.apiFetch("/api/media/video-frames");
    expect(result.body).toBe(result.body); expect(reads).toBe(0);
    const reader = result.body!.getReader();
    expect(await reader.read()).toEqual({ done: false, value: new Uint8Array([1, 2, 3]) });
    expect(await reader.read()).toEqual({ done: true, value: undefined });
    const signal = fetcher.mock.calls[1][1].signal;
    await reader.cancel(); reader.releaseLock();
    await auth.logout();
    expect(signal.aborted).toBe(false); // Normal EOF removed it from the registry.
  });

  it("rejects buffered stream bytes after A → B → A without leaking the old body", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(response(session("a", "a".repeat(32))))
      .mockResolvedValueOnce(new Response(new Uint8Array([7, 8, 9])))
      .mockResolvedValueOnce(response(session("b", "b".repeat(32))))
      .mockResolvedValueOnce(response(session("a", "c".repeat(32)))));
    stop = auth.startAuthLifecycle(); await flush();
    const result = await auth.apiFetch("/api/media/video-frames");
    const reader = result.body!.getReader();
    await auth.checkSession(); await auth.checkSession();
    await expect(reader.read()).rejects.toMatchObject({ name: "AbortError" });
    expect(auth.authSnapshot().session?.session_id).toBe("c".repeat(32));
  });

  it("cancels a pending stream read once on caller abort and cleans stream errors", async () => {
    const cancelled = vi.fn();
    const stream = new ReadableStream<Uint8Array>({ pull() { /* Controlled pending read. */ }, cancel: cancelled }, { highWaterMark: 0 });
    const fetcher = vi.fn().mockResolvedValueOnce(response(session())).mockResolvedValueOnce(new Response(stream))
      .mockResolvedValueOnce(new Response(new ReadableStream({ start(output) { output.error(new Error("controlled stream failure")); } })))
      .mockResolvedValueOnce(response(anonymous()));
    vi.stubGlobal("fetch", fetcher); stop = auth.startAuthLifecycle(); await flush();
    const controller = new AbortController();
    const result = await auth.apiFetch("/api/media/video-frames", { signal: controller.signal });
    const reader = result.body!.getReader();
    const pending = reader.read();
    controller.abort(); controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(cancelled).toHaveBeenCalledTimes(1);
    const failure = await auth.apiFetch("/api/media/video-frames");
    await expect(failure.body!.getReader().read()).rejects.toThrow("controlled stream failure");
    const signal = fetcher.mock.calls[2][1].signal;
    await auth.logout(); expect(signal.aborted).toBe(false);
  });

  it("rebinds cancellation for a non-2xx video body without retaining unread photo errors", async () => {
    const cancelled = vi.fn();
    const stream = new ReadableStream<Uint8Array>({ pull() { /* Delayed error body. */ }, cancel: cancelled }, { highWaterMark: 0 });
    const fetcher = vi.fn().mockResolvedValueOnce(response(session()))
      .mockResolvedValueOnce(new Response("controlled error", { status: 502 }))
      .mockResolvedValueOnce(new Response(stream, { status: 403 }))
      .mockResolvedValueOnce(response(anonymous()));
    vi.stubGlobal("fetch", fetcher); stop = auth.startAuthLifecycle(); await flush();
    const unread = await auth.apiFetch("/api/media/landmark");
    expect(unread.status).toBe(502);
    const unreadSignal = fetcher.mock.calls[1][1].signal;
    const controller = new AbortController();
    const failedVideo = await auth.apiFetch("/api/media/video-frames", { signal: controller.signal });
    const pending = failedVideo.body!.getReader().read();
    controller.abort(); controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(cancelled).toHaveBeenCalledTimes(1);
    await auth.logout();
    expect(unreadSignal.aborted).toBe(false);
  });

  it("clears identity synchronously on current-session 401 and aborts outstanding work", async () => {
    const pending = deferred<ReturnType<typeof response>>();
    const fetcher = vi.fn().mockResolvedValueOnce(response(session())).mockReturnValueOnce(pending.promise).mockResolvedValueOnce(response({}, 401));
    vi.stubGlobal("fetch", fetcher); stop = auth.startAuthLifecycle(); await flush();
    const old = auth.apiFetch("/api/media/video-frames");
    await expect(auth.apiFetch("/api/saved-trips")).rejects.toMatchObject({ name: "AbortError" });
    expect(auth.authSnapshot().phase).toBe("anonymous"); expect(fetcher.mock.calls[1][1].signal.aborted).toBe(true);
    pending.resolve(response({})); await expect(old).rejects.toMatchObject({ name: "AbortError" });
  });

  it("expiry clears state locally without issuing a model, route or session request", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn().mockResolvedValue(response(session("a", "a".repeat(32), Date.now() + 2000)));
    vi.stubGlobal("fetch", fetcher); stop = auth.startAuthLifecycle(); await flush();
    await vi.advanceTimersByTimeAsync(2000);
    expect(auth.authSnapshot().phase).toBe("anonymous"); expect(auth.authSnapshot().message).toContain("过期"); expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("login prevents parallel submissions and sends only the explicit invitation credentials once", async () => {
    const pending = deferred<ReturnType<typeof response>>();
    const fetcher = vi.fn().mockResolvedValueOnce(response(anonymous())).mockReturnValueOnce(pending.promise);
    vi.stubGlobal("fetch", fetcher); stop = auth.startAuthLifecycle(); await flush();
    const first = auth.login("invite-a", "controlled-password");
    expect(await auth.login("invite-a", "second-password")).toBe(false);
    expect(fetcher).toHaveBeenCalledTimes(2); expect(fetcher.mock.calls[1][0]).toBe("/api/auth/login");
    expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ username: "invite-a", password: "controlled-password" });
    expect(JSON.stringify(auth.authSnapshot())).not.toContain("controlled-password");
    pending.resolve(response(session())); expect(await first).toBe(true);
  });

  it("failed logout keeps old work cleared and distinguishes uncertain server revocation", async () => {
    const pending = deferred<ReturnType<typeof response>>();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(response(session())).mockReturnValueOnce(pending.promise));
    stop = auth.startAuthLifecycle(); await flush(); const oldEpoch = auth.authSnapshot().epoch;
    const result = auth.logout();
    expect(auth.authSnapshot().session).toBeNull(); expect(auth.authSnapshot().epoch).toBeGreaterThan(oldEpoch);
    pending.reject(new TypeError("network unavailable")); await result;
    expect(auth.authSnapshot().phase).toBe("error"); expect(auth.authSnapshot().message).toContain("尚未确认服务端退出");
  });

  it("authentication timeout ends in a retryable error rather than an endless loading screen", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn((_url, init) => new Promise((_resolve, reject) => init.signal.addEventListener("abort", () => reject(new DOMException("timeout", "AbortError"))))));
    stop = auth.startAuthLifecycle();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(auth.authSnapshot().phase).toBe("error"); expect(auth.authSnapshot().verifying).toBe(false);
  });

  it("same session focus checks retain the workspace epoch; a rotated same-user session resets it", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(response(session())).mockResolvedValueOnce(response(session())).mockResolvedValueOnce(response(session("invite-a", "b".repeat(32)))));
    stop = auth.startAuthLifecycle(); await flush(); const epoch = auth.authSnapshot().epoch;
    await auth.checkSession(); expect(auth.authSnapshot().epoch).toBe(epoch);
    await auth.checkSession(); expect(auth.authSnapshot().epoch).toBeGreaterThan(epoch);
  });

  it("rejects stale login and logout button callbacks even when the visible identity later returns", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(response(anonymous())).mockResolvedValueOnce(response(session()))
      .mockResolvedValueOnce(response(anonymous())).mockResolvedValueOnce(response(session("invite-b", "b".repeat(32))));
    vi.stubGlobal("fetch", fetcher); stop = auth.startAuthLifecycle(); await flush();
    const oldLoginEpoch = auth.authSnapshot().epoch;
    await auth.login("invite-a", "controlled-password", oldLoginEpoch);
    const oldLogoutEpoch = auth.authSnapshot().epoch;
    await auth.logout();
    expect(await auth.login("invite-a", "stale-password", oldLoginEpoch)).toBe(false);
    expect(fetcher).toHaveBeenCalledTimes(3);
    await auth.checkSession();
    await auth.logout(oldLogoutEpoch);
    expect(fetcher).toHaveBeenCalledTimes(4);
    expect(auth.authSnapshot().session?.user?.username).toBe("invite-b");
  });

  it("cross-tab invalidation contains no credentials and cannot restore an unverified identity", async () => {
    const channels: { onmessage: ((event: MessageEvent) => void) | null; postMessage: ReturnType<typeof vi.fn>; close: ReturnType<typeof vi.fn> }[] = [];
    vi.stubGlobal("BroadcastChannel", class { onmessage = null; postMessage = vi.fn(); close = vi.fn(); constructor() { channels.push(this); } });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(response(session())).mockResolvedValueOnce(response(anonymous())));
    stop = auth.startAuthLifecycle(); await flush();
    channels[0].onmessage!(new MessageEvent("message", { data: { type: "invalidate", nonce: "non-secret" } }));
    expect(auth.authSnapshot().phase).toBe("anonymous"); expect(auth.authSnapshot().session).toBeNull();
    expect(fetch).toHaveBeenCalledTimes(1);
    channels[0].onmessage!(new MessageEvent("message", { data: { type: "changed", nonce: "non-secret-2" } })); await flush();
    expect(auth.authSnapshot().phase).toBe("anonymous"); expect(fetch).toHaveBeenCalledTimes(2);
  });
});
