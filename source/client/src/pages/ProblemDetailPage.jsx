import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../lib/api.js";
import { LANGUAGES, MAX_CODE_BYTES, codeBytes, errorText } from "../lib/format.js";
import { BusyError, createSubmitter } from "../lib/submitter.js";
import { useFetch } from "../lib/useFetch.js";

function SubmitForm({ problemId }) {
  const navigate = useNavigate();
  const [language, setLanguage] = useState("python");
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [countdown, setCountdown] = useState(0);
  const countdownTimer = useRef(null);
  // Một submitter cho cả vòng đời form: nó nhớ token của lần nộp dở (lỗi mạng) để thử lại dùng đúng token đó.
  const submitter = useMemo(() => createSubmitter({ post: (payload) => api.submit(payload) }), []);

  useEffect(() => () => clearInterval(countdownTimer.current), []);

  const bytes = codeBytes(code);
  const tooLarge = bytes > MAX_CODE_BYTES;

  const startCountdown = (seconds) => {
    clearInterval(countdownTimer.current);
    setCountdown(seconds);
    countdownTimer.current = setInterval(() => {
      setCountdown((c) => {
        if (c <= 1) {
          clearInterval(countdownTimer.current);
          return 0;
        }
        return c - 1;
      });
    }, 1000);
  };

  const onSubmit = async (e) => {
    e.preventDefault();
    if (submitter.busy || tooLarge || code === "") return;
    setBusy(true);
    setError("");
    try {
      const result = await submitter.submit({ problem_id: problemId, language, code });
      navigate(`/submissions/${result.submission_id}`);
    } catch (err) {
      if (err instanceof BusyError) return;
      let text = errorText(err);
      if (err?.name === "NetworkError" || err?.status >= 500) text += " Nhấn Nộp bài để thử lại; bài sẽ không bị nộp trùng.";
      setError(text);
      if (err?.code === "RATE_LIMITED" && err.retryAfter) startCountdown(err.retryAfter);
    } finally {
      setBusy(false);
    }
  };

  const onFile = async (e) => {
    const file = e.target.files?.[0];
    if (file) setCode(await file.text());
    e.target.value = "";
  };

  return (
    <form onSubmit={onSubmit} className="submit-form">
      <h2>Nộp bài</h2>
      <label>
        Ngôn ngữ
        <select value={language} onChange={(e) => setLanguage(e.target.value)}>
          {LANGUAGES.map((l) => (
            <option key={l.value} value={l.value}>
              {l.label}
            </option>
          ))}
        </select>
      </label>
      <label>
        Mã nguồn
        <textarea value={code} onChange={(e) => setCode(e.target.value)} rows={16} spellCheck={false} />
      </label>
      <label>
        Hoặc chọn file
        <input type="file" onChange={onFile} />
      </label>
      <p className={tooLarge ? "error" : "muted"}>
        {bytes} / {MAX_CODE_BYTES} byte
      </p>
      {error && <p role="alert" className="error">{error}</p>}
      <button type="submit" disabled={busy || tooLarge || code === "" || countdown > 0}>
        {countdown > 0 ? `Chờ ${countdown} giây` : busy ? "Đang nộp…" : "Nộp bài"}
      </button>
    </form>
  );
}

export default function ProblemDetailPage() {
  const { id } = useParams();
  const { data, error, loading } = useFetch((signal) => api.problem(id, { signal }), id);

  if (loading) return <p className="muted">Đang tải…</p>;
  if (error) return <p role="alert" className="error">{errorText(error)}</p>;

  return (
    <>
      <h1>
        {data.title}
        {data.hidden && <span className="badge">ẩn</span>}
      </h1>
      <p className="muted">
        Giới hạn gốc: {data.time_limit_ms} ms, {data.memory_limit_kib} KiB
      </p>
      <pre className="statement">{data.statement}</pre>
      {data.samples.map((s, i) => (
        <div key={i} className="sample">
          <h3>Ví dụ {i + 1}</h3>
          <div className="io">
            <div>
              <strong>Input</strong>
              <pre>{s.input}</pre>
            </div>
            <div>
              <strong>Output</strong>
              <pre>{s.output}</pre>
            </div>
          </div>
        </div>
      ))}
      <SubmitForm key={data.id} problemId={data.id} />
    </>
  );
}
