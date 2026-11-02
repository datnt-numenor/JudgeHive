import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../App.jsx";
import { ADMIN, USER, adminSubmissionObject, apiError, installFetch, json, workerObject } from "../test/fakeServer.js";

const PROBLEMS = [
  { id: "sum-two", title: "Tổng hai số", time_limit_ms: 1000, memory_limit_kib: 262144, hidden: false },
  { id: "secret", title: "Đề bí mật", time_limit_ms: 1000, memory_limit_kib: 262144, hidden: true },
];

function renderAt(path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

const adminCount = (calls) => calls.filter((c) => c.path.startsWith("/api/admin/")).length;

describe("phân quyền giao diện", () => {
  it("người thường không thấy link admin, và đường dẫn admin giống một trang không tồn tại, không gọi API admin", async () => {
    const calls = installFetch((method, path) => {
      if (path === "/api/me") return json(200, { user: USER });
      if (path === "/api/problems") return json(200, { problems: PROBLEMS });
    });
    renderAt("/admin/workers");
    expect(await screen.findByText("Không tìm thấy trang.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Worker" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Bài nộp (admin)" })).not.toBeInTheDocument();
    for (const path of ["/admin/submissions", "/admin/submissions/7"]) {
      renderAt(path);
      expect((await screen.findAllByText("Không tìm thấy trang.")).length).toBeGreaterThan(0);
    }
    expect(adminCount(calls)).toBe(0);
  });

  it("admin thấy link Worker và Bài nộp (admin)", async () => {
    installFetch((method, path) => {
      if (path === "/api/me") return json(200, { user: ADMIN });
      if (path === "/api/problems") return json(200, { problems: PROBLEMS });
    });
    renderAt("/");
    expect(await screen.findByRole("link", { name: "Worker" })).toHaveAttribute("href", "/admin/workers");
    expect(screen.getByRole("link", { name: "Bài nộp (admin)" })).toHaveAttribute("href", "/admin/submissions");
  });
});

describe("nút ẩn/hiện đề", () => {
  const routes = (patch) => (method, path, body) => {
    if (path === "/api/me") return json(200, { user: ADMIN });
    if (path === "/api/problems") return json(200, { problems: PROBLEMS });
    if (method === "PATCH" && path.startsWith("/api/admin/problems/")) return patch(path, body);
  };

  it("người thường không có nút", async () => {
    installFetch((method, path) => {
      if (path === "/api/me") return json(200, { user: USER });
      if (path === "/api/problems") return json(200, { problems: [PROBLEMS[0]] });
    });
    renderAt("/");
    await screen.findByRole("link", { name: "Tổng hai số" });
    expect(screen.queryByRole("button", { name: /đề$/ })).not.toBeInTheDocument();
  });

  it("ẩn phải xác nhận; huỷ thì không gửi gì", async () => {
    const calls = installFetch(routes(() => json(200, { ...PROBLEMS[0], hidden: true })));
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const user = userEvent.setup();
    renderAt("/");
    await user.click((await screen.findAllByRole("button", { name: "Ẩn đề" }))[0]);
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(confirm.mock.calls[0][0]).toContain("Tổng hai số");
    expect(calls.some((c) => c.method === "PATCH")).toBe(false);
  });

  it("ẩn rồi hiện: gửi đúng body, không hỏi khi hiện, giao diện đổi theo đề server trả về", async () => {
    let hidden = false;
    const calls = installFetch(
      routes((path, body) => {
        hidden = body.hidden;
        return json(200, { ...PROBLEMS[0], hidden });
      }),
    );
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    renderAt("/");
    await user.click((await screen.findAllByRole("button", { name: "Ẩn đề" }))[0]);
    const row = screen.getByRole("link", { name: "Tổng hai số" }).closest("tr");
    expect(await within(row).findByRole("button", { name: "Hiện đề" })).toBeInTheDocument();
    expect(within(row).getByText("ẩn")).toBeInTheDocument();
    const patch = calls.find((c) => c.method === "PATCH");
    expect(patch.path).toBe("/api/admin/problems/sum-two");
    expect(patch.body).toEqual({ hidden: true });
    expect(patch.headers["Content-Type"]).toBe("application/json");

    await user.click(within(row).getByRole("button", { name: "Hiện đề" }));
    expect(await within(row).findByRole("button", { name: "Ẩn đề" })).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "PATCH").at(-1).body).toEqual({ hidden: false });
    expect(confirm).toHaveBeenCalledTimes(1); // chỉ lần ẩn
  });

  it("403 báo không đủ quyền và giữ nguyên trạng thái; nút dùng lại được", async () => {
    installFetch(routes(() => apiError(403, "FORBIDDEN")));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    renderAt("/");
    await user.click((await screen.findAllByRole("button", { name: "Ẩn đề" }))[0]);
    expect(await screen.findByRole("alert")).toHaveTextContent("không đủ quyền");
    expect(screen.getAllByRole("button", { name: "Ẩn đề" })[0]).toBeEnabled();
  });

  it("404 (đề đã mất) thì tải lại danh sách", async () => {
    let listed = 0;
    const calls = installFetch((method, path) => {
      if (path === "/api/me") return json(200, { user: ADMIN });
      if (path === "/api/problems") {
        listed += 1;
        return json(200, { problems: listed === 1 ? PROBLEMS : [PROBLEMS[1]] });
      }
      if (method === "PATCH") return apiError(404, "PROBLEM_NOT_FOUND");
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    renderAt("/");
    await user.click((await screen.findAllByRole("button", { name: "Ẩn đề" }))[0]);
    await waitFor(() => expect(screen.queryByRole("link", { name: "Tổng hai số" })).not.toBeInTheDocument());
    expect(calls.filter((c) => c.path === "/api/problems")).toHaveLength(2);
  });

  it("lỗi mạng báo lỗi, không đổi trạng thái", async () => {
    installFetch(routes(() => Promise.reject(new TypeError("fail"))));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    renderAt("/");
    await user.click((await screen.findAllByRole("button", { name: "Ẩn đề" }))[0]);
    expect(await screen.findByRole("alert")).toHaveTextContent("Không kết nối được");
    expect(screen.getAllByRole("button", { name: "Ẩn đề" })).toHaveLength(1);
  });
});

describe("trang worker", () => {
  const body = (over = {}) => ({
    workers: [
      workerObject({ worker_id: 3, name: "<b>evil</b>" }),
      workerObject({
        worker_id: 4,
        name: "w2",
        state: "busy",
        job: { submission_id: 105, attempt_id: 2, confirmed: true, tests_done: 4, tests_total: 10, is_rejudge: false },
      }),
      workerObject({
        worker_id: 5,
        name: "w3",
        state: "busy",
        job: { submission_id: 106, attempt_id: 1, confirmed: false, tests_done: null, tests_total: 3, is_rejudge: true },
      }),
    ],
    queue: { queued: 7, rejudge_queued: 120, judging: 2 },
    ...over,
  });

  it("hiện worker, hàng đợi, tiến độ; tên worker là văn bản thường", async () => {
    installFetch((method, path) => {
      if (path === "/api/me") return json(200, { user: ADMIN });
      if (path === "/api/admin/workers") return json(200, body());
    });
    renderAt("/admin/workers");
    expect(await screen.findByText("<b>evil</b>")).toBeInTheDocument();
    expect(document.querySelector("b")).toBeNull();
    expect(screen.getByTestId("queue")).toHaveTextContent("7 bài mới đang chờ");
    expect(screen.getByTestId("queue")).toHaveTextContent("120 bài chờ chấm lại");
    expect(screen.getByTestId("queue")).toHaveTextContent("2 bài đang chấm");
    expect(screen.getByText(/4\/10 test/)).toBeInTheDocument();
    expect(screen.getByText(/chưa báo tiến độ/)).toBeInTheDocument();
    expect(screen.getByText(/chưa xác nhận/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "#105" })).toHaveAttribute("href", "/admin/submissions/105");
  });

  it("không có worker thì báo rõ", async () => {
    installFetch((method, path) => {
      if (path === "/api/me") return json(200, { user: ADMIN });
      if (path === "/api/admin/workers") return json(200, body({ workers: [] }));
    });
    renderAt("/admin/workers");
    expect(await screen.findByText("Không có worker nào đang kết nối.")).toBeInTheDocument();
  });

  it("gọi lại mỗi 2 giây, cập nhật nội dung, và dừng khi rời trang", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    let queued = 1;
    const calls = installFetch((method, path) => {
      if (path === "/api/me") return json(200, { user: ADMIN });
      if (path === "/api/admin/workers") return json(200, body({ queue: { queued: queued++, rejudge_queued: 0, judging: 0 } }));
      if (path === "/api/problems") return json(200, { problems: [] });
    });
    renderAt("/admin/workers");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByTestId("queue")).toHaveTextContent("1 bài mới đang chờ");
    const n = () => calls.filter((c) => c.path === "/api/admin/workers").length;
    expect(n()).toBe(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1999);
    });
    expect(n()).toBe(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(n()).toBe(2);
    expect(screen.getByTestId("queue")).toHaveTextContent("2 bài mới đang chờ");

    // rời trang qua link Đề bài: không còn gọi nữa
    await act(async () => {
      screen.getByRole("link", { name: "Đề bài" }).click();
      await vi.advanceTimersByTimeAsync(0);
    });
    const after = n();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(n()).toBe(after);
  });

  it("403 báo không đủ quyền và dừng hẳn", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const calls = installFetch((method, path) => {
      if (path === "/api/me") return json(200, { user: ADMIN });
      if (path === "/api/admin/workers") return apiError(403, "FORBIDDEN");
    });
    renderAt("/admin/workers");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByRole("alert")).toHaveTextContent("không đủ quyền");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(calls.filter((c) => c.path === "/api/admin/workers")).toHaveLength(1);
  });

  it("lỗi mạng tạm thời: báo đang thử lại, vẫn giữ dữ liệu cũ", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    let n = 0;
    installFetch((method, path) => {
      if (path === "/api/me") return json(200, { user: ADMIN });
      if (path === "/api/admin/workers") {
        n += 1;
        return n === 2 ? Promise.reject(new TypeError("down")) : json(200, body());
      }
    });
    renderAt("/admin/workers");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(screen.getByText("Mất kết nối tạm thời, đang thử lại…")).toBeInTheDocument();
    expect(screen.getByTestId("queue")).toBeInTheDocument();
  });
});

