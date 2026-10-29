// Cập nhật tức thời qua WebSocket (README mục 4) ở mức ứng dụng: WebSocket giả + fetch giả, đồng hồ giả.
// Hành vi của máy trạng thái (mã đóng, backoff, PONG, 60 giây) đã có test riêng ở lib/realtime.test.js; ở đây kiểm cách
// các trang dùng nó. Chưa kiểm với server thật: xem danh sách test tay trong mô tả PR.
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App.jsx";
import { FakeWebSocket } from "./test/fakeWebSocket.js";
import { USER, installFetch, json, submissionObject } from "./test/fakeServer.js";

const settle = (ms = 0) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  vi.spyOn(Math, "random").mockReturnValue(0); // jitter của backoff = 0: nối lại đúng 1 giây sau khi mất
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const board = (version, rows = []) => ({ type: "SCOREBOARD", version, generated_at: "2026-10-27T08:15:30.123Z", rows });
const row = (rank, username, solved = 1, wrong = 0, last_ac_at = "2026-10-27T07:58:12.045Z") => ({ rank, username, solved, wrong, last_ac_at });
const status = (submission_id, state, extra = {}) => ({ type: "STATUS", submission_id, state, ...extra });
const listBody = (...items) => ({ submissions: items, next_before_id: null });

/** Dựng ứng dụng đã đăng nhập tại `path`. `routes` nhận đường dẫn đã bỏ query. `count(path)` đếm request GET. */
async function boot(path, routes = () => undefined) {
  const calls = installFetch((method, p, body, headers) => {
    const bare = p.split("?")[0];
    if (bare === "/api/me") return json(200, { user: USER });
    return routes(method, bare, body, headers);
  });
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
  await settle();
  return { calls, count: (p) => calls.filter((c) => c.method === "GET" && c.path.split("?")[0] === p).length };
}

const socket = () => FakeWebSocket.last;
const live = async (msg = board(1)) => {
  await act(async () => socket().receive(msg));
  await settle();
};
const send = (msg) => act(async () => socket().receive(msg));
const lose = async (code = 1006) => {
  await act(async () => socket().serverClose(code));
  await settle();
};
const reconnect = async (msg = board(1)) => {
  await settle(1000);
  await live(msg);
};

