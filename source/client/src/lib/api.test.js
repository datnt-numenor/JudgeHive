import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, NetworkError, REQUEST_TIMEOUT_MS, api, isTransient, request, setUnauthenticatedHandler } from "./api.js";

const json = (status, body, headers = {}) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers });

function stubFetch(impl) {
  const fn = vi.fn(impl);
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  setUnauthenticatedHandler(null);
});

describe("Content-Type", () => {
  it("POST luôn gửi application/json và body là object JSON, kể cả khi không có dữ liệu", async () => {
    const fetchMock = stubFetch(async () => new Response(null, { status: 204 }));
    await api.logout();
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/logout");
    expect(init.method).toBe("POST");
    expect(init.headers["Content-Type"]).toBe("application/json");
    expect(init.body).toBe("{}");
  });

  it.each([
    ["login", () => api.login("alice", "pw"), { user: { id: 1, username: "alice", role: "user" } }],
    [
      "submit",
      () => api.submit({ problem_id: "p", language: "python", code: "x", client_token: "t" }),
      { submission_id: 1, state: "queued", received_at: "2026-10-24T08:15:30.123Z" },
    ],
  ])("mọi hàm POST của api (%s) đi qua cùng đường và có Content-Type", async (_name, call, reply) => {
    const fetchMock = stubFetch(async () => json(201, reply));
    await call();
    const init = fetchMock.mock.calls[0][1];
    expect(init.method).toBe("POST");
    expect(init.headers["Content-Type"]).toBe("application/json");
    expect(typeof JSON.parse(init.body)).toBe("object");
  });

  it("GET không có Content-Type và không có body", async () => {
    const fetchMock = stubFetch(async () => json(200, { user: { id: 1 } }));
    await api.me();
    const init = fetchMock.mock.calls[0][1];
    expect(init.method).toBe("GET");
    expect(init.headers["Content-Type"]).toBeUndefined();
    expect(init.body).toBeUndefined();
  });

  it("gửi cookie phiên cùng origin", async () => {
    const fetchMock = stubFetch(async () => json(200, { problems: [] }));
    await api.problems();
    expect(fetchMock.mock.calls[0][1].credentials).toBe("same-origin");
  });
});

describe("kết quả và lỗi", () => {
  it("201 là bài mới, 200 là trùng client_token", async () => {
    stubFetch(async () => json(200, { submission_id: 7, state: "judged", received_at: "x" }));
    expect((await api.submit({})).duplicate).toBe(true);
    stubFetch(async () => json(201, { submission_id: 8, state: "queued", received_at: "x" }));
    expect((await api.submit({})).duplicate).toBe(false);
  });

  it("lỗi API thành ApiError với status, code, message và Retry-After", async () => {
    stubFetch(async () => json(429, { error: { code: "RATE_LIMITED", message: "slow down" } }, { "Retry-After": "12" }));
    const err = await request("POST", "/api/submissions", { body: {} }).catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect([err.status, err.code, err.message, err.retryAfter]).toEqual([429, "RATE_LIMITED", "slow down", 12]);
    expect(isTransient(err)).toBe(true);
  });

  it("phản hồi lỗi không phải JSON (ví dụ 502 từ proxy) thành BAD_RESPONSE và là lỗi tạm thời", async () => {
    stubFetch(async () => new Response("<html>bad gateway</html>", { status: 502 }));
    const err = await api.me().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect([err.status, err.code]).toEqual([502, "BAD_RESPONSE"]);
    expect(isTransient(err)).toBe(true);
  });

  it("404 không phải lỗi tạm thời", async () => {
    stubFetch(async () => json(404, { error: { code: "SUBMISSION_NOT_FOUND", message: "m" } }));
    const err = await api.submission(5).catch((e) => e);
    expect(isTransient(err)).toBe(false);
  });

  it("fetch bị từ chối thành NetworkError(network)", async () => {
    stubFetch(async () => {
      throw new TypeError("Failed to fetch");
    });
    const err = await api.me().catch((e) => e);
    expect(err).toBeInstanceOf(NetworkError);
    expect(err.kind).toBe("network");
  });

  it("quá hạn chờ thành NetworkError(timeout) và hủy request", async () => {
    vi.useFakeTimers();
    let aborted = false;
    stubFetch(
      (_url, init) =>
        new Promise((_resolve, reject) => {
          init.signal.addEventListener("abort", () => {
            aborted = true;
            reject(new DOMException("aborted", "AbortError"));
          });
        }),
    );
    const pending = api.me().catch((e) => e);
    await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS + 1);
    const err = await pending;
    expect(err).toBeInstanceOf(NetworkError);
    expect(err.kind).toBe("timeout");
    expect(aborted).toBe(true);
  });

  it("signal của người gọi bị hủy thì ném AbortError, không phải NetworkError", async () => {
    stubFetch(
      (_url, init) =>
        new Promise((_resolve, reject) => {
          init.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
        }),
    );
    const controller = new AbortController();
    const pending = api.me({ signal: controller.signal }).catch((e) => e);
    controller.abort();
    expect((await pending).name).toBe("AbortError");
  });

  it("endpoint có id mã hóa id trong đường dẫn", async () => {
    const fetchMock = stubFetch(async () => json(200, {}));
    await api.problem("a/b c");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/problems/a%2Fb%20c");
  });

  it("submissions đặt limit và before_id vào query", async () => {
    const fetchMock = stubFetch(async () => json(200, { submissions: [], next_before_id: null }));
    await api.submissions({ limit: 50, beforeId: 17 });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/submissions?limit=50&before_id=17");
  });
});

describe("401", () => {
  it("UNAUTHENTICATED gọi handler hết phiên", async () => {
    const handler = vi.fn();
    setUnauthenticatedHandler(handler);
    stubFetch(async () => json(401, { error: { code: "UNAUTHENTICATED", message: "m" } }));
    await api.problems().catch(() => {});
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("INVALID_CREDENTIALS (đăng nhập sai) không gọi handler", async () => {
    const handler = vi.fn();
    setUnauthenticatedHandler(handler);
    stubFetch(async () => json(401, { error: { code: "INVALID_CREDENTIALS", message: "m" } }));
    const err = await api.login("a", "b").catch((e) => e);
    expect(err.code).toBe("INVALID_CREDENTIALS");
    expect(handler).not.toHaveBeenCalled();
  });
});
