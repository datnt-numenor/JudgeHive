import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App.jsx";
import { PROBLEM, USER, apiError, installFetch, json, submissionObject } from "./test/fakeServer.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function renderAt(path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("đăng nhập", () => {
  it("chưa có phiên thì về trang đăng nhập; sai mật khẩu báo lỗi; đúng thì vào danh sách đề", async () => {
    let loggedIn = false;
    const calls = installFetch((method, path, body) => {
      if (path === "/api/me") return loggedIn ? json(200, { user: USER }) : apiError(401, "UNAUTHENTICATED");
      if (path === "/api/login") {
        if (body.password !== "good") return apiError(401, "INVALID_CREDENTIALS");
        loggedIn = true;
        return json(200, { user: USER });
      }
      if (path === "/api/problems") return json(200, { problems: [{ id: "sum-two", title: "Tổng hai số", time_limit_ms: 1000, memory_limit_kib: 262144, hidden: false }] });
    });
    const user = userEvent.setup();
    renderAt("/");
    await screen.findByRole("heading", { name: "Đăng nhập" });

    await user.type(screen.getByLabelText("Tên đăng nhập"), "alice");
    await user.type(screen.getByLabelText("Mật khẩu"), "bad");
    await user.click(screen.getByRole("button", { name: "Đăng nhập" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Sai tên đăng nhập hoặc mật khẩu.");
    // 401 của đăng nhập sai không được xem là hết phiên: vẫn ở trang đăng nhập, form còn nguyên
    expect(screen.getByRole("heading", { name: "Đăng nhập" })).toBeInTheDocument();

    await user.clear(screen.getByLabelText("Mật khẩu"));
    await user.type(screen.getByLabelText("Mật khẩu"), "good");
    await user.click(screen.getByRole("button", { name: "Đăng nhập" }));
    expect(await screen.findByRole("link", { name: "Tổng hai số" })).toBeInTheDocument();
    const login = calls.find((c) => c.path === "/api/login" && c.body.password === "good");
    expect(login.headers["Content-Type"]).toBe("application/json");
    expect(login.body).toEqual({ username: "alice", password: "good" });
  });

  it("đăng xuất gọi POST /api/logout rồi về trang đăng nhập", async () => {
    let loggedIn = true;
    const calls = installFetch((method, path) => {
      if (path === "/api/me") return loggedIn ? json(200, { user: USER }) : apiError(401, "UNAUTHENTICATED");
      if (path === "/api/problems") return json(200, { problems: [] });
      if (path === "/api/logout") {
        loggedIn = false;
        return new Response(null, { status: 204 });
      }
    });
    const user = userEvent.setup();
    renderAt("/");
    await user.click(await screen.findByRole("button", { name: "Đăng xuất" }));
    await screen.findByRole("heading", { name: "Đăng nhập" });
    expect(calls.some((c) => c.method === "POST" && c.path === "/api/logout" && c.body && Object.keys(c.body).length === 0)).toBe(true);
  });
});

describe("nộp bài", () => {
  function problemRoutes(onSubmit) {
    return (method, path, body) => {
      if (path === "/api/me") return json(200, { user: USER });
      if (path === "/api/problems/sum-two") return json(200, PROBLEM);
      if (path === "/api/submissions" && method === "POST") return onSubmit(body);
      if (path === "/api/submissions/42") return json(200, submissionObject({ state: "queued", verdict: null, time_ms: null, memory_kib: null }));
    };
  }

  it("gửi client_token UUID, Content-Type JSON, rồi chuyển sang trang bài nộp", async () => {
    const calls = installFetch(problemRoutes(() => json(201, { submission_id: 42, state: "queued", received_at: "2026-10-24T08:15:30.123Z" })));
    const user = userEvent.setup();
    renderAt("/problems/sum-two");
    await screen.findByRole("heading", { name: "Tổng hai số" });
    expect(screen.getByText(/Cho hai số a, b\./)).toBeInTheDocument();

    await user.type(screen.getByLabelText("Mã nguồn"), "print(1)");
    await user.click(screen.getByRole("button", { name: "Nộp bài" }));
    await screen.findByRole("heading", { name: /Bài nộp #42/ });

    const post = calls.find((c) => c.method === "POST");
    expect(post.headers["Content-Type"]).toBe("application/json");
    expect(post.body).toMatchObject({ problem_id: "sum-two", language: "python", code: "print(1)" });
    expect(post.body.client_token).toMatch(UUID_RE);
    expect(Object.keys(post.body).sort()).toEqual(["client_token", "code", "language", "problem_id"]); // không gửi user_id, role
  });

  it("lỗi mạng rồi bấm lại: dùng lại đúng token; sửa mã rồi bấm: token mới", async () => {
    let attempt = 0;
    const calls = installFetch((method, path, body, headers) => {
      if (path === "/api/submissions" && method === "POST") {
        attempt += 1;
        if (attempt <= 2) throw new TypeError("Failed to fetch");
        return json(201, { submission_id: 42, state: "queued", received_at: "x" });
      }
      return problemRoutes(() => undefined)(method, path, body, headers);
    });
    const user = userEvent.setup();
    renderAt("/problems/sum-two");
    await screen.findByRole("heading", { name: "Tổng hai số" });
    await user.type(screen.getByLabelText("Mã nguồn"), "print(1)");

    await user.click(screen.getByRole("button", { name: "Nộp bài" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Không kết nối được tới máy chủ.");
    await user.click(screen.getByRole("button", { name: "Nộp bài" }));
    await waitFor(() => expect(attempt).toBe(2));
    await user.type(screen.getByLabelText("Mã nguồn"), " # sửa");
    await user.click(await screen.findByRole("button", { name: "Nộp bài" }));
    await screen.findByRole("heading", { name: /Bài nộp #42/ });

    const tokens = calls.filter((c) => c.method === "POST").map((c) => c.body.client_token);
    expect(tokens).toHaveLength(3);
    expect(tokens[0]).toBe(tokens[1]);
    expect(tokens[2]).not.toBe(tokens[0]);
  });

  it("bấm hai lần liên tiếp khi đang chờ phản hồi chỉ gửi một request", async () => {
    let release;
    const calls = installFetch((method, path, body, headers) => {
      if (path === "/api/submissions" && method === "POST") {
        return new Promise((resolve) => {
          release = () => resolve(json(201, { submission_id: 42, state: "queued", received_at: "x" }));
        });
      }
      return problemRoutes(() => undefined)(method, path, body, headers);
    });
    const user = userEvent.setup();
    renderAt("/problems/sum-two");
    await screen.findByRole("heading", { name: "Tổng hai số" });
    await user.type(screen.getByLabelText("Mã nguồn"), "print(1)");
    const button = screen.getByRole("button", { name: "Nộp bài" });
    await user.dblClick(button);
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Đang nộp…" })).toBeDisabled();
    await act(async () => release());
    await screen.findByRole("heading", { name: /Bài nộp #42/ });
  });

  it("429 hiện thời gian chờ và khóa nút", async () => {
    installFetch(problemRoutes(() => apiError(429, "RATE_LIMITED", "m", { "Retry-After": "7" })));
    const user = userEvent.setup();
    renderAt("/problems/sum-two");
    await screen.findByRole("heading", { name: "Tổng hai số" });
    await user.type(screen.getByLabelText("Mã nguồn"), "x");
    await user.click(screen.getByRole("button", { name: "Nộp bài" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Thử lại sau 7 giây");
    expect(screen.getByRole("button", { name: "Chờ 7 giây" })).toBeDisabled();
  });

  it("mã quá 64 KB (tính theo byte UTF-8) bị chặn ngay ở client", async () => {
    const calls = installFetch(problemRoutes(() => json(201, { submission_id: 42, state: "queued", received_at: "x" })));
    renderAt("/problems/sum-two");
    await screen.findByRole("heading", { name: "Tổng hai số" });
    // 22000 ký tự "ế" = 66000 byte UTF-8 nhưng chỉ 22000 ký tự: đếm theo ký tự sẽ lọt
    const area = screen.getByLabelText("Mã nguồn");
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
      setter.call(area, "ế".repeat(22000));
      area.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(screen.getByRole("button", { name: "Nộp bài" })).toBeDisabled();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });
});

describe("trang bài nộp", () => {
  const detailRoutes = (getSubmission) => (method, path) => {
    if (path === "/api/me") return json(200, { user: USER });
    if (path.startsWith("/api/submissions/")) return getSubmission();
  };

  it("poll tới khi judged rồi dừng", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const states = ["queued", "judging", "judged"];
    const calls = installFetch(
      detailRoutes(() => {
        const state = states.shift() ?? "judged";
        return json(200, submissionObject({ state, verdict: state === "judged" ? "AC" : null }));
      }),
    );
    renderAt("/submissions/42");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByTestId("state")).toHaveTextContent("Đang chờ");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(screen.getByTestId("state")).toHaveTextContent("Đang chấm");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(screen.getByTestId("state")).toHaveTextContent("Đã chấm");
    expect(screen.getByText("Chấp nhận (AC)")).toBeInTheDocument();
    const n = calls.filter((c) => c.path === "/api/submissions/42").length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(calls.filter((c) => c.path === "/api/submissions/42")).toHaveLength(n);
  });

  it("hết phiên giữa chừng (401 UNAUTHENTICATED) thì dừng poll và về trang đăng nhập", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    let expired = false;
    const calls = installFetch((method, path) => {
      if (path === "/api/me") return json(200, { user: USER });
      if (path === "/api/submissions/42") return expired ? apiError(401, "UNAUTHENTICATED") : json(200, submissionObject({ state: "judging", verdict: null }));
    });
    renderAt("/submissions/42");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByTestId("state")).toHaveTextContent("Đang chấm");
    expired = true;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(screen.getByRole("heading", { name: "Đăng nhập" })).toBeInTheDocument();
    const n = calls.filter((c) => c.path === "/api/submissions/42").length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30000);
    });
    expect(calls.filter((c) => c.path === "/api/submissions/42")).toHaveLength(n);
  });

  it("bài của người khác hoặc không tồn tại: báo không tìm thấy, không poll tiếp", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const calls = installFetch(detailRoutes(() => apiError(404, "SUBMISSION_NOT_FOUND")));
    renderAt("/submissions/9");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByRole("alert")).toHaveTextContent("Không tìm thấy bài nộp.");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(calls.filter((c) => c.path === "/api/submissions/9")).toHaveLength(1);
  });

  it("MLE không hiện số bộ nhớ; SE chỉ hiện thông báo chung; RE hiện loại lỗi", async () => {
    installFetch(detailRoutes(() => json(200, submissionObject({ verdict: "MLE", memory_kib: 262144, time_ms: 40, failed_test: 3, failed_is_sample: false }))));
    const view = renderAt("/submissions/42");
    await screen.findByText("Quá bộ nhớ (MLE)");
    expect(screen.queryByText(/Bộ nhớ:/)).not.toBeInTheDocument();
    expect(screen.getByText("Sai ở test 3")).toBeInTheDocument();
    view.unmount();

    installFetch(detailRoutes(() => json(200, submissionObject({ verdict: "SE", time_ms: null, memory_kib: null }))));
    const se = renderAt("/submissions/42");
    await screen.findByText(/Hệ thống chấm gặp sự cố/);
    se.unmount();

    installFetch(detailRoutes(() => json(200, submissionObject({ verdict: "RE", re_kind: "SIGSEGV", failed_test: 1, failed_is_sample: false, time_ms: null, memory_kib: null }))));
    renderAt("/submissions/42");
    await screen.findByText(/SIGSEGV/);
  });

  it("test mẫu sai hiện input, output mong đợi và output của chương trình", async () => {
    installFetch(
      detailRoutes(() =>
        json(200, submissionObject({ verdict: "WA", failed_test: 1, failed_is_sample: true, sample_input: "1 2\n", sample_expected_output: "3\n", sample_stdout: "4\n" })),
      ),
    );
    renderAt("/submissions/42");
    await screen.findByText("Output của chương trình");
    expect(screen.getByText("Output mong đợi")).toBeInTheDocument();
    expect(screen.getByText("4", { exact: false, selector: "pre" })).toBeInTheDocument();
  });
});

describe("chuỗi từ server luôn hiển thị như văn bản", () => {
  const PAYLOAD = '<img src=x onerror="window.__pwned=1"><script>window.__pwned=1</script>';

  it("ce_message, mã nguồn và tên đề không thành phần tử HTML", async () => {
    window.__pwned = 0;
    installFetch((method, path) => {
      if (path === "/api/me") return json(200, { user: USER });
      if (path === "/api/submissions/42")
        return json(200, submissionObject({ verdict: "CE", ce_message: PAYLOAD, code: PAYLOAD, problem_title: PAYLOAD, time_ms: null, memory_kib: null }));
    });
    const { container } = renderAt("/submissions/42");
    await screen.findByText("Thông báo biên dịch");
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(screen.getAllByText(PAYLOAD, { exact: false }).length).toBeGreaterThanOrEqual(2);
    expect(window.__pwned).toBe(0);
  });

  it("đề bài: statement và ví dụ cũng chỉ là văn bản, xuống dòng được giữ", async () => {
    installFetch((method, path) => {
      if (path === "/api/me") return json(200, { user: USER });
      if (path === "/api/problems/sum-two") return json(200, { ...PROBLEM, statement: `dòng 1\n${PAYLOAD}`, samples: [{ input: PAYLOAD, output: "x" }] });
    });
    const { container } = renderAt("/problems/sum-two");
    await screen.findByRole("heading", { name: "Tổng hai số" });
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("pre.statement").textContent).toBe(`dòng 1\n${PAYLOAD}`);
  });
});
