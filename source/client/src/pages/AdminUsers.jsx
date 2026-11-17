import React, { useState } from "react";
import { api } from "../api";
import { useApp, useData } from "../context/AppContext";
import { Loading } from "../components/Loading";
import { ErrorText } from "../components/ErrorText";

export function AdminUsers() {
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
