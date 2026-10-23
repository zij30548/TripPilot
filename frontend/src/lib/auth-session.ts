import { API_ENDPOINTS } from "./api-endpoints";
import { clearBusinessUsageNotice, recordBusinessUsageNotice } from "./business-usage-notices";

/** Internal browser-workspace identity; no account/password sign-in surface. */
export type AuthSession = {
  authenticated: boolean;
  user: { id: string; username: string } | null;
  csrf_token: string;
  expires_at: string | null;
  /** Public session identity, not the HttpOnly authentication credential. */
  session_id: string | null;
};

type AuthState = {
  phase: "checking" | "authenticated" | "anonymous" | "error";
  session: AuthSession | null;
  epoch: number;
  verifying: boolean;
  message: string | null;
};

const INITIAL_STATE: AuthState = { phase: "checking", session: null, epoch: 0, verifying: false, message: null };
const AUTH_TIMEOUT_MS = 10_000;
const listeners = new Set<() => void>();
const requests = new Set<AbortController>();
let state = INITIAL_STATE;
let active = false;
let operationVersion = 0;
let operation: AbortController | null = null;
let expiryTimer: ReturnType<typeof setTimeout> | null = null;
let channel: BroadcastChannel | null = null;

class WorkspaceConnectionError extends Error {}

