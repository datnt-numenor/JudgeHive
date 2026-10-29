import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api.js";
import { errorText, formatTime, stateLabel, verdictLabel } from "../lib/format.js";
import { startPolling } from "../lib/poll.js";
import { isPollingMode, useRealtime, useStatusEvents } from "../realtime.jsx";

const PAGE_SIZE = 50; // mặc định của README 3.3
const FALLBACK_POLL_MS = 2000; // [ĐỀ XUẤT]: khoảng gọi lại trang 1 khi không có WebSocket mà còn bài chưa chấm xong
const RELOAD_DEBOUNCE_MS = 300;

/** Trang 1 mới đè lên danh sách đang có (REST là nguồn sự thật), giữ các trang cũ đã tải thêm; mới nhất lên đầu. */
function mergeFirstPage(old, first) {
  const byId = new Map(old.map((s) => [s.submission_id, s]));
  for (const s of first) byId.set(s.submission_id, s);
  return [...byId.values()].sort((a, b) => b.submission_id - a.submission_id);
}

/** Áp `STATUS` lên một dòng. Không hạ `judged` xuống `queued`/`judging` (README 4.8: thứ tự có thể lệch chốc lát). */
function applyStatus(row, msg) {
  if (row.state === "judged" && msg.state !== "judged") return row;
  if (msg.state === "judged") {
    const fresh = msg.submission && typeof msg.submission === "object" ? msg.submission : {};
    return { ...row, ...fresh, state: "judged" };
  }
  if (msg.state === "queued" || msg.state === "judging") return { ...row, state: msg.state };
  return row;
}

export default function SubmissionsPage() {
  const [items, setItems] = useState([]);
  const [next, setNext] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const { mode, resyncKey } = useRealtime();
  const polling = isPollingMode(mode);
  const itemsRef = useRef(items);
  itemsRef.current = items;
  const reloadTimer = useRef(null);

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

  const reloadFirstPage = async (signal) => {
    try {
      const page = await api.submissions({ limit: PAGE_SIZE }, { signal });
      setItems((old) => mergeFirstPage(old, page.submissions));
      setNext((n) => (itemsRef.current.length === 0 ? page.next_before_id : n));
    } catch {
      // Chỉ là làm mới ngầm: lỗi không đáng báo; lần sau (STATUS, poll, nối lại) sẽ thử lại. 401 đã có handler chung.
    }
  };

  useEffect(() => () => clearTimeout(reloadTimer.current), []);

  const scheduleReload = () => {
    clearTimeout(reloadTimer.current);
    reloadTimer.current = setTimeout(() => reloadFirstPage(), RELOAD_DEBOUNCE_MS);
  };

  useStatusEvents((msg) => {
    const known = itemsRef.current.some((s) => s.submission_id === msg.submission_id);
    if (known) {
      setItems((old) => old.map((s) => (s.submission_id === msg.submission_id ? applyStatus(s, msg) : s)));
      return;
    }
    // Bài chưa có trong danh sách (nộp từ tab khác): tải lại trang 1. Bài cũ hơn mọi dòng đã tải thì không thuộc trang 1.
    const oldest = itemsRef.current.at(-1)?.submission_id;
    if (oldest === undefined || msg.submission_id > oldest) scheduleReload();
  });

  // Sau khi nối lại WebSocket: lấy kết quả bị lỡ (README 4.7 mục 3).
  const syncedKey = useRef(resyncKey);
  useEffect(() => {
    if (syncedKey.current === resyncKey) return undefined;
    syncedKey.current = resyncKey;
    const controller = new AbortController();
    reloadFirstPage(controller.signal);
    return () => controller.abort();
  }, [resyncKey]);

  // Không có WebSocket (đang nối lại hoặc bị 4429): gọi lại trang 1 định kỳ chừng nào còn bài chưa chấm xong.
  useEffect(() => {
    if (!polling) return undefined;
    return startPolling({
      fetchOnce: (signal) => api.submissions({ limit: PAGE_SIZE }, { signal }),
      onResult: (page) => setItems((old) => mergeFirstPage(old, page.submissions)),
      isDone: (page) => !page.submissions.some((s) => s.state !== "judged"),
      intervalMs: FALLBACK_POLL_MS,
    });
  }, [polling]);

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
