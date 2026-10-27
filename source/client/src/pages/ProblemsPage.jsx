import { Link } from "react-router-dom";
import { api } from "../lib/api.js";
import { errorText } from "../lib/format.js";
import { useFetch } from "../lib/useFetch.js";

export default function ProblemsPage() {
  const { data, error, loading } = useFetch((signal) => api.problems({ signal }), "problems");

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
          </tr>
        </thead>
        <tbody>
          {data.map((p) => (
            <tr key={p.id}>
              <td>{p.id}</td>
              <td>
                <Link to={`/problems/${encodeURIComponent(p.id)}`}>{p.title}</Link>
                {p.hidden && <span className="badge">ẩn</span>}
              </td>
              <td>{p.time_limit_ms}</td>
              <td>{p.memory_limit_kib}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted">Giới hạn thời gian là giá trị gốc của đề; giới hạn thực tế nhân hệ số theo ngôn ngữ.</p>
    </>
  );
}
