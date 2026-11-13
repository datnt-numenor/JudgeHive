import React, { createContext, useContext, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Link, Navigate, Route, Routes, useNavigate, useParams } from "react-router-dom";
import { api } from "./api";
import "./styles.css";

const AppContext = createContext(null);
const useApp = () => useContext(AppContext);
const stateName = { queued: "Đang chờ", judging: "Đang chấm", done: "Hoàn tất", idle: "Rảnh", disconnected: "Đã ngắt" };
const date = (value) => value ? new Date(value).toLocaleString("vi-VN") : "—";

// Một hook tải REST, chạy lại khi có sự kiện dữ liệu hoặc người dùng bấm tải lại.
function useData(path) {
  const { version } = useApp();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    setError("");
    api(path).then(value => { if (active) setData(value); }).catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [path, version]);
  return { data, error };
}

function ErrorText({ message }) { return message ? <p role="alert" className="error">{message}</p> : null; }
function Verdict({ value }) { return <span className={`verdict ${value === "AC" ? "good" : ""}`}>{value || "—"}</span>; }
function Loading({ result, children }) {
  if (result.error) return <ErrorText message={result.error} />;
  return result.data === null ? <p>Đang tải…</p> : children(result.data);
}

function Login() {
  const { setUser } = useApp();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event) {
    event.preventDefault(); setBusy(true); setError("");
    const fields = new FormData(event.currentTarget);
    try { setUser(await api("/auth/login", "POST", Object.fromEntries(fields))); }
    catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }
  return <section className="login card"><span className="eyebrow">NHÓM 9 · LẬP TRÌNH MẠNG</span><h1>JudgeHive</h1>
    <p className="muted">Đăng nhập để luyện tập và theo dõi bài nộp.</p>
    <form onSubmit={submit}><label>Tên đăng nhập<input name="username" autoComplete="username" required /></label>
      <label>Mật khẩu<input type="password" name="password" autoComplete="current-password" required /></label>
      <ErrorText message={error} /><button disabled={busy}>{busy ? "Đang đăng nhập…" : "Đăng nhập"}</button></form></section>;
}

function Problems() {
  const result = useData("/problems");
  return <><h1>Kho đề bài</h1><p className="muted">Chọn một bài và gửi lời giải bằng Python, C++ hoặc Java.</p>
    <Loading result={result}>{rows => <div className="grid">{rows.map(p => <Link className="card problem" key={p.id} to={`/problems/${p.id}`}>
      <span className="eyebrow">BÀI {p.id}{p.hidden ? " · ĐANG ẨN" : ""}</span><h2>{p.title}</h2>
      <p className="muted">{p.time_limit_ms} ms · {p.memory_limit_kib / 1024} MiB</p><strong>Giải bài →</strong></Link>)}</div>}</Loading></>;
}

function ProblemDetail() {
  const { id } = useParams();
  const result = useData(`/problems/${id}`);
  const navigate = useNavigate();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event) {
    event.preventDefault(); setBusy(true); setError("");
    const fields = new FormData(event.currentTarget);
    try {
      const body = { problem_id: Number(id), language: fields.get("language"), code: fields.get("code") };
      const submitted = await api("/submissions", "POST", body);
      navigate(`/submissions/${submitted.id}`);
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }
  return <Loading result={result}>{p => <><h1>{p.title}</h1><p className="muted">{p.time_limit_ms} ms/test · {p.memory_limit_kib / 1024} MiB/test</p>
    <article className="card"><p className="statement">{p.statement}</p>{p.samples.map((sample, index) => <div className="grid" key={index}>
      <div><h3>Input mẫu {index + 1}</h3><pre>{sample.input}</pre></div><div><h3>Output mẫu</h3><pre>{sample.output}</pre></div></div>)}</article>
    <section className="card"><h2>Nộp lời giải</h2><form onSubmit={submit}><label>Ngôn ngữ<select name="language"><option value="python">Python</option><option value="cpp">C++17</option><option value="java">Java (lớp Main)</option></select></label>
      <label>Mã nguồn · tối đa 64 KiB<textarea name="code" rows="15" spellCheck="false" required /></label>
      <ErrorText message={error} /><button disabled={busy}>{busy ? "Đang gửi…" : "Nộp bài"}</button></form></section></>}</Loading>;
}

