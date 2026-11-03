import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../App.jsx";
import { ADMIN, USER, apiError, installFetch, json } from "../test/fakeServer.js";

const account = (id, username, over = {}) => ({
  id,
  username,
  role: "user",
  disabled: false,
  created_at: "2026-11-01T08:00:00.000Z",
  ...over,
});

const ROOT = account(1, "root", { role: "admin" }); // trùng ADMIN của fakeServer (id 1)
const ALICE = account(5, "alice");
const BOB = account(6, "bob");

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
});

/** Server giả có trạng thái cho nhóm `/api/admin/users`; `handlers` ghi đè từng route để dựng lỗi. */
function usersServer({ users = [BOB, ALICE, ROOT], handlers = {} } = {}) {
  const state = { users: users.map((u) => ({ ...u })) };
  const calls = installFetch((method, path, body) => {
    if (path === "/api/me") return json(200, { user: ADMIN });
    const key = `${method} ${path.split("?")[0].replace(/\/\d+(?=\/|$)/, "/:id")}`;
    if (handlers[key]) {
      const res = handlers[key](path, body, state);
      if (res) return res;
    }
    if (key === "GET /api/admin/users") return json(200, { users: state.users, next_before_id: null });
    const id = Number(path.match(/\/users\/(\d+)/)?.[1]);
    const target = state.users.find((u) => u.id === id);
    if (key === "PATCH /api/admin/users/:id") {
      Object.assign(target, body);
      return json(200, target);
    }
    if (key === "POST /api/admin/users/:id/password") return new Response(null, { status: 204 });
    if (key === "POST /api/admin/users") {
      const created = account(100, body.username, { role: body.role ?? "user" });
      state.users.unshift(created);
      return json(201, created);
    }
  });
  return { calls, state };
}

const rowOf = (name) => screen.getByRole("cell", { name: new RegExp(`^${name}`) }).closest("tr");
const patches = (calls) => calls.filter((c) => c.method === "PATCH");

describe("quyền truy cập trang", () => {
  it("người thường không thấy link và đường dẫn giống trang không tồn tại, không gọi API admin", async () => {
    const calls = installFetch((method, path) => {
      if (path === "/api/me") return json(200, { user: USER });
      if (path === "/api/problems") return json(200, { problems: [] });
    });
    renderAt("/admin/users");
    expect(await screen.findByText("Không tìm thấy trang.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Người dùng" })).not.toBeInTheDocument();
    expect(calls.filter((c) => c.path.startsWith("/api/admin/"))).toHaveLength(0);
  });

  it("admin thấy link Người dùng", async () => {
    installFetch((method, path) => {
      if (path === "/api/me") return json(200, { user: ADMIN });
      if (path === "/api/problems") return json(200, { problems: [] });
    });
    renderAt("/");
    expect(await screen.findByRole("link", { name: "Người dùng" })).toHaveAttribute("href", "/admin/users");
  });
});