describe("một kết nối dùng chung", () => {
  it("mở một socket tới /ws cùng origin sau đăng nhập, và chuyển trang không mở thêm", async () => {
    await boot("/", (method, p) => (p === "/api/problems" ? json(200, { problems: [] }) : p === "/api/submissions" ? json(200, listBody()) : undefined));
    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(socket().url).toBe("ws://localhost:3000/ws");
    fireEvent.click(screen.getByRole("link", { name: "Bài nộp của tôi" }));
    await settle();
    fireEvent.click(screen.getByRole("link", { name: "Bảng xếp hạng" }));
    await settle();
    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(socket().closedWith).toBeNull();
  });

  it("chưa đăng nhập thì không mở socket", async () => {
    installFetch(() => undefined); // /api/me trả 404 => anon
    render(
      <MemoryRouter initialEntries={["/"]}>
        <App />
      </MemoryRouter>,
    );
    await settle();
    expect(screen.getByRole("heading", { name: "Đăng nhập" })).toBeInTheDocument();
    expect(FakeWebSocket.instances).toHaveLength(0);
  });

  it("đăng xuất đóng socket bằng 1000 và không nối lại", async () => {
    await boot("/", (method, p) => {
      if (p === "/api/logout") return new Response(null, { status: 204 });
      if (p === "/api/problems") return json(200, { problems: [] });
      return undefined;
    });
    await live();
    const ws = socket();
    fireEvent.click(screen.getByRole("button", { name: "Đăng xuất" }));
    await settle();
    expect(screen.getByRole("heading", { name: "Đăng nhập" })).toBeInTheDocument();
    expect(ws.closedWith).toBe(1000);
    ws.serverClose(4401); // server đóng 4401 đến muộn (README 4.5): không được xử lý
    await settle(120000);
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it("4401 từ server: về trang đăng nhập và không nối lại", async () => {
    await boot("/", () => json(200, { problems: [] }));
    await live();
    await lose(4401);
    expect(screen.getByRole("heading", { name: "Đăng nhập" })).toBeInTheDocument();
    await settle(120000);
    expect(FakeWebSocket.instances).toHaveLength(1);
  });
});

describe("trang chi tiết bài nộp", () => {
  const detail = (over = {}) => () => json(200, submissionObject({ state: "queued", verdict: null, time_ms: null, memory_kib: null, ...over }));
  const routes = (get) => (method, p) => (p === "/api/submissions/42" ? get() : undefined);

  it("WebSocket đang sống: không poll, STATUS cập nhật state và tiến độ", async () => {
    const { count } = await boot("/submissions/42", routes(detail()));
    await live();
    expect(count("/api/submissions/42")).toBe(1);
    expect(screen.getByTestId("state")).toHaveTextContent("Đang chờ");
    await send(status(42, "judging"));
    expect(screen.getByTestId("state")).toHaveTextContent("Đang chấm");
    await send(status(42, "judging", { progress: { tests_done: 1, tests_total: 4 } }));
    expect(screen.getByTestId("state").parentElement).toHaveTextContent("(1/4 test)");
    await settle(20000);
    expect(count("/api/submissions/42")).toBe(1); // không poll
  });

  it("STATUS của bài khác bị bỏ qua", async () => {
    const { count } = await boot("/submissions/42", routes(detail()));
    await live();
    await send(status(43, "judged", { submission: submissionObject({ submission_id: 43 }) }));
    expect(screen.getByTestId("state")).toHaveTextContent("Đang chờ");
    await settle(5000);
    expect(count("/api/submissions/42")).toBe(1);
  });

  it("STATUS judged: hiện kết quả ngay, rồi gọi REST đúng một lần để lấy mã nguồn và chi tiết", async () => {
    let judged = false;
    const { count } = await boot(
      "/submissions/42",
      routes(() => json(200, submissionObject(judged ? { code: "print(42)\n" } : { state: "queued", verdict: null, time_ms: null, memory_kib: null }))),
    );
    await live();
    judged = true;
    await send(status(42, "judged", { submission: { ...submissionObject(), code: undefined } }));
    expect(screen.getByTestId("state")).toHaveTextContent("Đã chấm");
    expect(screen.getByText("Chấp nhận (AC)")).toBeInTheDocument();
    await settle();
    expect(count("/api/submissions/42")).toBe(2);
    expect(screen.getByText(/print\(42\)/)).toBeInTheDocument();
    await settle(20000);
    expect(count("/api/submissions/42")).toBe(2);
  });

  it("STATUS đến trễ không hạ judged xuống judging/queued", async () => {
    await boot("/submissions/42", routes(() => json(200, submissionObject())));
    await live();
    expect(screen.getByTestId("state")).toHaveTextContent("Đã chấm");
    await send(status(42, "judging", { progress: { tests_done: 1, tests_total: 4 } }));
    await send(status(42, "queued"));
    expect(screen.getByTestId("state")).toHaveTextContent("Đã chấm");
  });

  it("giao lại: judging rồi queued là hợp lệ (bài chưa judged)", async () => {
    await boot("/submissions/42", routes(detail({ state: "judging" })));
    await live();
    await send(status(42, "queued"));
    expect(screen.getByTestId("state")).toHaveTextContent("Đang chờ");
  });

  it("mất WebSocket thì quay lại poll 1 giây; nối lại thì dừng poll và lấy lại đúng một lần", async () => {
    const { count } = await boot("/submissions/42", routes(detail()));
    await live();
    expect(count("/api/submissions/42")).toBe(1);
    await lose(1006);
    expect(count("/api/submissions/42")).toBe(2); // poll bắt đầu ngay khi mất kết nối
    expect(screen.getByRole("status")).toHaveTextContent("đang thử lại");
    await settle(999);
    expect(count("/api/submissions/42")).toBe(2);
    await settle(1); // đúng 1 giây: socket mới được tạo và poll tick tiếp theo
    expect(FakeWebSocket.instances).toHaveLength(2);
    expect(count("/api/submissions/42")).toBe(3);
    await live(); // SCOREBOARD của kết nối mới: poll dừng, lấy lại đúng một lần
    expect(count("/api/submissions/42")).toBe(4);
    await settle(20000);
    expect(count("/api/submissions/42")).toBe(4); // hết poll
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("4429: poll, báo quá nhiều tab, và không bao giờ mở socket thứ hai", async () => {
    const { count } = await boot("/submissions/42", routes(detail()));
    await live();
    await lose(4429);
    expect(screen.getByRole("status")).toHaveTextContent("quá nhiều tab");
    const n = count("/api/submissions/42");
    await settle(5000);
    expect(count("/api/submissions/42")).toBeGreaterThanOrEqual(n + 4);
    await settle(10 * 60 * 1000);
    expect(FakeWebSocket.instances).toHaveLength(1);
  });
});

describe("trang lịch sử bài nộp", () => {
  const rows = (...ones) => (method, p) => (p === "/api/submissions" ? json(200, listBody(...ones)) : undefined);
  const queued = (id) => submissionObject({ submission_id: id, state: "queued", verdict: null, time_ms: null, memory_kib: null });

  it("STATUS sửa đúng dòng; judged mang verdict mới", async () => {
    const { count } = await boot("/submissions", rows(queued(42), submissionObject({ submission_id: 41 })));
    await live();
    expect(screen.getByText("Đang chờ")).toBeInTheDocument();
    await send(status(42, "judging", { progress: { tests_done: 1, tests_total: 4 } }));
    expect(screen.getByText("Đang chấm")).toBeInTheDocument();
    await send(status(42, "judged", { submission: submissionObject({ submission_id: 42, verdict: "WA" }) }));
    expect(screen.getByText("Sai kết quả (WA)")).toBeInTheDocument();
    expect(count("/api/submissions")).toBe(1);
  });

  it("không hạ dòng judged xuống queued khi STATUS đến trễ", async () => {
    await boot("/submissions", rows(submissionObject({ submission_id: 42 })));
    await live();
    await send(status(42, "queued"));
    expect(screen.getByText("Chấp nhận (AC)")).toBeInTheDocument();
  });

  it("bài lạ (nộp từ tab khác) thì tải lại trang 1 một lần, gộp các STATUS dồn dập", async () => {
    let list = [queued(42)];
    const { count } = await boot("/submissions", (method, p) => (p === "/api/submissions" ? json(200, listBody(...list)) : undefined));
    await live();
    list = [queued(44), queued(43), queued(42)];
    await send(status(43, "queued"));
    await send(status(44, "queued"));
    await send(status(44, "judging"));
    expect(count("/api/submissions")).toBe(1);
    await settle(300);
    expect(count("/api/submissions")).toBe(2);
    expect(screen.getAllByText("Đang chờ")).toHaveLength(3);
  });

  it("không có WebSocket mà còn bài chưa chấm: gọi lại trang 1 mỗi 2 giây; chấm xong thì dừng", async () => {
    let list = [queued(42)];
    const { count } = await boot("/submissions", (method, p) => (p === "/api/submissions" ? json(200, listBody(...list)) : undefined));
    await live();
    await lose(1006);
    const n = count("/api/submissions");
    await settle(2000);
    expect(count("/api/submissions")).toBe(n + 1);
    await settle(2000);
    expect(count("/api/submissions")).toBe(n + 2);
    list = [submissionObject({ submission_id: 42 })];
    await settle(2000);
    expect(screen.getByText("Chấp nhận (AC)")).toBeInTheDocument();
    const done = count("/api/submissions");
    await settle(20000);
    expect(count("/api/submissions")).toBe(done);
  });

  it("sau khi nối lại: đúng một GET /api/submissions, không gọi /api/scoreboard", async () => {
    const { count } = await boot("/submissions", rows(submissionObject({ submission_id: 42 })));
    await live();
    await lose(1012);
    const before = count("/api/submissions");
    await reconnect();
    expect(count("/api/submissions")).toBe(before + 1);
    expect(count("/api/scoreboard")).toBe(0);
  });
});

describe("trang bảng xếp hạng", () => {
  // Chỉ tìm trong <main>: header cũng hiện tên người đang đăng nhập ("alice").
  const main = () => within(screen.getByRole("main"));
  it("hiện hạng, người dùng, số đề giải, số lần sai, AC gần nhất ('—' khi null); dòng của mình được làm nổi", async () => {
    const { count } = await boot("/scoreboard");
    await live(board(3, [row(1, "alice", 3, 2), row(2, "bob", 2, 0), row(3, "carol", 0, 0, null)]));
    const cells = (name) => [...main().getByText(name).closest("tr").querySelectorAll("td")].map((td) => td.textContent);
    expect(cells("alice").slice(0, 4)).toEqual(["1", "alice", "3", "2"]);
    expect(cells("carol")).toEqual(["3", "carol", "0", "0", "—"]);
    expect(cells("alice")[4]).not.toBe("—");
    expect(main().getByText("alice").closest("tr")).toHaveClass("me");
    expect(main().getByText("bob").closest("tr")).not.toHaveClass("me");
    expect(count("/api/scoreboard")).toBe(1); // chỉ lần gọi đầu, lúc WebSocket chưa tới
    await settle(30000);
    expect(count("/api/scoreboard")).toBe(1); // SCOREBOARD đã có: không gọi thêm
  });

  it("bảng rỗng", async () => {
    await boot("/scoreboard");
    await live(board(1, []));
    expect(main().getByText("Chưa có ai nộp bài.")).toBeInTheDocument();
  });

  it("chưa có bản nào: gọi REST một lần lúc đầu, rồi không poll khi WebSocket sống", async () => {
    const { count } = await boot("/scoreboard", (method, p) => (p === "/api/scoreboard" ? json(200, { ...board(5, [row(1, "alice")]), type: undefined }) : undefined));
    expect(count("/api/scoreboard")).toBe(1);
    expect(main().getByText("alice")).toBeInTheDocument();
    await settle(30000);
    expect(count("/api/scoreboard")).toBe(1);
  });

  it("bỏ bản có version nhỏ hơn hoặc bằng bản đang hiển thị", async () => {
    await boot("/scoreboard");
    await live(board(5, [row(1, "alice")]));
    await send(board(4, [row(1, "zed")]));
    await send(board(5, [row(1, "yan")]));
    expect(main().getByText("alice")).toBeInTheDocument();
    expect(main().queryByText("zed")).toBeNull();
    expect(main().queryByText("yan")).toBeNull();
    await send(board(6, [row(1, "bob")]));
    expect(main().getByText("bob")).toBeInTheDocument();
  });

  it("sau khi nối lại, mốc version bị bỏ: server khởi động lại thì version nhỏ hơn vẫn được nhận", async () => {
    await boot("/scoreboard");
    await live(board(50, [row(1, "alice")]));
    await lose(1012);
    await reconnect(board(1, [row(1, "bob")]));
    expect(main().getByText("bob")).toBeInTheDocument();
    expect(main().queryByText("alice")).toBeNull();
  });

  it("phản hồi REST phát đi trước khi nối lại (epoch cũ) bị bỏ, không chặn SCOREBOARD mới", async () => {
    let release;
    const { count } = await boot("/scoreboard", (method, p) => {
      if (p !== "/api/scoreboard") return undefined;
      return new Promise((resolve) => {
        release = () => resolve(json(200, board(99, [row(1, "stale")])));
      });
    });
    expect(count("/api/scoreboard")).toBe(1); // đang chờ phản hồi
    await live(board(1, [row(1, "fresh")]));
    await act(async () => release()); // phản hồi cũ đến muộn, version 99
    await settle();
    expect(main().getByText("fresh")).toBeInTheDocument();
    expect(main().queryByText("stale")).toBeNull();
    await send(board(2, [row(1, "next")])); // version 2 < 99 nhưng phải được nhận
    expect(main().getByText("next")).toBeInTheDocument();
  });

  it("4429: gọi REST mỗi 5 giây", async () => {
    const { count } = await boot("/scoreboard", (method, p) => (p === "/api/scoreboard" ? json(200, board(7, [row(1, "alice")])) : undefined));
    await live(board(6, [row(1, "old")]));
    const n = count("/api/scoreboard");
    await lose(4429);
    expect(count("/api/scoreboard")).toBe(n + 1);
    await settle(5000);
    expect(count("/api/scoreboard")).toBe(n + 2);
    await settle(5000);
    expect(count("/api/scoreboard")).toBe(n + 3);
    expect(main().getByText("alice")).toBeInTheDocument();
  });

  it("tên người dùng từ server hiển thị như văn bản, không thành HTML", async () => {
    window.__pwned = 0;
    const payload = '<img src=x onerror="window.__pwned=1"><script>window.__pwned=1</script>';
    await boot("/scoreboard");
    await live(board(1, [row(1, payload)]));
    expect(main().getByText(payload)).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
    expect(document.querySelector("script")).toBeNull();
    expect(window.__pwned).toBe(0);
  });
});
