import React, { useState } from "react";
import { api } from "../api";
import { useApp } from "../context/AppContext";
import { ErrorText } from "../components/ErrorText";

export function Login() {
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