describe("danh sách", () => {
  it("hiện tài khoản, đánh dấu chính mình, không có field nào ngoài README", async () => {
    usersServer({ users: [BOB, account(5, "alice", { disabled: true }), ROOT] });
    renderAt("/admin/users");
    expect(await screen.findByRole("cell", { name: "bob" })).toBeInTheDocument();
    expect(within(rowOf("alice")).getByText("bị khóa")).toBeInTheDocument();
    expect(within(rowOf("alice")).getByRole("button", { name: "Mở khóa" })).toBeInTheDocument();
    expect(within(rowOf("root")).getByText("(bạn)")).toBeInTheDocument();
  });

  it("phân trang bằng before_id và limit, gộp trang sau vào cuối", async () => {
    const { calls } = usersServer({
      handlers: {
        "GET /api/admin/users": (path) =>
          path.includes("before_id=6")
            ? json(200, { users: [ALICE], next_before_id: null })
            : json(200, { users: [BOB], next_before_id: 6 }),
      },
    });
    const user = userEvent.setup();
    renderAt("/admin/users");
    await screen.findByRole("cell", { name: "bob" });
    expect(calls.find((c) => c.path.startsWith("/api/admin/users")).path).toBe("/api/admin/users?limit=50");
    await user.click(screen.getByRole("button", { name: "Tải thêm" }));
    await screen.findByRole("cell", { name: /^alice/ });
    expect(calls.at(-1).path).toBe("/api/admin/users?limit=50&before_id=6");
    expect(screen.getByRole("cell", { name: "bob" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Tải thêm" })).not.toBeInTheDocument();
  });

  it("lỗi tải danh sách báo lỗi", async () => {
    usersServer({ handlers: { "GET /api/admin/users": () => apiError(403, "FORBIDDEN") } });
    renderAt("/admin/users");
    expect(await screen.findByRole("alert")).toHaveTextContent("không đủ quyền");
  });
});

describe("tạo tài khoản", () => {
  const fill = async (user, name, password, role) => {
    await screen.findByRole("cell", { name: "bob" });
    await user.type(screen.getByLabelText("Tên đăng nhập"), name);
    await user.type(screen.getByLabelText("Mật khẩu"), password);
    if (role) await user.selectOptions(screen.getByLabelText("Vai trò"), role);
    await user.click(screen.getByRole("button", { name: "Tạo tài khoản" }));
  };

  it("gửi đúng body, thêm dòng mới lên đầu, xóa form", async () => {
    const { calls } = usersServer();
    const user = userEvent.setup();
    renderAt("/admin/users");
    await fill(user, "carol", "matkhau-dai", "admin");
    const created = await screen.findByRole("cell", { name: "carol" });
    expect(calls.find((c) => c.method === "POST").body).toEqual({ username: "carol", password: "matkhau-dai", role: "admin" });
    expect(created.closest("tr")).toBe(screen.getAllByRole("row")[1]); // ngay dưới hàng tiêu đề
    expect(screen.getByLabelText("Tên đăng nhập")).toHaveValue("");
    expect(screen.getByLabelText("Mật khẩu")).toHaveValue("");
  });

  it("409 USERNAME_TAKEN báo trùng tên và giữ dữ liệu đã gõ", async () => {
    usersServer({ handlers: { "POST /api/admin/users": () => apiError(409, "USERNAME_TAKEN") } });
    const user = userEvent.setup();
    renderAt("/admin/users");
    await fill(user, "alice", "matkhau-dai");
    expect(await screen.findByRole("alert")).toHaveTextContent("Tên đăng nhập đã tồn tại");
    expect(screen.getByLabelText("Tên đăng nhập")).toHaveValue("alice");
  });

  it("tên hoặc mật khẩu sai quy tắc bị chặn trước khi gửi", async () => {
    const { calls } = usersServer();
    const user = userEvent.setup();
    renderAt("/admin/users");
    await fill(user, "có dấu", "matkhau-dai");
    expect(await screen.findByRole("alert")).toHaveTextContent("Tên đăng nhập");
    await user.clear(screen.getByLabelText("Tên đăng nhập"));
    await user.type(screen.getByLabelText("Tên đăng nhập"), "carol");
    await user.clear(screen.getByLabelText("Mật khẩu"));
    await user.type(screen.getByLabelText("Mật khẩu"), "ngan");
    await user.click(screen.getByRole("button", { name: "Tạo tài khoản" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Mật khẩu");
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });
});

describe("khóa, mở khóa, đổi role", () => {
  it("khóa phải xác nhận; hủy thì không gửi gì", async () => {
    const { calls } = usersServer();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const user = userEvent.setup();
    renderAt("/admin/users");
    await screen.findByRole("cell", { name: "bob" });
    await user.click(within(rowOf("bob")).getByRole("button", { name: "Khóa" }));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(confirm.mock.calls[0][0]).toContain("bob");
    expect(patches(calls)).toHaveLength(0);
  });

  it("khóa rồi mở khóa: đúng body, mở khóa không hỏi, dòng đổi theo đối tượng server trả về", async () => {
    const { calls } = usersServer();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    renderAt("/admin/users");
    await screen.findByRole("cell", { name: "bob" });
    await user.click(within(rowOf("bob")).getByRole("button", { name: "Khóa" }));
    expect(await within(rowOf("bob")).findByText("bị khóa")).toBeInTheDocument();
    expect(patches(calls)[0]).toMatchObject({ path: "/api/admin/users/6", body: { disabled: true } });
    expect(patches(calls)[0].headers["Content-Type"]).toBe("application/json");

    await user.click(within(rowOf("bob")).getByRole("button", { name: "Mở khóa" }));
    expect(await within(rowOf("bob")).findByText("hoạt động")).toBeInTheDocument();
    expect(patches(calls)[1].body).toEqual({ disabled: false });
    expect(confirm).toHaveBeenCalledTimes(1);
  });

  it("hạ admin phải xác nhận, nâng lên admin thì không hỏi", async () => {
    const { calls } = usersServer({ users: [BOB, account(5, "alice", { role: "admin" }), ROOT] });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    renderAt("/admin/users");
    await screen.findByRole("cell", { name: "bob" });
    await user.click(within(rowOf("alice")).getByRole("button", { name: "Hạ xuống user" }));
    await waitFor(() => expect(within(rowOf("alice")).getByRole("button", { name: "Nâng lên admin" })).toBeInTheDocument());
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(patches(calls)[0].body).toEqual({ role: "user" });

    await user.click(within(rowOf("bob")).getByRole("button", { name: "Nâng lên admin" }));
    await waitFor(() => expect(within(rowOf("bob")).getByRole("button", { name: "Hạ xuống user" })).toBeInTheDocument());
    expect(patches(calls)[1].body).toEqual({ role: "admin" });
    expect(confirm).toHaveBeenCalledTimes(1);
  });

  it("409 LAST_ADMIN báo rõ và giữ nguyên dòng", async () => {
    usersServer({ users: [BOB, ROOT], handlers: { "PATCH /api/admin/users/:id": () => apiError(409, "LAST_ADMIN") } });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    renderAt("/admin/users");
    await screen.findByRole("cell", { name: "bob" });
    await user.click(within(rowOf("root")).getByRole("button", { name: "Hạ xuống user" }));
    expect(await within(rowOf("root")).findByRole("alert")).toHaveTextContent("admin hoạt động cuối cùng");
    expect(within(rowOf("root")).getByText("admin")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Đăng nhập" })).not.toBeInTheDocument(); // vẫn ở trang, không bị đăng xuất
  });

  it("404 USER_NOT_FOUND thì tải lại danh sách", async () => {
    let listed = 0;
    const { calls } = usersServer({
      handlers: {
        "GET /api/admin/users": () => {
          listed += 1;
          return json(200, { users: listed === 1 ? [BOB, ALICE, ROOT] : [ALICE, ROOT], next_before_id: null });
        },
        "PATCH /api/admin/users/:id": () => apiError(404, "USER_NOT_FOUND"),
      },
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    renderAt("/admin/users");
    await screen.findByRole("cell", { name: "bob" });
    await user.click(within(rowOf("bob")).getByRole("button", { name: "Khóa" }));
    await waitFor(() => expect(screen.queryByRole("cell", { name: "bob" })).not.toBeInTheDocument());
    expect(calls.filter((c) => c.path.startsWith("/api/admin/users?"))).toHaveLength(2);
  });

  it("lỗi mạng báo lỗi, không đổi dòng", async () => {
    usersServer({ handlers: { "PATCH /api/admin/users/:id": () => Promise.reject(new TypeError("fail")) } });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    renderAt("/admin/users");
    await screen.findByRole("cell", { name: "bob" });
    await user.click(within(rowOf("bob")).getByRole("button", { name: "Khóa" }));
    expect(await within(rowOf("bob")).findByRole("alert")).toHaveTextContent("Không kết nối được");
    expect(within(rowOf("bob")).getByText("hoạt động")).toBeInTheDocument();
  });
});

describe("đặt lại mật khẩu", () => {
  const openForm = async (user, name) => {
    await screen.findByRole("cell", { name: "bob" });
    await user.click(within(rowOf(name)).getByRole("button", { name: "Đặt lại mật khẩu" }));
    return within(rowOf(name)).getByLabelText(/Mật khẩu mới/);
  };

  it("gửi đúng body, báo thành công, đóng form và không còn mật khẩu trong giao diện", async () => {
    const { calls } = usersServer();
    const user = userEvent.setup();
    renderAt("/admin/users");
    const input = await openForm(user, "bob");
    expect(input).toHaveAttribute("type", "password");
    await user.type(input, "mat-khau-moi");
    await user.click(within(rowOf("bob")).getByRole("button", { name: "Đặt mật khẩu" }));
    expect(await within(rowOf("bob")).findByRole("status")).toHaveTextContent("Đã đặt lại mật khẩu");
    const post = calls.find((c) => c.path === "/api/admin/users/6/password");
    expect(post.method).toBe("POST");
    expect(post.body).toEqual({ password: "mat-khau-moi" });
    expect(screen.queryByLabelText(/Mật khẩu mới/)).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain("mat-khau-moi");
    await user.click(within(rowOf("bob")).getByRole("button", { name: "Đặt lại mật khẩu" }));
    expect(within(rowOf("bob")).getByLabelText(/Mật khẩu mới/)).toHaveValue(""); // mật khẩu cũ không nằm lại trong state
  });

  it("mật khẩu ngắn bị chặn trước khi gửi; hủy thì xóa mật khẩu đã gõ", async () => {
    const { calls } = usersServer();
    const user = userEvent.setup();
    renderAt("/admin/users");
    const input = await openForm(user, "bob");
    await user.type(input, "ngan");
    await user.click(within(rowOf("bob")).getByRole("button", { name: "Đặt mật khẩu" }));
    expect(await within(rowOf("bob")).findByRole("alert")).toHaveTextContent("8 đến 128");
    expect(calls.some((c) => c.path.endsWith("/password"))).toBe(false);
    await user.click(within(rowOf("bob")).getByRole("button", { name: "Hủy" }));
    await user.click(within(rowOf("bob")).getByRole("button", { name: "Đặt lại mật khẩu" }));
    expect(within(rowOf("bob")).getByLabelText(/Mật khẩu mới/)).toHaveValue("");
  });

  it("404 thì tải lại danh sách", async () => {
    let listed = 0;
    usersServer({
      handlers: {
        "GET /api/admin/users": () => {
          listed += 1;
          return json(200, { users: listed === 1 ? [BOB, ROOT] : [ROOT], next_before_id: null });
        },
        "POST /api/admin/users/:id/password": () => apiError(404, "USER_NOT_FOUND"),
      },
    });
    const user = userEvent.setup();
    renderAt("/admin/users");
    const input = await openForm(user, "bob");
    await user.type(input, "mat-khau-moi");
    await user.click(within(rowOf("bob")).getByRole("button", { name: "Đặt mật khẩu" }));
    await waitFor(() => expect(screen.queryByRole("cell", { name: "bob" })).not.toBeInTheDocument());
  });
});

describe("admin tác động lên chính mình", () => {
  const withSecondAdmin = [BOB, account(2, "dave", { role: "admin" }), ROOT];

  it("khóa mình: cảnh báo sẽ bị đăng xuất, thành công thì về trang đăng nhập", async () => {
    usersServer({ users: withSecondAdmin });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    renderAt("/admin/users");
    await screen.findByRole("cell", { name: "bob" });
    await user.click(within(rowOf("root")).getByRole("button", { name: "Khóa" }));
    expect(confirm.mock.calls[0][0]).toContain("đăng xuất");
    expect(await screen.findByRole("heading", { name: "Đăng nhập" })).toBeInTheDocument();
  });

  it("tự hạ quyền: về trang đăng nhập", async () => {
    usersServer({ users: withSecondAdmin });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    renderAt("/admin/users");
    await screen.findByRole("cell", { name: "bob" });
    await user.click(within(rowOf("root")).getByRole("button", { name: "Hạ xuống user" }));
    expect(await screen.findByRole("heading", { name: "Đăng nhập" })).toBeInTheDocument();
  });

  it("tự đặt lại mật khẩu: về trang đăng nhập", async () => {
    usersServer({ users: withSecondAdmin });
    const user = userEvent.setup();
    renderAt("/admin/users");
    await screen.findByRole("cell", { name: "bob" });
    await user.click(within(rowOf("root")).getByRole("button", { name: "Đặt lại mật khẩu" }));
    await user.type(within(rowOf("root")).getByLabelText(/Mật khẩu mới/), "mat-khau-moi");
    await user.click(within(rowOf("root")).getByRole("button", { name: "Đặt mật khẩu" }));
    expect(await screen.findByRole("heading", { name: "Đăng nhập" })).toBeInTheDocument();
  });

  it("huỷ xác nhận thì ở lại", async () => {
    usersServer({ users: withSecondAdmin });
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const user = userEvent.setup();
    renderAt("/admin/users");
    await screen.findByRole("cell", { name: "bob" });
    await user.click(within(rowOf("root")).getByRole("button", { name: "Khóa" }));
    expect(screen.queryByRole("heading", { name: "Đăng nhập" })).not.toBeInTheDocument();
  });

  it("người khác bị khóa/đổi thì KHÔNG bị đưa về đăng nhập", async () => {
    usersServer({ users: withSecondAdmin });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    renderAt("/admin/users");
    await screen.findByRole("cell", { name: "bob" });
    await user.click(within(rowOf("dave")).getByRole("button", { name: "Khóa" }));
    expect(await within(rowOf("dave")).findByText("bị khóa")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Đăng nhập" })).not.toBeInTheDocument();
  });
});
