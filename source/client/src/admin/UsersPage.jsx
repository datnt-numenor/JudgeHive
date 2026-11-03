import { useEffect, useRef, useState } from "react";
import { useAuth } from "../auth.jsx";
import { api } from "../lib/api.js";
import { errorText, formatTime } from "../lib/format.js";

const PAGE_SIZE = 50; // mặc định của README 3.6
const USERNAME_RE = /^[A-Za-z0-9_.-]{1,32}$/; // README 3.6; chỉ chặn sớm, server mới là nơi quyết định
const MIN_PASSWORD_BYTES = 8;
const MAX_PASSWORD_BYTES = 128;

/** Lý do mật khẩu không hợp lệ theo README 3.6 (8 đến 128 byte UTF-8, không NUL), hoặc "" nếu ổn. */
function passwordProblem(password) {
  const bytes = new TextEncoder().encode(password).length;
  if (password.includes("\u0000") || bytes < MIN_PASSWORD_BYTES || bytes > MAX_PASSWORD_BYTES) {
    return `Mật khẩu phải dài từ ${MIN_PASSWORD_BYTES} đến ${MAX_PASSWORD_BYTES} byte và không chứa ký tự NUL.`;
  }
  return "";
}

function CreateUserForm({ onCreated }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("user");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event) => {
    event.preventDefault();
    if (!USERNAME_RE.test(username)) {
      setError("Tên đăng nhập gồm 1 đến 32 ký tự trong A-Z a-z 0-9 _ . -");
      return;
    }
    const problem = passwordProblem(password);
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    setError("");
    try {
      const created = await api.createUser({ username, password, role });
      setUsername("");
      setPassword("");
      setRole("user");
      onCreated(created);
    } catch (e) {
      setError(errorText(e));
      setPassword(""); // tạo lỗi thì không để mật khẩu nằm lại trong ô; tên đăng nhập và vai trò giữ để sửa rồi gửi lại
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="filters">
      <label>
        Tên đăng nhập
        <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="off" />
      </label>{" "}
      <label>
        Mật khẩu
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" />
      </label>{" "}
      <label>
        Vai trò
        <select value={role} onChange={(e) => setRole(e.target.value)}>
          <option value="user">user</option>
          <option value="admin">admin</option>
        </select>
      </label>{" "}
      <button type="submit" disabled={busy}>
        Tạo tài khoản
      </button>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
    </form>
  );
}

/**
 * Một dòng tài khoản. Không cập nhật lạc quan: dòng chỉ đổi theo đối tượng server trả về.
 * `onUpdated(user)`: tài khoản mới từ server. `onGone()`: tài khoản không còn (404), người gọi nên tải lại danh sách.
 * `onSelfEnded()`: admin vừa tác động lên chính mình và server đã xóa phiên của họ.
 */
