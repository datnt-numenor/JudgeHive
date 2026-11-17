import React, { useState } from "react";
import { SubmissionPreview } from "../components/SubmissionPreview";
import { Link } from "react-router-dom";
import { useData } from "../context/AppContext";
import { Loading } from "../components/Loading";
import { SubmissionState } from "../components/SubmissionState";
import { Verdict } from "../components/Verdict";
import { date, languageName } from "../utils";

export function Submissions({ admin = false }) {
  const [selected, setSelected] = useState(null);
  // Server trả 100 bài mới nhất. Tên đề lấy từ danh sách đề (không đổi API bài nộp).
  const result = useData(`${admin ? "/admin" : ""}/submissions`);
  const problems = useData("/problems");
  const titles = Object.fromEntries((problems.data ?? []).map((p) => [p.id, p.title]));

  return (
    <>
      <h1>{admin ? "Tất cả bài nộp" : "Bài nộp của tôi"}</h1>
      <p className="muted">100 bài nộp gần nhất</p>
      <Loading result={result}>
        {(rows) => (
          <>
            <div className="table-wrap">
              <table className={`submissions-table ${admin ? "admin-submissions" : ""}`}>
                <thead>
                  <tr>
                    <th>ID</th>
                    {admin && <th>Người nộp</th>}
                    <th>Đề bài</th>
                    <th>Ngôn ngữ</th>
                    <th>Trạng thái</th>
                    <th>Kết quả</th>
                    <th>Thời điểm</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((s) => (
                    <tr key={s.id} className={selected === s.id ? "selected-row" : ""} onClick={() => setSelected(s.id)}>
                      <td>
                        <button className="table-link" onClick={() => setSelected(s.id)} aria-label={`Xem nhanh bài nộp ${s.id}`}>#{s.id}</button>
                      </td>
                      {admin && <td>{s.username}</td>}
                      <td>
                        <Link to={`/problems/${s.problem_id}`}>
                          {titles[s.problem_id]
                            ? `${s.problem_id}. ${titles[s.problem_id]}`
                            : `#${s.problem_id}`}
                        </Link>
                      </td>
                      <td>{languageName[s.language] ?? s.language}</td>
                      <td>
                        <SubmissionState value={s.state} />
                      </td>
                      <td>
                        <Verdict value={s.verdict} />
                      </td>
                      <td>{date(s.submitted_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!rows.length && <p>Chưa có bài nộp.</p>}
            {rows.length > 0 && <SubmissionPreview key={selected ?? rows[0].id} id={selected ?? rows[0].id} compact={admin}/>}
          </>
        )}
      </Loading>
    </>
  );
}
