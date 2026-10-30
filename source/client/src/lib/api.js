// Lớp gọi REST duy nhất của client. Mọi request đi qua `request`, nên Content-Type, timeout và xử lý 401 nằm một chỗ.
// Hợp đồng: README mục "Giao tiếp" phần 3 (định dạng lỗi `{"error": {"code", "message"}}`).

export const REQUEST_TIMEOUT_MS = 15000; // [ĐỀ XUẤT]

/** Server đã trả lời nhưng là lỗi (HTTP status không phải 2xx). */
export class ApiError extends Error {
  constructor(status, code, message, retryAfter = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.retryAfter = retryAfter;
  }
}

/** Không nhận được phản hồi: mất mạng (`kind` = "network") hoặc quá hạn chờ (`kind` = "timeout"). */
export class NetworkError extends Error {
  constructor(kind) {
    super(kind === "timeout" ? "request timed out" : "network error");
    this.name = "NetworkError";
    this.kind = kind;
  }
}

let unauthenticatedHandler = null;

/** Đăng ký hàm được gọi khi server trả 401 `UNAUTHENTICATED` (hết phiên). 401 `INVALID_CREDENTIALS` không tính. */
export function setUnauthenticatedHandler(fn) {
  unauthenticatedHandler = fn;
}

/** Lỗi tạm thời: thử lại sau được. Không gồm 4xx khác (400, 404, 413...), vì thử lại cũng vậy. */
export function isTransient(err) {
  return err instanceof NetworkError || (err instanceof ApiError && (err.status >= 500 || err.status === 429));
}

/** Chưa biết server đã xử lý request hay chưa (không có phản hồi, bị hủy giữa chừng, hoặc 5xx). */
export function isUnknownOutcome(err) {
  return err instanceof NetworkError || (err instanceof ApiError && err.status >= 500) || err?.name === "AbortError";
}

function abortError() {
  return new DOMException("The operation was aborted.", "AbortError");
}

/**
 * Trả `{status, data}`; `data` là null khi không có body (204). Ném `ApiError` hoặc `NetworkError`; ném `AbortError`
 * nếu `signal` của người gọi bị hủy. Mọi request không phải GET luôn gửi `Content-Type: application/json` và một body
 * là object JSON (mặc định `{}`): server trả 415 nếu thiếu.
 */
export async function request(method, path, { body, signal, timeoutMs = REQUEST_TIMEOUT_MS } = {}) {
  const headers = { Accept: "application/json" };
  const init = { method, headers, credentials: "same-origin" };
  if (method !== "GET") {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body ?? {});
  }

  if (signal?.aborted) throw abortError();
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  const onCallerAbort = () => controller.abort();
  signal?.addEventListener("abort", onCallerAbort, { once: true });
  init.signal = controller.signal;

  const failure = () => {
    if (timedOut) return new NetworkError("timeout");
    if (signal?.aborted) return abortError();
    return new NetworkError("network");
  };

  try {
    let response;
    let text;
    try {
      response = await fetch(path, init);
      text = await response.text();
    } catch {
      throw failure();
    }
    return interpret(response, text);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", onCallerAbort);
  }
}

function interpret(response, text) {
  const { status } = response;
  let data = null;
  let parsed = true;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      parsed = false;
    }
  }
  if (status >= 200 && status < 300) {
    if (!parsed) throw new ApiError(status, "BAD_RESPONSE", "response is not JSON");
    return { status, data };
  }
  const info = parsed && data && typeof data.error === "object" && data.error ? data.error : null;
  const code = typeof info?.code === "string" ? info.code : "BAD_RESPONSE";
  const message = typeof info?.message === "string" ? info.message : `HTTP ${status}`;
  const retry = Number.parseInt(response.headers.get("Retry-After") ?? "", 10);
  const err = new ApiError(status, code, message, Number.isFinite(retry) && retry >= 1 ? retry : null);
  if (status === 401 && code === "UNAUTHENTICATED") unauthenticatedHandler?.();
  throw err;
}

const enc = encodeURIComponent;

export const api = {
  login: (username, password) => request("POST", "/api/login", { body: { username, password } }).then((r) => r.data.user),
  logout: () => request("POST", "/api/logout"),
  me: (opts) => request("GET", "/api/me", opts).then((r) => r.data.user),
  problems: (opts) => request("GET", "/api/problems", opts).then((r) => r.data.problems),
  problem: (id, opts) => request("GET", `/api/problems/${enc(id)}`, opts).then((r) => r.data),
  /** 201 là bài mới, 200 là trùng `client_token`: `duplicate`. */
  submit: (payload, opts) =>
    request("POST", "/api/submissions", { ...opts, body: payload }).then((r) => ({ ...r.data, duplicate: r.status === 200 })),
  submissions: ({ limit, beforeId } = {}, opts) => {
    const q = new URLSearchParams();
    if (limit != null) q.set("limit", String(limit));
    if (beforeId != null) q.set("before_id", String(beforeId));
    const qs = q.toString();
    return request("GET", `/api/submissions${qs ? `?${qs}` : ""}`, opts).then((r) => r.data);
  },
  submission: (id, opts) => request("GET", `/api/submissions/${enc(id)}`, opts).then((r) => r.data),
  scoreboard: (opts) => request("GET", "/api/scoreboard", opts).then((r) => r.data),
};
