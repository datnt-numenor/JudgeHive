import { useState } from "react";
import { Link, Navigate, Outlet, Route, Routes, useLocation } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth.jsx";
import { RealtimeProvider, useRealtime } from "./realtime.jsx";
import { errorText } from "./lib/format.js";
import LoginPage from "./pages/LoginPage.jsx";
import ProblemDetailPage from "./pages/ProblemDetailPage.jsx";
import ProblemsPage from "./pages/ProblemsPage.jsx";
import ScoreboardPage from "./pages/ScoreboardPage.jsx";
import SubmissionDetailPage from "./pages/SubmissionDetailPage.jsx";
import SubmissionsPage from "./pages/SubmissionsPage.jsx";

function RealtimeNotice() {
  const { mode } = useRealtime();
  if (mode === "limited") {
    return (
      <p role="status" className="error">
        Bạn đang mở quá nhiều tab (tối đa 10 kết nối). Dữ liệu được cập nhật bằng cách tải lại định kỳ. Đóng bớt tab rồi
        tải lại trang để nhận cập nhật tức thời.
      </p>
    );
  }
  if (mode === "reconnecting") return <p role="status" className="muted">Mất kết nối cập nhật tức thời, đang thử lại…</p>;
  return null;
}

function Layout() {
  const { user, logout } = useAuth();
  const [error, setError] = useState("");

  const onLogout = async () => {
    setError("");
    try {
      await logout();
    } catch (e) {
      setError(errorText(e));
    }
  };

  return (
    <div className="app">
      <header className="nav">
        <Link to="/" className="brand">
          JudgeHive
        </Link>
        <Link to="/">Đề bài</Link>
        <Link to="/submissions">Bài nộp của tôi</Link>
        <Link to="/scoreboard">Bảng xếp hạng</Link>
        <span className="spacer" />
        <span>{user.username}</span>
        <button type="button" onClick={onLogout}>
          Đăng xuất
        </button>
      </header>
      {error && <p role="alert" className="error">{error}</p>}
      <RealtimeNotice />
      <main>
        <Outlet />
      </main>
    </div>
  );
}

function RequireAuth() {
  const { status } = useAuth();
  const location = useLocation();
  if (status === "loading") return <p className="muted">Đang tải…</p>;
  if (status === "anon") return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <Layout />;
}

export default function App() {
  return (
    <AuthProvider>
      <RealtimeProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route element={<RequireAuth />}>
            <Route path="/" element={<ProblemsPage />} />
            <Route path="/problems/:id" element={<ProblemDetailPage />} />
            <Route path="/submissions" element={<SubmissionsPage />} />
            <Route path="/submissions/:id" element={<SubmissionDetailPage />} />
            <Route path="/scoreboard" element={<ScoreboardPage />} />
            <Route path="*" element={<p>Không tìm thấy trang.</p>} />
          </Route>
        </Routes>
      </RealtimeProvider>
    </AuthProvider>
  );
}
