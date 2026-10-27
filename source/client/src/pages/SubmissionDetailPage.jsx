import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../lib/api.js";
import { errorText, formatKib, formatTime, reKindLabel, stateLabel, verdictLabel } from "../lib/format.js";
import { startPolling } from "../lib/poll.js";

function Verdict({ s }) {
  if (s.verdict == null) return <p>Chưa có kết quả.</p>;
  return (
    <ul className="verdict">
      <li>
        <strong>{verdictLabel(s.verdict)}</strong>
        {s.state !== "judged" && " (kết quả của lần chấm trước)"}
      </li>
      {s.failed_test != null && (
        <li>
          Sai ở test {s.failed_test}
          {s.failed_is_sample ? " (test mẫu)" : ""}
        </li>
      )}
      {s.verdict === "RE" && s.re_kind && <li>Loại lỗi: {reKindLabel(s.re_kind)}</li>}
      {s.time_ms != null && <li>Thời gian: {s.time_ms} ms</li>}
      {/* README 2.5: với MLE, UI không hiện con số bộ nhớ */}
      {s.memory_kib != null && s.verdict !== "MLE" && <li>Bộ nhớ: {formatKib(s.memory_kib)}</li>}
      {s.verdict === "SE" && <li>Hệ thống chấm gặp sự cố. Thử nộp lại sau.</li>}
      {s.rejudged_at && <li>Chấm lại lúc {formatTime(s.rejudged_at)}</li>}
    </ul>
  );
}

function Details({ s }) {
  return (
    <>
      {s.verdict === "CE" && s.ce_message != null && (
        <>
          <h2>Thông báo biên dịch</h2>
          <pre>{s.ce_message}</pre>
        </>
      )}
      {s.failed_is_sample && (
        <>
          <h2>Test mẫu sai</h2>
          {s.sample_input != null && (
            <>
              <strong>Input</strong>
              <pre>{s.sample_input}</pre>
            </>
          )}
          {s.sample_expected_output != null && (
            <>
              <strong>Output mong đợi</strong>
              <pre>{s.sample_expected_output}</pre>
            </>
          )}
          {s.sample_stdout != null && (
            <>
              <strong>Output của chương trình</strong>
              <pre>{s.sample_stdout}</pre>
            </>
          )}
        </>
      )}
      <h2>Mã nguồn</h2>
      <pre>{s.code}</pre>
    </>
  );
}

export default function SubmissionDetailPage() {
  const { id } = useParams();
  const [submission, setSubmission] = useState(null);
  const [error, setError] = useState(null);
  const [retrying, setRetrying] = useState(false);

  useEffect(() => {
    setSubmission(null);
    setError(null);
    setRetrying(false);
    return startPolling({
      fetchOnce: (signal) => api.submission(id, { signal }),
      onResult: (data) => {
        setSubmission(data);
        setError(null);
        setRetrying(false);
      },
      onError: (e, { fatal }) => {
        if (fatal) setError(e);
        else setRetrying(true);
      },
      isDone: (data) => data.state === "judged",
    });
  }, [id]);

  if (error) {
    return (
      <p role="alert" className="error">
        {errorText(error)}
      </p>
    );
  }
  if (!submission) return <p className="muted">Đang tải…</p>;

  const s = submission;
  return (
    <>
      <p>
        <Link to="/submissions">← Bài nộp của tôi</Link>
      </p>
      <h1>
        Bài nộp #{s.submission_id}: {s.problem_title}
      </h1>
      <p className="muted">
        {s.language} · {formatTime(s.received_at)} · <span data-testid="state">{stateLabel(s.state)}</span>
      </p>
      {retrying && <p className="muted">Mất kết nối tạm thời, đang thử lại…</p>}
      <Verdict s={s} />
      <Details s={s} />
    </>
  );
}
