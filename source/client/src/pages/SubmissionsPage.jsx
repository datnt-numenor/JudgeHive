import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api.js";
import { errorText, formatTime, stateLabel, verdictLabel } from "../lib/format.js";

const PAGE_SIZE = 50; // mặc định của README 3.3

export default function SubmissionsPage() {
  const [items, setItems] = useState([]);
  const [next, setNext] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = async (beforeId, signal) => {
    setLoading(true);
    setError(null);
    try {
      const page = await api.submissions({ limit: PAGE_SIZE, beforeId }, { signal });
      setItems((old) => (beforeId == null ? page.submissions : [...old, ...page.submissions]));
      setNext(page.next_before_id);
    } catch (e) {
      if (e?.name !== "AbortError") setError(e);
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  };

  useEffect(() => {
    const controller = new AbortController();
    load(null, controller.signal);
    return () => controller.abort();
  }, []);

  return (
    <>
      <h1>Bài nộp của tôi</h1>
      {items.length === 0 && !loading && !error && <p className="muted">Chưa có bài nộp nào.</p>}
      {items.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>Đề</th>
              <th>Ngôn ngữ</th>
              <th>Thời điểm nộp</th>
              <th>Kết quả</th>
            </tr>
          </thead>
          <tbody>
            {items.map((s) => (
              <tr key={s.submission_id}>
                <td>
                  <Link to={`/submissions/${s.submission_id}`}>{s.submission_id}</Link>
                </td>
                <td>{s.problem_title}</td>
                <td>{s.language}</td>
                <td>{formatTime(s.received_at)}</td>
                <td>{s.state === "judged" ? verdictLabel(s.verdict) : stateLabel(s.state)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {loading && <p className="muted">Đang tải…</p>}
      {error && <p role="alert" className="error">{errorText(error)}</p>}
      {next != null && !loading && (
        <button type="button" onClick={() => load(next)}>
          Tải thêm
        </button>
      )}
    </>
  );
}
