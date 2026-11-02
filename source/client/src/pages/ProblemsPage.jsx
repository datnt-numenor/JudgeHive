import { useState } from "react";
import { Link } from "react-router-dom";
import HideToggle from "../admin/HideToggle.jsx";
import { useAuth } from "../auth.jsx";
import { api } from "../lib/api.js";
import { errorText } from "../lib/format.js";
import { useFetch } from "../lib/useFetch.js";

function ProblemRow({ problem: p, isAdmin, onUpdated, onGone }) {
  return (
    <tr>
      <td>{p.id}</td>
      <td>
        <Link to={`/problems/${encodeURIComponent(p.id)}`}>{p.title}</Link>
        {p.hidden && <span className="badge">ẩn</span>}
      </td>
      <td>{p.time_limit_ms}</td>
      <td>{p.memory_limit_kib}</td>
      {isAdmin && (
        <td>
          <HideToggle problem={p} onUpdated={onUpdated} onGone={onGone} />
        </td>
      )}
    </tr>
  );
}

export default function ProblemsPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [reloadKey, setReloadKey] = useState(0);
  const [patched, setPatched] = useState({}); // đề do server trả về sau khi ẩn/hiện, theo id
  const { data, error, loading } = useFetch((signal) => api.problems({ signal }), `problems:${reloadKey}`);

  if (loading) return <p className="muted">Đang tải…</p>;
  if (error) return <p role="alert" className="error">{errorText(error)}</p>;
  if (data.length === 0) return <p className="muted">Chưa có đề bài nào.</p>;

  return (
    <>
      <h1>Đề bài</h1>
      <table>
        <thead>
          <tr>
            <th>Mã</th>
            <th>Tên</th>
            <th>Thời gian (ms)</th>
            <th>Bộ nhớ (KiB)</th>
            {isAdmin && <th>Quản trị</th>}
          </tr>
        </thead>
        <tbody>
          {data.map((listed) => (
            <ProblemRow
              key={listed.id}
              problem={patched[listed.id] ?? listed}
              isAdmin={isAdmin}
              onUpdated={(updated) => setPatched((old) => ({ ...old, [updated.id]: updated }))}
              onGone={() => {
                setPatched({});
                setReloadKey((n) => n + 1);
              }}
            />
          ))}
        </tbody>
      </table>
      <p className="muted">Giới hạn thời gian là giá trị gốc của đề; giới hạn thực tế nhân hệ số theo ngôn ngữ.</p>
    </>
  );
}
