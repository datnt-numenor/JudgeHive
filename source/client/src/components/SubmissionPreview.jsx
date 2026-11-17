import React from "react";
import { Link } from "react-router-dom";
import { useData } from "../context/AppContext";
import { Loading } from "./Loading";
import { Verdict } from "./Verdict";
import { SubmissionState } from "./SubmissionState";
import { CodeBlock } from "./CodeBlock";
import { languageName } from "../utils";

export function SubmissionPreview({ id, compact = false }) {
  const result = useData(`/submissions/${id}`);
  return <Loading result={result}>{s => <section className={`card submission-preview ${compact ? "compact" : ""}`}><div className="preview-heading"><h2>Bài nộp #{s.id}</h2><Link to={`/submissions/${s.id}`}>Xem chi tiết →</Link></div><div className="result-summary"><Verdict value={s.verdict} full />{!s.verdict && <SubmissionState value={s.state}/>}<span>{languageName[s.language] ?? s.language} · {s.tests_done} test đã qua · {s.verdict === "TLE" ? "Quá giới hạn" : `${s.time_ms ?? "—"} ms`}</span></div>{!compact && <CodeBlock code={s.code}/>}</section>}</Loading>;
}
