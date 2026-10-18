import { API_ENDPOINTS } from "./api-endpoints";

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

function record(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function exact(value: Record<string, unknown>, fields: string[]): boolean {
  return Object.keys(value).length === fields.length && fields.every(field => Object.hasOwn(value, field));
}

export function parseAuthSession(value: unknown): AuthSession {
  if (!record(value) || !exact(value, ["authenticated", "user", "csrf_token", "expires_at", "session_id"]) ||
      typeof value.authenticated !== "boolean" || typeof value.csrf_token !== "string" ||
      !/^[A-Za-z0-9_-]{20,128}$/.test(value.csrf_token)) throw new Error("登录状态响应无效，请重新检查。");
  if (value.authenticated) {
    if (!record(value.user) || !exact(value.user, ["id", "username"]) ||
        typeof value.user.id !== "string" || !/^[A-Za-z0-9_-]{1,80}$/.test(value.user.id) ||
        typeof value.user.username !== "string" || value.user.username.length < 1 || value.user.username.length > 80 ||
        /[\u0000-\u001f\u007f]/.test(value.user.username) ||
        typeof value.session_id !== "string" || !/^[A-Za-z0-9_-]{20,128}$/.test(value.session_id) ||
        typeof value.expires_at !== "string" || value.expires_at.length > 40 ||
        !/(?:Z|[+-]\d{2}:\d{2})$/.test(value.expires_at) || !Number.isFinite(Date.parse(value.expires_at))) throw new Error("登录状态响应无效，请重新检查。");
  } else if (value.user !== null || value.session_id !== null || value.expires_at !== null) {
    throw new Error("登录状态响应无效，请重新检查。");
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
    if (state.session?.session_id === identity && expired()) {
      operationVersion += 1;
      operation?.abort();
      clearIdentity("登录已过期，请重新登录；未保存的工作已清除。");
    }
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
  const timer = setTimeout(() => controller.abort(), AUTH_TIMEOUT_MS);
  try {
    const response = await fetch(endpoint, { ...init, credentials: "same-origin", cache: "no-store", signal: controller.signal });
    if (!response.ok) {
      if (response.status === 401) throw new Error("账号或密码不正确，或登录已失效。");
      if (response.status === 429) throw new Error("登录尝试过于频繁，请稍后再试。");
      if (response.status === 403) throw new Error("登录安全校验失败，请重新检查登录状态后再试。");
      throw new Error("登录服务暂时不可用，请稍后重试。");
    }
    return parseAuthSession(await response.json());
  } finally { clearTimeout(timer); }
}

export async function checkSession(): Promise<void> {
  const version = ++operationVersion;
  operation?.abort();
  const controller = new AbortController();
  operation = controller;
  publish({ ...state, verifying: true, message: state.phase === "error" ? null : state.message });
  try {
    const session = await authRequest(API_ENDPOINTS.authSession, controller);
    if (version !== operationVersion) return;
    if (controller.signal.aborted) throw new Error("登录状态查询已超时。");
    acceptSession(session, !session.authenticated && state.session?.authenticated ? "登录已失效，请重新登录；未保存的工作已清除。" : state.message);
  } catch {
    if (version !== operationVersion) return;
    // Fail closed; never expose the previous identity while verification failed.
    clearIdentity("无法验证登录状态，请检查连接后重试。", "error");
  } finally {
    if (version === operationVersion) {
      operation = null;
      if (state.verifying) publish({ ...state, verifying: false });
    }
  }
}

export async function login(username: string, password: string, expectedEpoch = state.epoch): Promise<boolean> {
  const csrf = state.session?.csrf_token;
  if (!csrf || operation || state.phase !== "anonymous" || expectedEpoch !== state.epoch) return false;
  const version = ++operationVersion;
  const controller = new AbortController();
  operation = controller;
  clearIdentity(null, "checking");
  try {
    const session = await authRequest(API_ENDPOINTS.authLogin, controller, {
      method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: JSON.stringify({ username, password }),
    });
    if (version !== operationVersion) return false;
    if (controller.signal.aborted) throw new Error("登录等待超时，请重新检查登录状态。");
    if (!session.authenticated) throw new Error("登录未完成，请重新检查登录状态。");
    acceptSession(session);
    broadcast("changed");
    return true;
  } catch (error) {
    if (version !== operationVersion) return false;
    // Preserve only the pre-login CSRF bootstrap; credentials are never retained.
    publish({ ...state, phase: "anonymous", session: { authenticated: false, user: null, csrf_token: csrf, expires_at: null, session_id: null },
      message: error instanceof Error ? error.message : "登录失败，请重试。" });
    return false;
  } finally { if (version === operationVersion) operation = null; }
}

export async function logout(expectedEpoch = state.epoch): Promise<void> {
  if (expectedEpoch !== state.epoch || state.phase !== "authenticated") return;
  const csrf = state.session?.csrf_token;
  const version = ++operationVersion;
  operation?.abort();
  const controller = new AbortController();
  operation = controller;
  clearIdentity("正在退出，未保存的工作已清除。", "checking");
  broadcast("invalidate");
  try {
    if (!csrf) throw new Error("登录安全状态已失效。");
    const session = await authRequest(API_ENDPOINTS.authLogout, controller, {
      method: "POST", headers: { "X-CSRF-Token": csrf },
    });
    if (version !== operationVersion) return;
    if (controller.signal.aborted) throw new Error("退出等待超时。");
    acceptSession(session, "已退出登录；未保存的工作已清除。");
    broadcast("changed");
  } catch {
    if (version !== operationVersion) return;
    clearIdentity("本机工作已清除，但尚未确认服务端退出。请恢复连接后重新检查登录状态并退出。", "error");
  } finally { if (version === operationVersion) operation = null; }
}

export function startAuthLifecycle(): () => void {
  active = true;
  if (typeof BroadcastChannel !== "undefined") {
    channel = new BroadcastChannel("trippilot-identity");
    channel.onmessage = event => {
      if (!record(event.data) || !["invalidate", "changed"].includes(String(event.data.type))) return;
      operationVersion += 1;
      operation?.abort();
      operation = null;
      clearIdentity("其他标签页的登录状态已变化；原身份工作已清除。");
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
    if (expired()) clearIdentity("登录已过期，请重新登录；未保存的工作已清除。");
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
      clearIdentity("登录已失效，请重新登录；未保存的工作已清除。");
      broadcast("invalidate");
      throw new DOMException("登录已失效", "AbortError");
    }
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
