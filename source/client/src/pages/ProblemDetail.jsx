import React, { useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api";
import { useData } from "../context/AppContext";
import { Loading } from "../components/Loading";
import { SubmissionPreview } from "../components/SubmissionPreview";
import { CodeBlock } from "../components/CodeBlock";
import { ErrorText } from "../components/ErrorText";

export function ProblemDetail() {
  const { id } = useParams();
  const result = useData(`/problems/${id}`);
  const [submissionId, setSubmissionId] = useState(null);
  const [code, setCode] = useState("");
  const highlight = useRef(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const fields = new FormData(event.currentTarget);
    try {
      const body = { problem_id: Number(id), language: fields.get("language"), code: fields.get("code") };
      const submitted = await api("/submissions", "POST", body);
      setSubmissionId(submitted.id);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Loading result={result}>
      {(p) => (
        <div className="solve-layout">
          <article className="problem-statement">
            <h1>{String(p.id).padStart(2, "0")}. {p.title}</h1>
            <p className="problem-limits">◷ {p.time_limit_ms / 1000} giây <span>·</span> ▦ {p.memory_limit_kib / 1024} MB</p>
            <h2>Mô tả</h2>
            <p className="statement">{p.statement}</p>
            {p.samples.map((sample, index) => (
              <section key={index}><h2>Ví dụ {p.samples.length > 1 ? index + 1 : ""}</h2><div className="table-wrap sample-table"><table><thead><tr><th>Dữ liệu vào</th><th>Kết quả ra</th></tr></thead><tbody><tr><td><pre>{sample.input}</pre></td><td><pre>{sample.output}</pre></td></tr></tbody></table></div></section>
            ))}
            <p className="limits-note">
              Giới hạn: {p.time_limit_ms} ms và {p.memory_limit_kib / 1024} MiB cho mỗi test.
            </p>
          </article>
          <section className="workspace">
            <form onSubmit={submit}>
              <div className="editor-toolbar"><label htmlFor="language">Ngôn ngữ:</label>
                <select name="language" id="language">
                  <option value="python">Python 3</option>
                  <option value="cpp">C++17</option>
                  <option value="java">Java (lớp Main)</option>
                </select>
                <button disabled={busy}>{busy ? "Đang gửi…" : "➤ Nộp bài"}</button></div>
              <div className="code-editor"><div className="editor-highlight" ref={highlight} aria-hidden="true"><CodeBlock code={code}/></div><textarea aria-label="Mã nguồn · tối đa 64 KiB" name="code" spellCheck="false" required value={code} onChange={e => setCode(e.target.value)} onScroll={e => { highlight.current.style.transform = `translate(${-e.currentTarget.scrollLeft}px, ${-e.currentTarget.scrollTop}px)`; }} placeholder="Viết lời giải của bạn ở đây…" onKeyDown={e => { if (e.key === "Tab") { e.preventDefault(); const field = e.currentTarget; const start = field.selectionStart; const next = code.slice(0, start) + "    " + code.slice(field.selectionEnd); setCode(next); requestAnimationFrame(() => { field.selectionStart = field.selectionEnd = start + 4; }); } }}/></div>
              <ErrorText message={error} />
            </form>
            <div className="judge-result"><h2>›_ Kết quả chấm</h2>{submissionId ? <SubmissionPreview key={submissionId} id={submissionId} compact/> : <p className="muted">Nộp lời giải để xem kết quả chấm.</p>}</div>
          </section>
        </div>
      )}
    </Loading>
  );
}
