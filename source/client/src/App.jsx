import React, { useEffect, useState } from "react";
import { Link, NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Brand } from "./components/Brand";
import { api } from "./api";
import { AppContext } from "./context/AppContext";
import { ErrorText } from "./components/ErrorText";
import { Login } from "./pages/Login";
import { Problems } from "./pages/Problems";
import { ProblemDetail } from "./pages/ProblemDetail";
import { Submissions } from "./pages/Submissions";
import { SubmissionDetail } from "./pages/SubmissionDetail";
import { Scoreboard } from "./pages/Scoreboard";
import { AdminProblems } from "./pages/AdminProblems";
import { AdminUsers } from "./pages/AdminUsers";
import { AdminWorkers } from "./pages/AdminWorkers";

export function App() {
  const { pathname } = useLocation();
  const adminPage = pathname.startsWith("/admin/");
  const solving = pathname.startsWith("/problems/");
  const pageName = adminPage ? { problems: "QUẢN LÝ ĐỀ", users: "NGƯỜI DÙNG", workers: "MÁY CHẤM", submissions: "TẤT CẢ BÀI NỘP" }[pathname.split("/")[2]] : pathname.startsWith("/scoreboard") ? "03 / XẾP HẠNG" : pathname.startsWith("/submissions") ? "02 / BÀI NỘP" : solving ? "C / AMBER CONSOLE" : "01 / ĐỀ BÀI";
  const [user, setUser] = useState(null);
  const [ready, setReady] = useState(false);
  const [version, setVersion] = useState(0);
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
        if (active) refresh();
      };
      // Server chỉ báo "có thay đổi" (submission/scoreboard/workers_changed); mọi trang tải lại bằng REST.
      socket.onmessage = () => {
        if (active) refresh();
      };
      socket.onerror = () => socket.close();
      socket.onclose = () => {
        if (!active) return;
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
              <Brand />
            </Link>
            <nav>
              {/* NavLink tự gắn class "active" cho trang đang mở. */}
              <NavLink to="/" end className={() => pathname === "/" || solving ? "active" : ""}>
                Đề bài
              </NavLink>
              <NavLink to="/submissions">Bài nộp</NavLink>
              <NavLink to="/scoreboard">Xếp hạng</NavLink>
              {user.role === "admin" && (
                <Link to="/admin/problems" className={adminPage ? "active" : ""}>Quản trị</Link>
              )}
            </nav>
            <details className="account-menu">
              <summary><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true"><circle cx="12" cy="6" r="4"/><path d="M3 23v-3a9 9 0 0 1 18 0v3"/></svg>{user.username}{user.role === "admin" ? <span className="role-badge">ADMIN</span> : <span>⌄</span>}</summary>
              <div className="account-dropdown"><button onClick={logout}>Đăng xuất</button></div>
            </details>
          </header>
          {adminPage && <nav className="admin-nav" aria-label="Quản trị">
            <NavLink to="/admin/problems">Quản lý đề</NavLink><NavLink to="/admin/users">Người dùng</NavLink><NavLink to="/admin/workers">Máy chấm</NavLink><NavLink to="/admin/submissions">Tất cả bài nộp</NavLink>
          </nav>}
          <main className={`${adminPage ? "admin-page" : ""} ${solving ? "solve-page" : ""}`}>
            <div className="breadcrumb">{solving ? <>Trang chủ <span>/</span> Bài tập <span>/</span> Đề {pathname.split("/")[2]}</> : <>judgehive <span>/</span> {pathname === "/" ? "problems" : pathname.slice(1).split("/").join(" / ")}</>}</div>
            <ErrorText message={error} />
            <Routes>
              <Route path="/" element={<Problems />} />
              <Route path="/problems/:id" element={<ProblemDetail key={pathname} />} />
              <Route path="/submissions" element={<Submissions />} />
              <Route path="/submissions/:id" element={<SubmissionDetail />} />
              <Route path="/scoreboard" element={<Scoreboard />} />
              {user.role === "admin" && (
                <>
                  <Route path="/admin/problems" element={<AdminProblems />} />
                  <Route path="/admin/users" element={<AdminUsers />} />
                  <Route path="/admin/workers" element={<AdminWorkers />} />
                  <Route path="/admin/submissions" element={<Submissions admin />} />
                </>
              )}
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </main>
          <footer><span>{adminPage ? `ADMIN / ${pageName}` : pageName}</span><button className="refresh-button" onClick={() => { refresh(); api("/auth/me").catch(() => {}); }} title="Tải lại dữ liệu">↻ <span>Tải lại</span></button></footer>
        </>
      )}
    </AppContext.Provider>
  );
}
