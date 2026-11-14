import React, { createContext, useContext, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  BrowserRouter,
  Link,
  NavLink,
  Navigate,
  Route,
  Routes,
  useNavigate,
  useParams,
} from "react-router-dom";
import { api } from "./api";
import "./styles.css";

const AppContext = createContext(null);
const useApp = () => useContext(AppContext);
const stateName = { queued: "Đang chờ", judging: "Đang chấm", done: "Hoàn tất", idle: "Rảnh" };
const date = (value) => (value ? new Date(value).toLocaleString("vi-VN") : "—");

// Một hook tải REST, chạy lại khi có sự kiện dữ liệu hoặc người dùng bấm tải lại.
function useData(path) {
  const { version } = useApp();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    setError("");
    api(path)
      .then((value) => {
        if (active) setData(value);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [path, version]);
  return { data, error };
}

function ErrorText({ message }) {
  return message ? (
    <p role="alert" className="error">
      {message}
    </p>
  ) : null;
}
// Mỗi verdict một màu (CSS .v-ac, .v-wa, ...); bài chưa có kết quả hiện "—".
function Verdict({ value }) {
  return <span className={`verdict v-${(value || "none").toLowerCase()}`}>{value || "—"}</span>;
}
// Trạng thái bài nộp; "Đang chờ/Đang chấm" có hiệu ứng nhấp nháy nhẹ.
function SubmissionState({ value }) {
  return <span className={`state state-${value}`}>{stateName[value]}</span>;
}
function Loading({ result, children }) {
  if (result.error) return <ErrorText message={result.error} />;
  return result.data === null ? <p>Đang tải…</p> : children(result.data);
}

function Login() {
  const { setUser } = useApp();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const fields = new FormData(event.currentTarget);
    try {
      setUser(await api("/auth/login", "POST", Object.fromEntries(fields)));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="login card">
      <span className="eyebrow">NHÓM 9 · LẬP TRÌNH MẠNG</span>
      <h1>JudgeHive</h1>
      <p className="muted">Đăng nhập để luyện tập và theo dõi bài nộp.</p>
      <form onSubmit={submit}>
        <label>
          Tên đăng nhập
          <input name="username" autoComplete="username" required />
        </label>
        <label>
          Mật khẩu
          <input type="password" name="password" autoComplete="current-password" required />
        </label>
        <ErrorText message={error} />
        <button disabled={busy}>{busy ? "Đang đăng nhập…" : "Đăng nhập"}</button>
      </form>
    </section>
  );
}

// Trạng thái của chính người đang xem (server tính): đã AC, đã nộp nhưng chưa AC, chưa nộp.
const problemStatus = {
  solved: { icon: "✓", label: "Đã giải" },
  attempted: { icon: "○", label: "Đã thử" },
};
const percent = (rate) => (rate === null ? "—" : `${Math.round(rate * 1000) / 10}%`);
// Độ khó do server trả (easy/medium/hard); rank dùng để sắp xếp Dễ → Khó.
const difficulties = {
  easy: { label: "Dễ", rank: 0 },
  medium: { label: "Trung bình", rank: 1 },
  hard: { label: "Khó", rank: 2 },
};
function Difficulty({ value }) {
  return <span className={`difficulty difficulty-${value}`}>{difficulties[value]?.label ?? value}</span>;
}

function Problems() {
  const result = useData("/problems");
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [level, setLevel] = useState("all");
  const [sort, setSort] = useState("id");
  return (
    <>
      <h1>Kho đề bài</h1>
      <p className="muted">Chọn một bài và gửi lời giải bằng Python, C++ hoặc Java.</p>
      <Loading result={result}>
        {(rows) => {
          // Lọc và sắp xếp hoàn toàn ở client trên danh sách đã tải.
          const text = query.trim().toLowerCase();
          const shown = rows
            .filter((p) => !text || p.title.toLowerCase().includes(text) || String(p.id).startsWith(text))
            .filter((p) => status === "all" || (p.status ?? "todo") === status)
            .filter((p) => level === "all" || p.difficulty === level)
            .sort((a, b) => {
              if (sort === "rate") return (b.ac_rate ?? -1) - (a.ac_rate ?? -1) || a.id - b.id;
              if (sort === "difficulty")
                return difficulties[a.difficulty].rank - difficulties[b.difficulty].rank || a.id - b.id;
              return a.id - b.id;
            });
          const solved = rows.filter((p) => p.status === "solved").length;
          return (
            <>
              <div className="toolbar">
                <input
                  type="search"
                  placeholder="Tìm theo tên hoặc số đề"
                  aria-label="Tìm đề"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
                <select
                  aria-label="Lọc trạng thái"
                  value={status}
                  onChange={(e) => setStatus(e.target.value)}
                >
                  <option value="all">Tất cả</option>
                  <option value="solved">Đã giải</option>
                  <option value="attempted">Đã thử</option>
                  <option value="todo">Chưa làm</option>
                </select>
                <select aria-label="Lọc độ khó" value={level} onChange={(e) => setLevel(e.target.value)}>
                  <option value="all">Mọi độ khó</option>
                  <option value="easy">Dễ</option>
                  <option value="medium">Trung bình</option>
                  <option value="hard">Khó</option>
                </select>
                <select aria-label="Sắp xếp" value={sort} onChange={(e) => setSort(e.target.value)}>
                  <option value="id">Theo số đề</option>
                  <option value="rate">Theo tỉ lệ AC</option>
                  <option value="difficulty">Theo độ khó</option>
                </select>
                <span className="solved-count">
                  <span className="status-solved">✓</span> {solved}/{rows.length} đã giải
                </span>
              </div>
              <div className="table-wrap">
                <table className="problem-table">
                  <thead>
                    <tr>
                      <th className="status-col">Trạng thái</th>
                      <th>Đề</th>
                      <th>Tỉ lệ AC</th>
                      <th>Độ khó</th>
                      <th>Giới hạn</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((p) => (
                      // Cả hàng bấm được; link trong ô tên để dùng được bàn phím.
                      <tr key={p.id} className="row-link" onClick={() => navigate(`/problems/${p.id}`)}>
                        <td className="status-col" title={problemStatus[p.status]?.label ?? "Chưa làm"}>
                          <span className={`status-${p.status ?? "todo"}`}>
                            {problemStatus[p.status]?.icon ?? ""}
                          </span>
                        </td>
                        <td>
                          <Link to={`/problems/${p.id}`} onClick={(e) => e.stopPropagation()}>
                            {p.id}. {p.title}
                          </Link>
                          {p.hidden ? <span className="badge">Ẩn</span> : null}
                        </td>
                        <td>{percent(p.ac_rate)}</td>
                        <td>
                          <Difficulty value={p.difficulty} />
                        </td>
                        <td className="muted">
                          {p.time_limit_ms} ms · {p.memory_limit_kib / 1024} MiB
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {!shown.length && <p className="muted">Không có đề nào khớp bộ lọc.</p>}
            </>
          );
        }}
      </Loading>
    </>
  );
}

function ProblemDetail() {
  const { id } = useParams();
  const result = useData(`/problems/${id}`);
  const navigate = useNavigate();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const fields = new FormData(event.currentTarget);
    try {
      const body = { problem_id: Number(id), language: fields.get("language"), code: fields.get("code") };
      const submitted = await api("/submissions", "POST", body);
      navigate(`/submissions/${submitted.id}`);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Loading result={result}>
      {(p) => (
        <>
          <div className="title-row">
            <h1>{p.title}</h1>
            <Difficulty value={p.difficulty} />
          </div>
          <p className="muted">
            {p.time_limit_ms} ms/test · {p.memory_limit_kib / 1024} MiB/test
          </p>
          <article className="card">
            <p className="statement">{p.statement}</p>
            {p.samples.map((sample, index) => (
              <div className="grid" key={index}>
                <div>
                  <h3>Input mẫu {index + 1}</h3>
                  <pre>{sample.input}</pre>
                </div>
                <div>
                  <h3>Output mẫu</h3>
                  <pre>{sample.output}</pre>
                </div>
              </div>
            ))}
          </article>
          <section className="card">
            <h2>Nộp lời giải</h2>
            <form onSubmit={submit}>
              <label>
                Ngôn ngữ
                <select name="language">
                  <option value="python">Python</option>
                  <option value="cpp">C++17</option>
                  <option value="java">Java (lớp Main)</option>
                </select>
              </label>
              <label>
                Mã nguồn · tối đa 64 KiB
                <textarea name="code" rows="15" spellCheck="false" required />
              </label>
              <ErrorText message={error} />
              <button disabled={busy}>{busy ? "Đang gửi…" : "Nộp bài"}</button>
            </form>
          </section>
        </>
      )}
    </Loading>
  );
}

function Submissions({ admin = false }) {
  // Server trả 100 bài mới nhất. Tên đề lấy từ danh sách đề (không đổi API bài nộp).
  const result = useData(`${admin ? "/admin" : ""}/submissions`);
  const problems = useData("/problems");
  const titles = Object.fromEntries((problems.data ?? []).map((p) => [p.id, p.title]));
  return (
    <>
      <h1>{admin ? "Tất cả bài nộp" : "Bài nộp của tôi"}</h1>
      <Loading result={result}>
        {(rows) => (
          <>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>ID</th>
                    {admin && <th>Người nộp</th>}
                    <th>Đề</th>
                    <th>Ngôn ngữ</th>
                    <th>Trạng thái</th>
                    <th>Kết quả</th>
                    <th>Thời điểm</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((s) => (
                    <tr key={s.id}>
                      <td>
                        <Link to={`/submissions/${s.id}`}>#{s.id}</Link>
                      </td>
                      {admin && <td>{s.username}</td>}
                      <td>
                        <Link to={`/problems/${s.problem_id}`}>
                          {titles[s.problem_id]
                            ? `${s.problem_id}. ${titles[s.problem_id]}`
                            : `#${s.problem_id}`}
                        </Link>
                      </td>
                      <td>{s.language}</td>
                      <td>
                        <SubmissionState value={s.state} />
                      </td>
                      <td>
                        <Verdict value={s.verdict} />
                      </td>
                      <td>{date(s.submitted_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!rows.length && <p>Chưa có bài nộp.</p>}
          </>
        )}
      </Loading>
    </>
  );
}

function SubmissionDetail() {
  const { id } = useParams();
  const result = useData(`/submissions/${id}`);
  const problems = useData("/problems");
  const limitOf = (pid) => (problems.data ?? []).find((p) => p.id === pid)?.time_limit_ms;
  return (
    <Loading result={result}>
      {(s) => (
        <>
          <h1>Bài nộp #{s.id}</h1>
          <section className="card">
            <div className="actions">
              <Verdict value={s.verdict} />
              <SubmissionState value={s.state} />
            </div>
            <p>
              Ngôn ngữ: {s.language} · Lần giao: {s.attempt_id} · Test đã qua: {s.tests_done}
            </p>
            <p>
              Thời gian test chậm nhất:{" "}
              {/* Bài TLE bị dừng sau giới hạn, nên thời gian thật là "lớn hơn giới hạn". */}
              {s.verdict === "TLE" && limitOf(s.problem_id)
                ? `> ${limitOf(s.problem_id)} ms`
                : `${s.time_ms ?? "—"} ms`}
            </p>
            {s.verdict === "CE" ? <pre>{s.message}</pre> : <p>{s.message}</p>}
            <p className="muted">Nộp lúc {date(s.submitted_at)}</p>
          </section>
          <h2>Mã nguồn</h2>
          <pre>{s.code}</pre>
        </>
      )}
    </Loading>
  );
}

function Scoreboard() {
  const result = useData("/scoreboard");
  return (
    <>
      <h1>Bảng xếp hạng</h1>
      <p className="muted">Ưu tiên số đề AC, sau đó thời điểm đạt số đề đó. Mỗi đề chỉ tính một lần.</p>
      <Loading result={result}>
        {(rows) => (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Hạng</th>
                  <th>Người dùng</th>
                  <th>Số đề AC</th>
                  <th>Thời điểm đạt</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={r.username}>
                    <td>{i + 1}</td>
                    <td>{r.username}</td>
                    <td>{r.solved}</td>
                    <td>{date(r.last_ac)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Loading>
    </>
  );
}

function Workers() {
  const result = useData("/admin/workers");
  return (
    <>
      <h1>Máy chấm</h1>
      <p className="muted">
        Trạng thái theo thông tin server hiện có. Worker rảnh mất mạng có thể chưa được phát hiện cho đến khi
        giao bài.
      </p>
      <Loading result={result}>
        {(data) => (
          <>
            <p>
              Đang chờ: <strong>{data.queue}</strong> bài
            </p>
            <div className="grid">
              {data.workers.map((w) => (
                <section className="card" key={w.id}>
                  <span className="eyebrow">KẾT NỐI #{w.id}</span>
                  <h2>{w.name}</h2>
                  <p>{stateName[w.state]}</p>
                  {w.submission_id && (
                    <Link to={`/submissions/${w.submission_id}`}>Bài #{w.submission_id}</Link>
                  )}
                </section>
              ))}
            </div>
          </>
        )}
      </Loading>
    </>
  );
}

function Users() {
  const { refresh, user } = useApp();
  const result = useData("/admin/users");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function create(event) {
    event.preventDefault();
    const form = event.currentTarget;
    setBusy(true);
    setError("");
    try {
      await api("/admin/users", "POST", Object.fromEntries(new FormData(form)));
      form.reset();
      refresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function edit(event, id) {
    event.preventDefault();
    setError("");
    setBusy(true);
    const form = event.currentTarget;
    const fields = new FormData(form);
    const body = { disabled: fields.get("disabled") === "on" };
    if (fields.get("password")) body.password = fields.get("password");
    try {
      await api(`/admin/users/${id}`, "PATCH", body);
      form.querySelector('input[type="password"]').value = "";
      refresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <h1>Người dùng</h1>
      <ErrorText message={error} />
      <form className="card" onSubmit={create}>
        <h2>Tạo tài khoản</h2>
        <div className="grid">
          <label>
            Tên đăng nhập
            <input name="username" pattern="[A-Za-z0-9_.\-]{1,32}" required />
          </label>
          <label>
            Mật khẩu
            <input name="password" type="password" minLength="8" required autoComplete="new-password" />
          </label>
          <label>
            Vai trò
            <select name="role">
              <option value="user">USER</option>
              <option value="admin">ADMIN</option>
            </select>
          </label>
        </div>
        <button disabled={busy}>Tạo tài khoản</button>
      </form>
      {/* key gồm cả disabled để React tạo lại form khi dữ liệu đổi: defaultChecked chỉ được đọc lúc tạo. */}
      <Loading result={result}>
        {(rows) =>
          rows.map((u) => (
            <form className="card" key={`${u.id}-${u.disabled}`} onSubmit={(e) => edit(e, u.id)}>
              <h3>
                {u.username} · {u.role.toUpperCase()}
              </h3>
              <div className="grid">
                <label>
                  Mật khẩu mới (bỏ trống để giữ)
                  <input name="password" type="password" autoComplete="new-password" />
                </label>
                <label className="check">
                  <input
                    name="disabled"
                    type="checkbox"
                    defaultChecked={!!u.disabled}
                    disabled={u.id === user.id}
                  />
                  Khóa tài khoản
                </label>
              </div>
              <button disabled={busy}>Lưu tài khoản</button>
            </form>
          ))
        }
      </Loading>
    </>
  );
}

// Đề được nạp bằng lệnh `python -m server.manage problem FILE.json`; web chỉ xem và ẩn/hiện.
function AdminProblems() {
  const { refresh } = useApp();
  const result = useData("/problems");
  const [error, setError] = useState("");
  async function hide(p) {
    setError("");
    try {
      await api(`/admin/problems/${p.id}`, "PATCH", { hidden: !p.hidden });
      refresh();
    } catch (e) {
      setError(e.message);
    }
  }
  return (
    <>
      <h1>Quản lý đề bài</h1>
      <p className="muted">
        Thêm đề bằng lệnh nạp file JSON ở server (xem README). Đề đang ẩn không hiện với USER và không tính
        vào bảng xếp hạng.
      </p>
      <ErrorText message={error} />
      <Loading result={result}>
        {(rows) => (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Đề</th>
                  <th>Giới hạn</th>
                  <th>Hiển thị</th>
                  <th>Thao tác</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((p) => (
                  <tr key={p.id}>
                    <td>
                      #{p.id} {p.title}
                    </td>
                    <td>
                      {p.time_limit_ms} ms · {p.memory_limit_kib / 1024} MiB
                    </td>
                    <td>{p.hidden ? "Ẩn" : "Hiện"}</td>
                    <td className="actions">
                      <button onClick={() => hide(p)}>{p.hidden ? "Hiện đề" : "Ẩn đề"}</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Loading>
    </>
  );
}

function App() {
  const [user, setUser] = useState(null);
  const [ready, setReady] = useState(false);
  const [version, setVersion] = useState(0);
  const [connection, setConnection] = useState("Đang kết nối");
  const [error, setError] = useState("");
  const refresh = () => setVersion((v) => v + 1);
  useEffect(() => {
    api("/auth/me")
      .then(setUser)
      .catch(() => {})
      .finally(() => setReady(true));
    const unauthorized = () => setUser(null);
    window.addEventListener("unauthorized", unauthorized);
    return () => window.removeEventListener("unauthorized", unauthorized);
  }, []);
  useEffect(() => {
    if (!user) return;
    let socket,
      timer,
      active = true;
    function connect() {
      socket = new WebSocket(`${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/ws`);
      socket.onopen = () => {
        if (active) {
          setConnection("Đã kết nối");
          refresh();
        }
      };
      // Server chỉ báo "có thay đổi" (submission/scoreboard/workers_changed); mọi trang tải lại bằng REST.
      socket.onmessage = () => {
        if (active) refresh();
      };
      socket.onerror = () => socket.close();
      socket.onclose = () => {
        if (!active) return;
        setConnection("Mất kết nối · thử lại sau 3 giây");
        api("/auth/me").catch(() => {});
        timer = setTimeout(connect, 3000);
      };
    }
    connect();
    return () => {
      active = false;
      clearTimeout(timer);
      socket?.close();
    };
  }, [user?.id]);
  async function logout() {
    try {
      await api("/auth/logout", "POST", {});
      setUser(null);
    } catch (e) {
      setError(e.message);
    }
  }
  const context = { user, setUser, version, refresh };
  if (!ready) return <p>Đang tải…</p>;
  return (
    <AppContext.Provider value={context}>
      {!user ? (
        <Login />
      ) : (
        <>
          <header>
            <Link to="/" className="brand">
              ⬡ JudgeHive
            </Link>
            <nav>
              {/* NavLink tự gắn class "active" cho trang đang mở. */}
              <NavLink to="/" end>
                Đề bài
              </NavLink>
              <NavLink to="/submissions">Bài nộp</NavLink>
              <NavLink to="/scoreboard">Xếp hạng</NavLink>
              {user.role === "admin" && (
                <>
                  <NavLink to="/admin/problems">Quản lý đề</NavLink>
                  <NavLink to="/admin/users">Người dùng</NavLink>
                  <NavLink to="/admin/workers">Worker</NavLink>
                  <NavLink to="/admin/submissions">Tất cả bài</NavLink>
                </>
              )}
            </nav>
            <span>{user.username}</span>
            <button onClick={logout}>Đăng xuất</button>
          </header>
          <main>
            <div className="statusbar">
              <span>{connection}</span>
              <button
                onClick={() => {
                  refresh();
                  api("/auth/me").catch(() => {});
                }}
              >
                Tải lại dữ liệu
              </button>
            </div>
            <ErrorText message={error} />
            <Routes>
              <Route path="/" element={<Problems />} />
              <Route path="/problems/:id" element={<ProblemDetail />} />
              <Route path="/submissions" element={<Submissions />} />
              <Route path="/submissions/:id" element={<SubmissionDetail />} />
              <Route path="/scoreboard" element={<Scoreboard />} />
              {user.role === "admin" && (
                <>
                  <Route path="/admin/problems" element={<AdminProblems />} />
                  <Route path="/admin/users" element={<Users />} />
                  <Route path="/admin/workers" element={<Workers />} />
                  <Route path="/admin/submissions" element={<Submissions admin />} />
                </>
              )}
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </main>
          <footer>JudgeHive · Nhóm 9 · Hệ thống chấm code đồ án lập trình mạng</footer>
        </>
      )}
    </AppContext.Provider>
  );
}

createRoot(document.getElementById("root")).render(
  <BrowserRouter>
    <App />
  </BrowserRouter>,
);
