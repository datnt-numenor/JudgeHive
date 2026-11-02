import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api.js";
import { errorText } from "../lib/format.js";
import { startPolling } from "../lib/poll.js";

export const WORKERS_POLL_MS = 2000;

function JobCell({ job }) {
  if (!job) return <span className="muted">—</span>;
  const progress = job.tests_done == null ? "chưa báo tiến độ" : `${job.tests_done}/${job.tests_total} test`;
  return (
    <>
      <Link to={`/admin/submissions/${job.submission_id}`}>#{job.submission_id}</Link> · lần giao {job.attempt_id} ·{" "}
      {job.confirmed ? "đã xác nhận" : "chưa xác nhận"} · {progress}
      {job.is_rejudge && " · chấm lại"}
    </>
  );
}

/** Theo dõi worker và hàng đợi (README 3.6, `GET /api/admin/workers`), gọi lại mỗi 2 giây, tạm dừng khi tab ẩn. */
export default function WorkersPage() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [retrying, setRetrying] = useState(false);

  useEffect(
    () =>
      startPolling({
        fetchOnce: (signal) => api.adminWorkers({ signal }),
        onResult: (d) => {
          setData(d);
          setError(null);
          setRetrying(false);
        },
        onError: (e, { fatal }) => {
          if (fatal) setError(e);
          else setRetrying(true);
        },
        isDone: () => false,
        intervalMs: WORKERS_POLL_MS,
      }),
    [],
  );

  if (error) return <p role="alert" className="error">{errorText(error)}</p>;
  if (!data) return <p className="muted">Đang tải…</p>;

  const { workers, queue } = data;
  return (
    <>
      <h1>Worker và hàng đợi</h1>
      {retrying && <p className="muted">Mất kết nối tạm thời, đang thử lại…</p>}
      <p data-testid="queue">
        Hàng đợi: {queue.queued} bài mới đang chờ · {queue.rejudge_queued} bài chờ chấm lại · {queue.judging} bài đang chấm
      </p>
      {workers.length === 0 ? (
        <p className="muted">Không có worker nào đang kết nối.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Tên</th>
              <th>Địa chỉ</th>
              <th>Trạng thái</th>
              <th>Kết nối (giây)</th>
              <th>Thấy lần cuối (giây trước)</th>
              <th>Bài đã chấm</th>
              <th>Bài đang giữ</th>
            </tr>
          </thead>
          <tbody>
            {workers.map((w) => (
              <tr key={w.worker_id}>
                <td>{w.worker_id}</td>
                <td>{w.name}</td>
                <td>{w.addr}</td>
                <td>{w.state === "busy" ? "Bận" : "Rảnh"}</td>
                <td>{w.connected_s}</td>
                <td>{w.last_seen_s}</td>
                <td>{w.jobs_done}</td>
                <td>
                  <JobCell job={w.job} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="muted">
        Danh sách worker và số trong hàng đợi được đọc ở hai thời điểm khác nhau nên có thể lệch nhẹ. Trang tự cập nhật mỗi{" "}
        {WORKERS_POLL_MS / 1000} giây.
      </p>
    </>
  );
}