function UserRow({ user, isSelf, onUpdated, onGone, onSelfEnded }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState("");
  const [resetting, setResetting] = useState(false);
  const [newPassword, setNewPassword] = useState("");
  const [passwordError, setPasswordError] = useState("");

  const finish = (e) => {
    if (e?.code === "USER_NOT_FOUND") onGone();
    else setError(e);
  };

  const change = async (changes, confirmText) => {
    if (confirmText && !window.confirm(confirmText)) return;
    setBusy(true);
    setError(null);
    setNotice("");
    try {
      const updated = await api.updateUser(user.id, changes);
      if (isSelf) onSelfEnded();
      else onUpdated(updated);
    } catch (e) {
      finish(e);
    } finally {
      setBusy(false);
    }
  };

  const signOutWarning = isSelf
    ? " Đây là tài khoản của chính bạn: bạn sẽ bị đăng xuất."
    : " Mọi phiên đăng nhập của họ sẽ bị đăng xuất.";

  const toggleDisabled = () =>
    user.disabled
      ? change({ disabled: false })
      : change({ disabled: true }, `Khóa tài khoản "${user.username}"? Họ không đăng nhập được nữa.${signOutWarning}`);

  const toggleRole = () =>
    user.role === "admin"
      ? change({ role: "user" }, `Hạ "${user.username}" từ admin xuống user?${signOutWarning}`)
      : change({ role: "admin" });

  const submitPassword = async (event) => {
    event.preventDefault();
    const problem = passwordProblem(newPassword);
    if (problem) {
      setPasswordError(problem);
      return;
    }
    if (isSelf && !window.confirm("Đặt lại mật khẩu của chính bạn? Bạn sẽ bị đăng xuất khỏi mọi phiên.")) return;
    setBusy(true);
    setError(null);
    setPasswordError("");
    try {
      await api.resetUserPassword(user.id, newPassword);
      setNewPassword("");
      setResetting(false);
      if (isSelf) onSelfEnded();
      else setNotice("Đã đặt lại mật khẩu. Mọi phiên đăng nhập của họ đã bị đăng xuất.");
    } catch (e) {
      if (e?.code === "INVALID_REQUEST") setPasswordError(errorText(e));
      else finish(e);
    } finally {
      setBusy(false);
    }
  };

  const cancelReset = () => {
    setResetting(false);
    setNewPassword("");
    setPasswordError("");
  };

  return (
    <tr>
      <td>{user.id}</td>
      <td>
        {user.username}
        {isSelf && <span className="muted"> (bạn)</span>}
      </td>
      <td>{user.role}</td>
      <td>{user.disabled ? "bị khóa" : "hoạt động"}</td>
      <td>{formatTime(user.created_at)}</td>
      <td>
        <button type="button" onClick={toggleDisabled} disabled={busy}>
          {user.disabled ? "Mở khóa" : "Khóa"}
        </button>{" "}
        <button type="button" onClick={toggleRole} disabled={busy}>
          {user.role === "admin" ? "Hạ xuống user" : "Nâng lên admin"}
        </button>{" "}
        {!resetting && (
          <button type="button" onClick={() => setResetting(true)} disabled={busy}>
            Đặt lại mật khẩu
          </button>
        )}
        {resetting && (
          <form onSubmit={submitPassword}>
            <label>
              Mật khẩu mới cho {user.username}
              <input
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                autoComplete="new-password"
                autoFocus
              />
            </label>{" "}
            <button type="submit" disabled={busy}>
              Đặt mật khẩu
            </button>{" "}
            <button type="button" onClick={cancelReset} disabled={busy}>
              Hủy
            </button>
            {passwordError && (
              <p role="alert" className="error">
                {passwordError}
              </p>
            )}
          </form>
        )}
        {notice && <p role="status">{notice}</p>}
        {error && (
          <p role="alert" className="error">
            {errorText(error)}
          </p>
        )}
      </td>
    </tr>
  );
}

/**
 * Quản lý tài khoản (README 3.6, `/api/admin/users`). Không tự làm mới: chỉ tải khi mở, sau khi gặp 404 và khi bấm
 * "Tải lại". Admin tác động lên chính mình (khóa, hạ quyền, đặt lại mật khẩu) thì server xóa phiên của họ: về đăng nhập.
 */
export default function UsersPage() {
  const { user: me, expire } = useAuth();
  const [items, setItems] = useState([]);
  const [next, setNext] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const generation = useRef(0); // kết quả của lần gọi cũ (đã bấm Tải lại hoặc rời trang) bị bỏ

  const load = async (beforeId) => {
    const mine = ++generation.current;
    setLoading(true);
    setError(null);
    try {
      const page = await api.adminUsers({ beforeId, limit: PAGE_SIZE });
      if (mine !== generation.current) return;
      setItems((old) => (beforeId == null ? page.users : [...old, ...page.users]));
      setNext(page.next_before_id);
    } catch (e) {
      if (mine === generation.current) {
        if (beforeId == null) setItems([]);
        setNext(null);
        setError(e);
      }
    } finally {
      if (mine === generation.current) setLoading(false);
    }
  };

  useEffect(() => {
    load(null);
    return () => {
      generation.current += 1;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- chỉ tải khi mở trang
  }, []);

  const replace = (updated) => setItems((old) => old.map((u) => (u.id === updated.id ? updated : u)));
  // Tài khoản mới có id lớn nhất nên đứng đầu danh sách (id giảm dần).
  const prepend = (created) => setItems((old) => [created, ...old.filter((u) => u.id !== created.id)]);

  return (
    <>
      <h1>Người dùng</h1>
      <CreateUserForm onCreated={prepend} />
      <p>
        <button type="button" onClick={() => load(null)} disabled={loading}>
          Tải lại
        </button>
      </p>
      {items.length === 0 && !loading && !error && <p className="muted">Chưa có tài khoản nào.</p>}
      {items.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>Tên đăng nhập</th>
              <th>Vai trò</th>
              <th>Trạng thái</th>
              <th>Tạo lúc</th>
              <th>Thao tác</th>
            </tr>
          </thead>
          <tbody>
            {items.map((u) => (
              <UserRow
                key={u.id}
                user={u}
                isSelf={u.id === me.id}
                onUpdated={replace}
                onGone={() => load(null)}
                onSelfEnded={expire}
              />
            ))}
          </tbody>
        </table>
      )}
      {loading && <p className="muted">Đang tải…</p>}
      {error && (
        <p role="alert" className="error">
          {errorText(error)}
        </p>
      )}
      {next != null && !loading && (
        <button type="button" onClick={() => load(next)}>
          Tải thêm
        </button>
      )}
    </>
  );
}
