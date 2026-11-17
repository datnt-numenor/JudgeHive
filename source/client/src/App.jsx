import React, { useEffect, useState } from "react";
import { Link, NavLink, Navigate, Route, Routes } from "react-router-dom";
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
                  <Route path="/admin/users" element={<AdminUsers />} />
                  <Route path="/admin/workers" element={<AdminWorkers />} />
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
