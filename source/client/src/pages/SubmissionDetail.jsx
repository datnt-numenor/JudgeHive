import React from "react";
import { useParams } from "react-router-dom";
import { useData } from "../context/AppContext";
import { Loading } from "../components/Loading";
import { SubmissionState } from "../components/SubmissionState";
import { Verdict } from "../components/Verdict";
import { date } from "../utils";

export function SubmissionDetail() {
  const { id } = useParams();
  const result = useData(`/submissions/${id}`);
  const problems = useData("/problems");
  const limitOf = (pid) => (problems.data ?? []).find((p) => p.id === pid)?.time_limit_ms;

  return (
    <Loading result={result}>
      {(s) => (
        <>
          <h1>Bài nộp #{s.id}</h1>
          <section className="card">
            <div className="actions">
              <Verdict value={s.verdict} />
              <SubmissionState value={s.state} />
            </div>
            <p>
              Ngôn ngữ: {s.language} · Lần giao: {s.attempt_id} · Test đã qua: {s.tests_done}
            </p>
            <p>
              Thời gian test chậm nhất:{" "}
              {/* Bài TLE bị dừng sau giới hạn, nên thời gian thật là "lớn hơn giới hạn". */}
              {s.verdict === "TLE" && limitOf(s.problem_id)
                ? `> ${limitOf(s.problem_id)} ms`
                : `${s.time_ms ?? "—"} ms`}
            </p>
            {s.verdict === "CE" ? <pre>{s.message}</pre> : <p>{s.message}</p>}
            <p className="muted">Nộp lúc {date(s.submitted_at)}</p>
          </section>
          <h2>Mã nguồn</h2>
          <pre>{s.code}</pre>
        </>
      )}
    </Loading>
  );
}
