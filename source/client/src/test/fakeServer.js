// Server giả cho test giao diện: dựng lại ĐÚNG hình dạng request/response của README mục 3, không hơn.
// Đây chỉ là chỗ thay mạng. Việc đối chiếu hợp đồng với server thật là một lượt kiểm riêng khi PR 5 merge.
import { vi } from "vitest";

export const json = (status, body, headers = {}) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });

export const apiError = (status, code, message = "m", headers = {}) => json(status, { error: { code, message } }, headers);

export const USER = { id: 5, username: "alice", role: "user" };

export const PROBLEM = {
  id: "sum-two",
  title: "Tổng hai số",
  statement: "Cho hai số a, b.\nIn ra a + b.",
  time_limit_ms: 1000,
  memory_limit_kib: 262144,
  hidden: false,
  samples: [{ input: "1 2\n", output: "3\n" }],
};

export function submissionObject(over = {}) {
  return {
    submission_id: 42,
    problem_id: "sum-two",
    problem_title: "Tổng hai số",
    language: "python",
    received_at: "2026-10-24T08:15:30.123Z",
    state: "judged",
    verdict: "AC",
    time_ms: 12,
    memory_kib: 9216,
    failed_test: null,
    failed_is_sample: null,
    re_kind: null,
    rejudged_at: null,
    code: "print(1)\n",
    ce_message: null,
    sample_stdout: null,
    sample_input: null,
    sample_expected_output: null,
    ...over,
  };
}

/**
 * Cài `fetch` giả. `routes` là hàm `(method, path, body, headers) => Response | undefined`; trả undefined thì 404 JSON.
 * Mọi request được ghi vào `calls` để test soi lại.
 */
export function installFetch(routes) {
  const calls = [];
  const fn = vi.fn(async (url, init = {}) => {
    const method = init.method ?? "GET";
    const body = init.body ? JSON.parse(init.body) : undefined;
    calls.push({ method, path: url, body, headers: init.headers ?? {} });
    const res = await routes(method, url, body, init.headers ?? {});
    return res ?? apiError(404, "NOT_FOUND");
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}
