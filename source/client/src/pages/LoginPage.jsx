import { useEffect, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth.jsx";
import { errorText } from "../lib/format.js";

export default function LoginPage() {
  const { status, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [countdown, setCountdown] = useState(0);

  // 429: khóa nút tới hết `Retry-After`.
  useEffect(() => {
    if (countdown <= 0) return undefined;
    const t = setTimeout(() => setCountdown((c) => c - 1), 1000);
    return () => clearTimeout(t);
  }, [countdown]);

  if (status === "user") return <Navigate to={location.state?.from ?? "/"} replace />;

  const onSubmit = async (e) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await login(username, password);
      navigate(location.state?.from ?? "/", { replace: true });
    } catch (err) {
      setError(errorText(err));
      if (err?.code === "RATE_LIMITED" && err.retryAfter) setCountdown(err.retryAfter);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="login" onSubmit={onSubmit}>
      <h1>Đăng nhập</h1>
      <label>
        Tên đăng nhập
        <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required />
      </label>
      <label>
        Mật khẩu
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
          required
        />
      </label>
      {error && <p role="alert" className="error">{error}</p>}
      <button type="submit" disabled={busy || countdown > 0}>
        {countdown > 0 ? `Chờ ${countdown} giây` : "Đăng nhập"}
      </button>
    </form>
  );
}