describe("danh sách bài nộp admin", () => {
  const row = (id, over = {}) => {
    const { code, ce_message, sample_stdout, sample_input, sample_expected_output, ...list } = adminSubmissionObject({
      submission_id: id,
      ...over,
    });
    return list;
  };

  const listRoutes = (handler) => (method, path) => {
    if (path === "/api/me") return json(200, { user: ADMIN });
    if (path.startsWith("/api/admin/submissions") && !path.startsWith("/api/admin/submissions/")) return handler(path);
  };

  it("tải khi mở với limit=50, hiện người nộp, lần giao và cờ rejudge, link sang chi tiết", async () => {
    const calls = installFetch(
      listRoutes(() =>
        json(200, {
          submissions: [
            row(9, { username: "bob", user_id: 6, delivery_count: 1, total_deliveries: 3, is_rejudge: true, rejudge_failed: true }),
            row(8),
          ],
          next_before_id: null,
        }),
      ),
    );
    renderAt("/admin/submissions");
    const link = await screen.findByRole("link", { name: "9" });
    expect(link).toHaveAttribute("href", "/admin/submissions/9");
    const tr = link.closest("tr");
    expect(tr).toHaveTextContent("bob");
    expect(tr).toHaveTextContent("1 / 3");
    expect(tr).toHaveTextContent("đang chấm lại");
    expect(tr).toHaveTextContent("lần chấm lại gần nhất lỗi");
    expect(calls.find((c) => c.path.startsWith("/api/admin/submissions")).path).toBe("/api/admin/submissions?limit=50");
    expect(screen.queryByRole("button", { name: "Tải thêm" })).not.toBeInTheDocument();
  });

  it("không có bài nào thì báo rõ", async () => {
    installFetch(listRoutes(() => json(200, { submissions: [], next_before_id: null })));
    renderAt("/admin/submissions");
    expect(await screen.findByText("Không có bài nộp nào khớp.")).toBeInTheDocument();
  });

  it("phân trang: Tải thêm dùng before_id và nối tiếp danh sách", async () => {
    const calls = installFetch(
      listRoutes((path) =>
        path.includes("before_id=8")
          ? json(200, { submissions: [row(7)], next_before_id: null })
          : json(200, { submissions: [row(9), row(8)], next_before_id: 8 }),
      ),
    );
    const user = userEvent.setup();
    renderAt("/admin/submissions");
    await user.click(await screen.findByRole("button", { name: "Tải thêm" }));
    expect(await screen.findByRole("link", { name: "7" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "9" })).toBeInTheDocument();
    expect(calls.at(-1).path).toBe("/api/admin/submissions?before_id=8&limit=50");
    expect(screen.queryByRole("button", { name: "Tải thêm" })).not.toBeInTheDocument();
  });

  it("lọc: gửi đúng tham số, chỉ gửi tham số có giá trị, và bắt đầu lại từ trang 1", async () => {
    const calls = installFetch(
      listRoutes((path) => {
        if (path.includes("state=judged")) return json(200, { submissions: [row(5)], next_before_id: null });
        if (path.includes("before_id=8")) return json(200, { submissions: [row(7)], next_before_id: null });
        return json(200, { submissions: [row(9), row(8)], next_before_id: 8 });
      }),
    );
    const user = userEvent.setup();
    renderAt("/admin/submissions");
    await user.click(await screen.findByRole("button", { name: "Tải thêm" }));
    await screen.findByRole("link", { name: "7" }); // trang 2 đã nối vào danh sách trước khi đổi bộ lọc
    await user.selectOptions(screen.getByLabelText("Trạng thái"), "judged");
    await user.type(screen.getByLabelText("Mã đề"), "  sum-two ");
    await user.type(screen.getByLabelText("Mã người dùng"), "5");
    await user.click(screen.getByRole("button", { name: "Lọc" }));
    expect(await screen.findByRole("link", { name: "5" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "9" })).not.toBeInTheDocument();
    expect(calls.at(-1).path).toBe("/api/admin/submissions?state=judged&problem_id=sum-two&user_id=5&limit=50");
    expect(calls.at(-1).path).not.toContain("before_id");
  });

  it("mã người dùng không phải số nguyên dương: báo ngay, không gọi API", async () => {
    const calls = installFetch(listRoutes(() => json(200, { submissions: [], next_before_id: null })));
    const user = userEvent.setup();
    renderAt("/admin/submissions");
    await screen.findByText("Không có bài nộp nào khớp.");
    const before = calls.length;
    for (const bad of ["abc", "0", "-1", "1.5", "012"]) {
      await user.clear(screen.getByLabelText("Mã người dùng"));
      await user.type(screen.getByLabelText("Mã người dùng"), bad);
      await user.click(screen.getByRole("button", { name: "Lọc" }));
      expect(screen.getByRole("alert")).toHaveTextContent("Mã người dùng phải là số nguyên dương.");
    }
    expect(calls).toHaveLength(before);
  });

  it("Tải lại gọi lại bộ lọc đang dùng (không phải bộ lọc đang gõ dở)", async () => {
    const calls = installFetch(listRoutes(() => json(200, { submissions: [row(9)], next_before_id: null })));
    const user = userEvent.setup();
    renderAt("/admin/submissions");
    await screen.findByRole("link", { name: "9" });
    await user.selectOptions(screen.getByLabelText("Trạng thái"), "queued"); // chưa bấm Lọc
    await user.click(screen.getByRole("button", { name: "Tải lại" }));
    await waitFor(() => expect(calls.filter((c) => c.path.startsWith("/api/admin/submissions"))).toHaveLength(2));
    expect(calls.at(-1).path).toBe("/api/admin/submissions?limit=50");
  });

  it("không tự làm mới", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const calls = installFetch(listRoutes(() => json(200, { submissions: [row(9)], next_before_id: null })));
    renderAt("/admin/submissions");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60000);
    });
    expect(calls.filter((c) => c.path.startsWith("/api/admin/submissions"))).toHaveLength(1);
  });

  it("400 từ server (ví dụ mã đề sai) hiện thông báo và không giữ danh sách cũ", async () => {
    installFetch(
      listRoutes((path) =>
        path.includes("problem_id") ? apiError(400, "INVALID_REQUEST") : json(200, { submissions: [row(9)], next_before_id: null }),
      ),
    );
    const user = userEvent.setup();
    renderAt("/admin/submissions");
    await screen.findByRole("link", { name: "9" });
    await user.type(screen.getByLabelText("Mã đề"), "BAD ID");
    await user.click(screen.getByRole("button", { name: "Lọc" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Dữ liệu không hợp lệ.");
    expect(screen.queryByRole("link", { name: "9" })).not.toBeInTheDocument();
  });
});

describe("chi tiết bài nộp admin", () => {
  const detailRoutes = (handler) => (method, path) => {
    if (path === "/api/me") return json(200, { user: ADMIN });
    if (path.startsWith("/api/admin/submissions/")) return handler(path);
  };

  it("hiện người nộp, trường vận hành, kết quả và mã nguồn của người khác", async () => {
    const calls = installFetch(
      detailRoutes(() =>
        json(
          200,
          adminSubmissionObject({
            submission_id: 42,
            username: "bob",
            user_id: 6,
            code: "print('xin chào')\n",
            attempt_id: 2,
            delivery_count: 1,
            total_deliveries: 3,
            is_rejudge: true,
            rejudge_failed: true,
          }),
        ),
      ),
    );
    renderAt("/admin/submissions/42");
    expect(await screen.findByRole("heading", { name: /Bài nộp #42/ })).toBeInTheDocument();
    expect(calls.find((c) => c.path.startsWith("/api/admin/")).path).toBe("/api/admin/submissions/42");
    expect(screen.getByText(/bob \(#6\)/)).toBeInTheDocument();
    expect(screen.getByTestId("ops")).toHaveTextContent("Lần giao hiện tại 2");
    expect(screen.getByTestId("ops")).toHaveTextContent("đã xác nhận bị cắt 1");
    expect(screen.getByTestId("ops")).toHaveTextContent("tổng lần giao 3");
    expect(screen.getByTestId("ops")).toHaveTextContent("đang chấm lại");
    expect(screen.getByTestId("ops")).toHaveTextContent("lần chấm lại gần nhất lỗi");
    expect(screen.getByText("Chấp nhận (AC)")).toBeInTheDocument();
    expect(screen.getByText("print('xin chào')")).toBeInTheDocument();
  });

  it("code, ce_message, sample_* và tên người nộp chỉ là văn bản, không thành phần tử HTML", async () => {
    installFetch(
      detailRoutes(() =>
        json(
          200,
          adminSubmissionObject({
            username: "<i>x</i>",
            problem_title: "<u>đề</u>",
            code: '<script>alert("x")</script><img src=x onerror=alert(1)>',
            verdict: "WA",
            failed_test: 1,
            failed_is_sample: true,
            sample_input: "<b>in</b>",
            sample_expected_output: "<b>out</b>",
            sample_stdout: "<b>got</b>",
          }),
        ),
      ),
    );
    renderAt("/admin/submissions/42");
    expect(await screen.findByText(/<script>alert\("x"\)<\/script>/)).toBeInTheDocument();
    for (const tag of ["script", "img", "b", "i", "u"]) expect(document.querySelector(`main ${tag}`)).toBeNull();
    expect(screen.getByText("<b>got</b>")).toBeInTheDocument();
  });

  it("CE hiện thông báo biên dịch", async () => {
    installFetch(
      detailRoutes(() => json(200, adminSubmissionObject({ verdict: "CE", ce_message: "Main.cpp:1: error: boom", time_ms: null }))),
    );
    renderAt("/admin/submissions/42");
    expect(await screen.findByText("Main.cpp:1: error: boom")).toBeInTheDocument();
  });

  it("404 báo không tìm thấy bài nộp", async () => {
    installFetch(detailRoutes(() => apiError(404, "SUBMISSION_NOT_FOUND")));
    renderAt("/admin/submissions/404");
    expect(await screen.findByRole("alert")).toHaveTextContent("Không tìm thấy bài nộp.");
  });

  it("403 báo không đủ quyền", async () => {
    installFetch(detailRoutes(() => apiError(403, "FORBIDDEN")));
    renderAt("/admin/submissions/5");
    expect(await screen.findByRole("alert")).toHaveTextContent("không đủ quyền");
  });

  it("Tải lại gọi lại endpoint và cập nhật trạng thái; không tự làm mới", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const states = ["queued", "judged"];
    const calls = installFetch(
      detailRoutes(() => {
        const state = states.shift() ?? "judged";
        return json(200, adminSubmissionObject({ state, verdict: state === "judged" ? "AC" : null }));
      }),
    );
    renderAt("/admin/submissions/42");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByTestId("state")).toHaveTextContent("Đang chờ");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30000);
    });
    expect(calls.filter((c) => c.path.startsWith("/api/admin/submissions/"))).toHaveLength(1);
    await act(async () => {
      screen.getByRole("button", { name: "Tải lại" }).click();
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByTestId("state")).toHaveTextContent("Đã chấm");
  });
});