function Submissions({ admin = false }) {
  const [before, setBefore] = useState(null);
  const result = useData(`${admin ? "/admin" : ""}/submissions${before ? `?before=${before}` : ""}`);
  return <><h1>{admin ? "Tất cả bài nộp" : "Bài nộp của tôi"}</h1><Loading result={result}>{rows => <>
    <div className="table-wrap"><table><thead><tr><th>ID</th>{admin && <th>Người nộp</th>}<th>Đề</th><th>Ngôn ngữ</th><th>Trạng thái</th><th>Kết quả</th><th>Thời điểm</th></tr></thead>
      <tbody>{rows.map(s => <tr key={s.id}><td><Link to={`/submissions/${s.id}`}>#{s.id}</Link></td>{admin && <td>{s.username}</td>}<td>{s.problem_id}</td><td>{s.language}</td><td>{stateName[s.state]}</td><td><Verdict value={s.verdict} /></td><td>{date(s.submitted_at)}</td></tr>)}</tbody></table></div>
    {!rows.length && <p>Chưa có bài nộp.</p>}<div className="actions"><button onClick={() => setBefore(null)} disabled={!before}>Mới nhất</button>
    <button disabled={rows.length < 100} onClick={() => setBefore(rows.at(-1).id)}>100 bài cũ hơn</button></div></>}</Loading></>;
}

function SubmissionDetail() {
  const { id } = useParams();
  const result = useData(`/submissions/${id}`);
  return <Loading result={result}>{s => <><h1>Bài nộp #{s.id}</h1><section className="card"><div className="actions"><Verdict value={s.verdict} /><strong>{stateName[s.state]}</strong></div>
    <p>Ngôn ngữ: {s.language} · Lần giao: {s.attempt_id} · Test đã qua: {s.tests_done}</p><p>Thời gian chạy các test: {s.time_ms ?? "—"} ms</p>
    <p>{s.message}</p><p className="muted">Nộp lúc {date(s.submitted_at)}</p></section><h2>Mã nguồn</h2><pre>{s.code}</pre></>}</Loading>;
}

function Scoreboard() {
  const result = useData("/scoreboard");
  return <><h1>Bảng xếp hạng</h1><p className="muted">Ưu tiên số đề AC, sau đó thời điểm đạt số đề đó. Mỗi đề chỉ tính một lần.</p>
    <Loading result={result}>{rows => <div className="table-wrap"><table><thead><tr><th>Hạng</th><th>Người dùng</th><th>Số đề AC</th><th>Thời điểm đạt</th></tr></thead>
      <tbody>{rows.map((r, i) => <tr key={r.username}><td>{i + 1}</td><td>{r.username}</td><td>{r.solved}</td><td>{date(r.last_ac)}</td></tr>)}</tbody></table></div>}</Loading></>;
}

function Workers() {
  const result = useData("/admin/workers");
  return <><h1>Máy chấm</h1><p className="muted">Trạng thái theo thông tin server hiện có. Worker rảnh mất mạng có thể chưa được phát hiện cho đến khi giao bài.</p>
    <Loading result={result}>{data => <><p>Đang chờ: <strong>{data.queue}</strong> bài</p><div className="grid">{data.workers.map(w => <section className="card" key={w.id}><span className="eyebrow">KẾT NỐI #{w.id}</span><h2>{w.name}</h2><p>{stateName[w.state]}</p>{w.submission_id && <Link to={`/submissions/${w.submission_id}`}>Bài #{w.submission_id}</Link>}</section>)}</div></>}</Loading></>;
}

function Users() {
  const { refresh, user } = useApp();
  const result = useData("/admin/users");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function create(event) {
    event.preventDefault(); const form = event.currentTarget; setBusy(true); setError("");
    try { await api("/admin/users", "POST", Object.fromEntries(new FormData(form))); form.reset(); refresh(); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  async function edit(event, id) {
    event.preventDefault(); setError(""); setBusy(true);
    const form = event.currentTarget;
    const fields = new FormData(form);
    const body = { disabled: fields.get("disabled") === "on" };
    if (fields.get("password")) body.password = fields.get("password");
    try { await api(`/admin/users/${id}`, "PATCH", body); form.querySelector('input[type="password"]').value = ""; refresh(); }
    catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  return <><h1>Người dùng</h1><ErrorText message={error} /><form className="card" onSubmit={create}><h2>Tạo tài khoản</h2><div className="grid">
    <label>Tên đăng nhập<input name="username" pattern="[A-Za-z0-9_.\-]{1,32}" required /></label><label>Mật khẩu<input name="password" type="password" minLength="8" required autoComplete="new-password" /></label>
    <label>Vai trò<select name="role"><option value="user">USER</option><option value="admin">ADMIN</option></select></label></div><button disabled={busy}>Tạo tài khoản</button></form>
    <Loading result={result}>{rows => rows.map(u => <form className="card" key={`${u.id}-${u.disabled}`} onSubmit={e => edit(e, u.id)}><h3>{u.username} · {u.role.toUpperCase()}</h3><div className="grid">
      <label>Mật khẩu mới (bỏ trống để giữ)<input name="password" type="password" autoComplete="new-password" /></label><label className="check"><input name="disabled" type="checkbox" defaultChecked={!!u.disabled} disabled={u.id === user.id} />Khóa tài khoản</label></div><button disabled={busy}>Lưu tài khoản</button></form>)}</Loading></>;
}

const emptyProblem = { title: "", statement: "", time_limit_ms: 2000, memory_limit_kib: 262144,
  tests: [{ input: "2 3\n", output: "5\n", is_sample: true }] };

function AdminProblems() {
  const { refresh } = useApp();
  const result = useData("/problems");
  const [editing, setEditing] = useState(null);
  const [draft, setDraft] = useState(emptyProblem);
  const [testText, setTestText] = useState(JSON.stringify(emptyProblem.tests, null, 2));
  const [locked, setLocked] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  function startNew() { setEditing(null); setDraft(emptyProblem); setTestText(JSON.stringify(emptyProblem.tests, null, 2)); setLocked(false); setError(""); }
  async function load(id) {
    setError("");
    try {
      const p = await api(`/admin/problems/${id}`); setEditing(id); setDraft(p); setTestText(JSON.stringify(p.tests, null, 2)); setLocked(p.locked);
    } catch (e) { setError(e.message); }
  }
  async function hide(p) {
    setError("");
    try { await api(`/admin/problems/${p.id}`, "PATCH", { hidden: !p.hidden }); refresh(); }
    catch (e) { setError(e.message); }
  }
  async function save(event) {
    event.preventDefault(); setError(""); setBusy(true);
    try {
      const body = { title: draft.title, statement: draft.statement, time_limit_ms: Number(draft.time_limit_ms),
        memory_limit_kib: Number(draft.memory_limit_kib), tests: JSON.parse(testText) };
      await api(`/admin/problems${editing ? `/${editing}` : ""}`, editing ? "PUT" : "POST", body);
      startNew(); refresh();
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }
  const change = field => event => setDraft({ ...draft, [field]: event.target.value });
  return <><h1>Quản lý đề bài</h1><Loading result={result}>{rows => <div className="table-wrap"><table><thead><tr><th>Đề</th><th>Hiển thị</th><th>Thao tác</th></tr></thead><tbody>{rows.map(p => <tr key={p.id}><td>{p.title}</td><td>{p.hidden ? "Ẩn" : "Hiện"}</td><td className="actions"><button onClick={() => load(p.id)}>Xem / sửa</button><button onClick={() => hide(p)}>{p.hidden ? "Hiện đề" : "Ẩn đề"}</button></td></tr>)}</tbody></table></div>}</Loading>
    <form className="card" onSubmit={save}><div className="actions"><h2>{editing ? `Đề #${editing}` : "Tạo đề mới"}</h2><button type="button" onClick={startNew}>Tạo mới</button></div>
      {locked && <p className="muted">Đề đã có bài nộp nên không sửa nội dung/test. Hãy tạo đề mới.</p>}<ErrorText message={error} />
      <fieldset disabled={locked || busy}><label>Tiêu đề<input value={draft.title} onChange={change("title")} required /></label><label>Nội dung<textarea rows="5" value={draft.statement} onChange={change("statement")} required /></label><div className="grid">
      <label>Thời gian/test (ms)<input type="number" min="100" max="10000" value={draft.time_limit_ms} onChange={change("time_limit_ms")} required /></label>
      <label>Bộ nhớ/test (KiB)<input type="number" min="65536" max="524288" value={draft.memory_limit_kib} onChange={change("memory_limit_kib")} required /></label></div>
      <label>Tests JSON · tối đa 20 test, tổng 1 MiB<textarea rows="12" spellCheck="false" value={testText} onChange={e => setTestText(e.target.value)} required /></label><button>{busy ? "Đang lưu…" : "Lưu đề"}</button></fieldset></form></>;
}

function App() {
  const [user, setUser] = useState(null);
  const [ready, setReady] = useState(false);
  const [version, setVersion] = useState(0);
  const [connection, setConnection] = useState("Đang kết nối");
  const [error, setError] = useState("");
  const refresh = () => setVersion(v => v + 1);
  useEffect(() => {
    api("/auth/me").then(setUser).catch(() => {}).finally(() => setReady(true));
    const unauthorized = () => setUser(null);
    window.addEventListener("unauthorized", unauthorized);
    return () => window.removeEventListener("unauthorized", unauthorized);
  }, []);
  useEffect(() => {
    if (!user) return;
    let socket, timer, active = true;
    function connect() {
      socket = new WebSocket(`${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/ws`);
      socket.onopen = () => { if (active) { setConnection("Đã kết nối"); refresh(); } };
      socket.onmessage = () => { if (active) refresh(); };
      socket.onerror = () => socket.close();
      socket.onclose = () => {
        if (!active) return;
        setConnection("Mất kết nối · thử lại sau 3 giây");
        api("/auth/me").catch(() => {});
        timer = setTimeout(connect, 3000);
      };
    }
    connect();
    return () => { active = false; clearTimeout(timer); socket?.close(); };
  }, [user?.id]);
  async function logout() {
    try { await api("/auth/logout", "POST", {}); setUser(null); }
    catch (e) { setError(e.message); }
  }
  const context = { user, setUser, version, refresh };
  if (!ready) return <p>Đang tải…</p>;
  return <AppContext.Provider value={context}>{!user ? <Login /> : <>
    <header><Link to="/" className="brand">⬡ JudgeHive</Link><nav><Link to="/">Đề bài</Link><Link to="/submissions">Bài nộp</Link><Link to="/scoreboard">Xếp hạng</Link>
      {user.role === "admin" && <><Link to="/admin/problems">Quản lý đề</Link><Link to="/admin/users">Người dùng</Link><Link to="/admin/workers">Worker</Link><Link to="/admin/submissions">Tất cả bài</Link></>}</nav>
      <span>{user.username}</span><button onClick={logout}>Đăng xuất</button></header>
    <main><div className="statusbar"><span>{connection}</span><button onClick={() => { refresh(); api("/auth/me").catch(() => {}); }}>Tải lại dữ liệu</button></div><ErrorText message={error} />
    <Routes><Route path="/" element={<Problems />} /><Route path="/problems/:id" element={<ProblemDetail />} /><Route path="/submissions" element={<Submissions />} /><Route path="/submissions/:id" element={<SubmissionDetail />} /><Route path="/scoreboard" element={<Scoreboard />} />
      {user.role === "admin" && <><Route path="/admin/problems" element={<AdminProblems />} /><Route path="/admin/users" element={<Users />} /><Route path="/admin/workers" element={<Workers />} /><Route path="/admin/submissions" element={<Submissions admin />} /></>}
      <Route path="*" element={<Navigate to="/" replace />} /></Routes></main><footer>JudgeHive · Nhóm 9 · Hệ thống chấm code đồ án lập trình mạng</footer>
  </>}</AppContext.Provider>;
}

createRoot(document.getElementById("root")).render(<BrowserRouter><App /></BrowserRouter>);
