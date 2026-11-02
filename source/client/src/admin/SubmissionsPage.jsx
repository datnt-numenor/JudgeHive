import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api.js";
import { errorText, formatTime, stateLabel, verdictLabel } from "../lib/format.js";

const PAGE_SIZE = 50; // mặc định của README 3.6
const USER_ID_RE = /^[1-9][0-9]{0,18}$/; // server nhận số nguyên dương ASCII tối đa 2^63-1; chỉ chặn sớm chỗ gõ sai rõ ràng

const NO_FILTER = { state: "", problemId: "", userId: "" };

/**
 * Bài nộp của mọi người (README 3.6, `GET /api/admin/submissions`). Không tự làm mới: chỉ tải khi mở, khi áp bộ lọc và
 * khi bấm "Tải lại". Đổi bộ lọc là bắt đầu lại từ trang 1.
 */
export default function AdminSubmissionsPage() {
  const [draft, setDraft] = useState(NO_FILTER); // đang gõ
  const [applied, setApplied] = useState(NO_FILTER); // đang dùng để gọi API
  const [formError, setFormError] = useState("");
  const [items, setItems] = useState([]);
  const [next, setNext] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const generation = useRef(0); // kết quả của lần gọi cũ (đã đổi bộ lọc hoặc đã bấm Tải lại) bị bỏ

  const load = async (filters, beforeId) => {
    const mine = ++generation.current;
    setLoading(true);
    setError(null);
    try {
      const page = await api.adminSubmissions({
        state: filters.state,
        problemId: filters.problemId,
        userId: filters.userId === "" ? undefined : Number(filters.userId),
        beforeId,
        limit: PAGE_SIZE,
      });
      if (mine !== generation.current) return;
      setItems((old) => (beforeId == null ? page.submissions : [...old, ...page.submissions]));
      setNext(page.next_before_id);
    } catch (e) {
      if (mine === generation.current) {
        if (beforeId == null) setItems([]);
        setNext(null);
        setError(e);
      }
    } finally {
      if (mine === generation.current) setLoading(false);
    }
  };

  useEffect(() => {
    load(applied, null);
    return () => {
      generation.current += 1; // rời trang hoặc đổi bộ lọc: bỏ kết quả đang bay
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- chỉ tải lại khi bộ lọc đã áp đổi; `load` đọc tham số truyền vào
  }, [applied]);

  const submit = (event) => {
    event.preventDefault();
    const userId = draft.userId.trim();
    if (userId !== "" && !USER_ID_RE.test(userId)) {
      setFormError("Mã người dùng phải là số nguyên dương.");
      return;
    }
    setFormError("");
    setApplied({ state: draft.state, problemId: draft.problemId.trim(), userId });
  };

  return (
    <>
      <h1>Bài nộp (admin)</h1>
      <form onSubmit={submit} className="filters">
        <label>
          Trạng thái
          <select value={draft.state} onChange={(e) => setDraft({ ...draft, state: e.target.value })}>
            <option value="">Tất cả</option>
            <option value="queued">{stateLabel("queued")}</option>
            <option value="judging">{stateLabel("judging")}</option>
            <option value="judged">{stateLabel("judged")}</option>
          </select>
        </label>{" "}
        <label>
          Mã đề
          <input value={draft.problemId} onChange={(e) => setDraft({ ...draft, problemId: e.target.value })} />
        </label>{" "}
        <label>
          Mã người dùng
          <input value={draft.userId} inputMode="numeric" onChange={(e) => setDraft({ ...draft, userId: e.target.value })} />
        </label>{" "}
        <button type="submit">Lọc</button>{" "}
        <button type="button" onClick={() => load(applied, null)} disabled={loading}>
          Tải lại
        </button>
      </form>
      {formError && <p role="alert" className="error">{formError}</p>}
      {items.length === 0 && !loading && !error && <p className="muted">Không có bài nộp nào khớp.</p>}
      {items.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>Người nộp</th>
              <th>Đề</th>
              <th>Ngôn ngữ</th>
              <th>Thời điểm nộp</th>
              <th>Kết quả</th>
              <th>Lần giao (đã xác nhận bị cắt / tổng)</th>
              <th>Ghi chú</th>
            </tr>
          </thead>
          <tbody>
            {items.map((s) => (
              <tr key={s.submission_id}>
                <td>
                  <Link to={`/admin/submissions/${s.submission_id}`}>{s.submission_id}</Link>
                </td>
                <td>
                  {s.username} <span className="muted">(#{s.user_id})</span>
                </td>
                <td>{s.problem_title}</td>
                <td>{s.language}</td>
                <td>{formatTime(s.received_at)}</td>
                <td>{s.state === "judged" ? verdictLabel(s.verdict) : stateLabel(s.state)}</td>
                <td>
                  {s.delivery_count} / {s.total_deliveries}
                </td>
                <td>
                  {s.is_rejudge && "đang chấm lại"}
                  {s.rejudge_failed && " lần chấm lại gần nhất lỗi"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {loading && <p className="muted">Đang tải…</p>}
      {error && <p role="alert" className="error">{errorText(error)}</p>}
      {next != null && !loading && (
        <button type="button" onClick={() => load(applied, next)}>
          Tải thêm
        </button>
      )}
    </>
  );
}
