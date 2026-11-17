import React, { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { useData } from "../context/AppContext";
import { Loading } from "../components/Loading";
import { Difficulty } from "../components/Difficulty";
import { ErrorText } from "../components/ErrorText";

export function ProblemDetail() {
  const { id } = useParams();
  const result = useData(`/problems/${id}`);
  const navigate = useNavigate();
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
      navigate(`/submissions/${submitted.id}`);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Loading result={result}>
      {(p) => (
        <>
          <div className="title-row">
            <h1>{p.title}</h1>
            <Difficulty value={p.difficulty} />
          </div>
          <article className="card">
            <p className="statement">{p.statement}</p>
            {p.samples.map((sample, index) => (
              <div className="grid" key={index}>
                <div>
                  <h3>Input mẫu {index + 1}</h3>
                  <pre>{sample.input}</pre>
                </div>
                <div>
                  <h3>Output mẫu</h3>
                  <pre>{sample.output}</pre>
                </div>
              </div>
            ))}
            <p className="limits-note">
              Giới hạn: {p.time_limit_ms} ms và {p.memory_limit_kib / 1024} MiB cho mỗi test.
            </p>
          </article>
          <section className="card">
            <h2>Nộp lời giải</h2>
            <form onSubmit={submit}>
              <label>
                Ngôn ngữ
                <select name="language">
                  <option value="python">Python</option>
                  <option value="cpp">C++17</option>
                  <option value="java">Java (lớp Main)</option>
                </select>
              </label>
              <label>
                Mã nguồn · tối đa 64 KiB
                <textarea name="code" rows="15" spellCheck="false" required />
              </label>
              <ErrorText message={error} />
              <button disabled={busy}>{busy ? "Đang gửi…" : "Nộp bài"}</button>
            </form>
          </section>
        </>
      )}
    </Loading>
  );
}