function record(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function exact(value: Record<string, unknown>, fields: string[]): boolean {
  return Object.keys(value).length === fields.length && fields.every(field => Object.hasOwn(value, field));
}

export function parseAuthSession(value: unknown): AuthSession {
  if (!record(value) || !exact(value, ["authenticated", "user", "csrf_token", "expires_at", "session_id"]) ||
      typeof value.authenticated !== "boolean" || typeof value.csrf_token !== "string" ||
      !/^[A-Za-z0-9_-]{20,128}$/.test(value.csrf_token)) throw new Error("工作区状态响应无效，请重新检查。");
  if (value.authenticated) {
    if (!record(value.user) || !exact(value.user, ["id", "username"]) ||
        typeof value.user.id !== "string" || !/^[A-Za-z0-9_-]{1,80}$/.test(value.user.id) ||
        typeof value.user.username !== "string" || value.user.username.length < 1 || value.user.username.length > 80 ||
        /[\u0000-\u001f\u007f]/.test(value.user.username) ||
        typeof value.session_id !== "string" || !/^[A-Za-z0-9_-]{20,128}$/.test(value.session_id) ||
        typeof value.expires_at !== "string" || value.expires_at.length > 40 ||
        !/(?:Z|[+-]\d{2}:\d{2})$/.test(value.expires_at) || !Number.isFinite(Date.parse(value.expires_at))) throw new Error("工作区状态响应无效，请重新检查。");
  } else if (value.user !== null || value.session_id !== null || value.expires_at !== null) {
    throw new Error("工作区状态响应无效，请重新检查。");
  }
  return value as AuthSession;
}

export function subscribeAuth(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
export function authSnapshot(): AuthState { return state; }
export function authServerSnapshot(): AuthState { return INITIAL_STATE; }

function publish(next: AuthState): void {
  if (next.epoch !== state.epoch) clearBusinessUsageNotice();
  state = next;
  listeners.forEach(listener => listener());
}

function abortRequests(): void {
  for (const controller of requests) controller.abort();
  requests.clear();
}

function clearExpiry(): void {
  if (expiryTimer) clearTimeout(expiryTimer);
  expiryTimer = null;
}

function clearIdentity(message: string | null, phase: AuthState["phase"] = "anonymous"): void {
  clearExpiry();
  // Advance before aborting: even a transport that ignores AbortSignal is stale.
  publish({ phase, session: null, epoch: state.epoch + 1, verifying: false, message });
  abortRequests();
}

function expired(): boolean {
  return !!state.session?.expires_at && Date.parse(state.session.expires_at) <= Date.now();
}

function armExpiry(session: AuthSession): void {
  clearExpiry();
  if (!session.authenticated || !session.expires_at) return;
  const identity = session.session_id;
  expiryTimer = setTimeout(() => {
    if (state.session?.session_id !== identity) return;
    // Browser timers cap at ~24.8 days; a 30-day workspace needs another arm.
    if (!expired()) { armExpiry(session); return; }
    operationVersion += 1;
    operation?.abort();
    clearIdentity("工作区已过期，请重新连接；未保存的工作已清除。");
  }, Math.max(0, Math.min(2_147_483_647, Date.parse(session.expires_at) - Date.now())));
}

function acceptSession(session: AuthSession, message: string | null = null): void {
  const previous = state.session?.authenticated ? state.session.session_id : null;
  const next = session.authenticated ? session.session_id : null;
  const changed = previous !== next;
  if (changed) abortRequests();
  publish({
    phase: session.authenticated ? "authenticated" : "anonymous", session,
    epoch: state.epoch + (changed ? 1 : 0), verifying: false, message,
  });
  armExpiry(session);
}

function broadcast(type: "invalidate" | "changed"): void {
  channel?.postMessage({ type, nonce: crypto.randomUUID() });
}

async function authRequest(endpoint: string, controller: AbortController, init?: RequestInit): Promise<AuthSession> {
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, AUTH_TIMEOUT_MS);
  try {
    const response = await fetch(endpoint, { ...init, credentials: "same-origin", cache: "no-store", signal: controller.signal });
    if (!response.ok) {
      if (response.status === 401) throw new WorkspaceConnectionError("工作区会话已失效，请重新连接。");
      if (response.status === 429) {
        const value = response.headers?.get("Retry-After");
        const wait = value && /^\d{1,3}$/.test(value) ? Number(value) : null;
        throw new WorkspaceConnectionError(wait !== null && wait >= 1 && wait <= 300 ?
          `工作区连接过于频繁，请至少等待${wait}秒后手动重试。` : "工作区连接过于频繁，请稍后手动重试。");
      }
      if (response.status === 403) throw new WorkspaceConnectionError("工作区安全校验失败，请重新连接。");
      throw new WorkspaceConnectionError("工作区服务暂时不可用，请稍后手动重试。");
    }
    let session: AuthSession;
    try { session = parseAuthSession(await response.json()); }
    catch { throw new WorkspaceConnectionError("工作区状态响应无效，请手动重试。"); }
    if (timedOut) throw new WorkspaceConnectionError("工作区连接已超时，请手动重试。");
    if (controller.signal.aborted) throw new DOMException("工作区连接已取消。", "AbortError");
    return session;
  } catch (error) {
    if (timedOut) throw new WorkspaceConnectionError("工作区连接已超时，请手动重试。");
    if (controller.signal.aborted) throw new DOMException("工作区连接已取消。", "AbortError");
    if (error instanceof WorkspaceConnectionError) throw error;
    throw new WorkspaceConnectionError("无法连接工作区，请检查连接后手动重试。");
  } finally { clearTimeout(timer); }
}

export async function checkSession(): Promise<void> {
  const version = ++operationVersion;
  operation?.abort();
  const controller = new AbortController();
  operation = controller;
  if (expired()) clearIdentity("工作区已过期，请重新连接；未保存的工作已清除。", "checking");
  publish({ ...state, verifying: true, message: state.phase === "error" ? null : state.message });
  try {
    let session = await authRequest(API_ENDPOINTS.workspaceSession, controller);
    if (version !== operationVersion) return;
    if (controller.signal.aborted) throw new WorkspaceConnectionError("工作区连接已取消或超时，请手动重试。");
    if (!session.authenticated) {
      // A server-side expiry/revocation is a boundary even if seed recovery later
      // returns the same public session ID. Invalidate old business work first.
      if (state.session?.authenticated) {
        clearIdentity(null, "checking");
        publish({ ...state, verifying: true });
      }
      session = await authRequest(API_ENDPOINTS.workspaceSession, controller, {
        method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": session.csrf_token },
        body: "{}",
      });
      if (version !== operationVersion) return;
      if (controller.signal.aborted || !session.authenticated) throw new WorkspaceConnectionError("工作区尚未建立，请手动重试。");
    }
    acceptSession(session);
  } catch (error) {
    if (version !== operationVersion) return;
    // Fail closed; never expose the previous identity while verification failed.
    clearIdentity(error instanceof WorkspaceConnectionError ? error.message :
      controller.signal.aborted ? "工作区连接已取消或超时，请手动重试。" : "无法连接工作区，请检查连接后手动重试。", "error");
  } finally {
    if (version === operationVersion) {
      operation = null;
      if (state.verifying) publish({ ...state, verifying: false });
    }
  }
}

export function startAuthLifecycle(): () => void {
  active = true;
  if (typeof BroadcastChannel !== "undefined") {
    channel = new BroadcastChannel("trippilot-workspace");
    channel.onmessage = event => {
      if (!record(event.data) || !["invalidate", "changed"].includes(String(event.data.type))) return;
      operationVersion += 1;
      operation?.abort();
      operation = null;
      clearIdentity("其他标签页的工作区已变化；旧工作区的临时内容已清除。");
      if (event.data.type === "changed") void checkSession();
    };
  }
  const visible = () => { if (document.visibilityState === "visible" && !operation) void checkSession(); };
  const focused = () => { if (!operation) void checkSession(); };
  const restored = () => { if (!operation) void checkSession(); };
  document.addEventListener("visibilitychange", visible);
  window.addEventListener("focus", focused);
  window.addEventListener("pageshow", restored);
  void checkSession();
  return () => {
    active = false;
    operationVersion += 1;
    operation?.abort();
    operation = null;
    clearIdentity(null, "checking");
    channel?.close();
    channel = null;
    document.removeEventListener("visibilitychange", visible);
    window.removeEventListener("focus", focused);
    window.removeEventListener("pageshow", restored);
  };
}

/** Auth-aware transport only; callers retain their own timeout/AbortController. */
export async function apiFetch(input: string, init: RequestInit = {}): Promise<Response> {
  if (active && (state.verifying || state.phase !== "authenticated" || expired())) {
    if (expired()) clearIdentity("工作区已过期，请重新连接；未保存的工作已清除。");
    throw new DOMException("身份已失效或正在校验", "AbortError");
  }
  const epoch = state.epoch;
  const controller = new AbortController();
  const cancel = () => controller.abort();
  if (init.signal?.aborted) controller.abort();
  else init.signal?.addEventListener("abort", cancel, { once: true });
  requests.add(controller);
  let registered = true;
  let headers = init.headers;
  const method = (init.method ?? "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method) && state.session?.csrf_token) {
    headers = new Headers(init.headers);
    headers.set("X-CSRF-Token", state.session.csrf_token);
  }
  const cleanup = () => {
    registered = false;
    requests.delete(controller);
    init.signal?.removeEventListener("abort", cancel);
  };
  controller.signal.addEventListener("abort", cleanup, { once: true });
  try {
    const response = await fetch(input, { ...init, headers, credentials: "same-origin", cache: "no-store", signal: controller.signal });
    if (epoch !== state.epoch || controller.signal.aborted) throw new DOMException("旧身份请求已失效", "AbortError");
    if (response.status === 401) {
      operationVersion += 1;
      operation?.abort();
      operation = null;
      clearIdentity("工作区会话已失效，请重新连接；未保存的工作已清除。");
      broadcast("invalidate");
      throw new DOMException("工作区已失效", "AbortError");
    }
    // Read allowlisted metadata only, never consume/clone a media or error body.
    // The identity check above rejects old-account notices before publication.
    recordBusinessUsageNotice(response, epoch);
    if (!response.ok) cleanup();
    let guardedBody: ReadableStream<Uint8Array> | null = null;
    const stale = () => epoch !== state.epoch || controller.signal.aborted;
    const staleError = () => new DOMException("旧身份响应已失效", "AbortError");
    const registerForBody = () => {
      if (stale() || init.signal?.aborted) throw staleError();
      if (!registered) {
        registered = true;
        requests.add(controller);
        init.signal?.addEventListener("abort", cancel, { once: true });
      }
    };
    function guardStream(source: ReadableStream<Uint8Array>): ReadableStream<Uint8Array> {
      let reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
      let finished = false;
      let cancellation: Promise<void> | null = null;
      let sink: ReadableStreamDefaultController<Uint8Array>;
      const finish = () => {
        finished = true;
        controller.signal.removeEventListener("abort", abortBody);
        cleanup();
      };
      const release = () => { reader?.releaseLock(); reader = null; };
      const cancelBody = (reason?: unknown): Promise<void> => {
        if (!cancellation) {
          cancellation = Promise.resolve().then(() => {
            reader ??= source.getReader();
            return reader.cancel(reason);
          }).finally(release);
        }
        return cancellation;
      };
      const abortBody = () => {
        if (finished) return;
        const error = staleError();
        sink.error(error);
        finish();
        void cancelBody(error).catch(() => undefined);
      };
      const stream = new ReadableStream<Uint8Array>({
        start(output) { sink = output; },
        async pull(output) {
          try {
            if (stale()) throw staleError();
            reader ??= source.getReader();
            const chunk = await reader.read();
            if (stale()) throw staleError();
            if (finished) return;
            if (chunk.done) {
              output.close();
              finish();
              release();
            } else output.enqueue(chunk.value);
          } catch (error) {
            if (!finished) { output.error(error); finish(); }
            void cancelBody(error).catch(() => undefined);
          }
        },
        async cancel(reason) {
          finish();
          await cancelBody(reason);
        },
      }, { highWaterMark: 0 }); // Read only on consumer demand; no eager buffering.
      controller.signal.addEventListener("abort", abortBody, { once: true });
      if (stale()) abortBody();
      return stream;
    }
    // Keep identity and cancellation guards through body consumption, not only
    // until HTTP headers arrive. This also handles deliberately late JSON stubs.
    return new Proxy(response, {
      get(target, property) {
        const member = Reflect.get(target, property, target);
        if (property === "body") {
          if (!member) { cleanup(); return null; }
          if (!guardedBody) { registerForBody(); guardedBody = guardStream(member); }
          return guardedBody;
        }
        if (["json", "text", "blob", "arrayBuffer", "formData"].includes(String(property)) && typeof member === "function") {
          return async () => {
            try {
              registerForBody();
              const body = await member.call(target);
              if (epoch !== state.epoch || controller.signal.aborted) throw new DOMException("旧身份响应已失效", "AbortError");
              return body;
            } finally { cleanup(); }
          };
        }
        return typeof member === "function" ? member.bind(target) : member;
      },
    });
  } catch (error) {
    cleanup();
    throw error;
  }
}
