import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../lib/api.js";
import { errorText, formatTime, stateLabel } from "../lib/format.js";
import { useFetch } from "../lib/useFetch.js";
import { Details, Verdict } from "../pages/SubmissionDetailPage.jsx";

/**
 * Chi tiết bài nộp của bất kỳ ai, kèm mã nguồn (README 3.6, `GET /api/admin/submissions/{id}`). Dùng lại `Verdict` và
 * `Details` của trang thí sinh nên mọi chuỗi từ server (code, ce_message, sample_*) chỉ là text trong `<pre>`. Không tự
 * làm mới: bấm "Tải lại" để xem trạng thái mới.
 */
export default function AdminSubmissionDetailPage() {
  const { id } = useParams();
  const [reload, setReload] = useState(0);
  const { data: s, error, loading } = useFetch((signal) => api.adminSubmission(id, { signal }), `${id}:${reload}`);

  return (
    <>
      <p>
        <Link to="/admin/submissions">← Bài nộp (admin)</Link>
      </p>
      {loading && <p className="muted">Đang tải…</p>}
      {error && <p role="alert" className="error">{errorText(error)}</p>}
      {s && (
        <>
          <h1>
            Bài nộp #{s.submission_id}: {s.problem_title}
          </h1>
          <p className="muted">
            {s.username} (#{s.user_id}) · {s.language} · {formatTime(s.received_at)} ·{" "}
            <span data-testid="state">{stateLabel(s.state)}</span>
          </p>
          <p className="muted" data-testid="ops">
            Lần giao hiện tại {s.attempt_id} · đã xác nhận bị cắt {s.delivery_count} · tổng lần giao {s.total_deliveries}
            {s.is_rejudge && " · đang chấm lại"}
            {s.rejudge_failed && " · lần chấm lại gần nhất lỗi"}
          </p>
          <Verdict s={s} />
          <Details s={s} />
        </>
      )}
      <button type="button" onClick={() => setReload((n) => n + 1)} disabled={loading}>
        Tải lại
      </button>
    </>
  );
}
