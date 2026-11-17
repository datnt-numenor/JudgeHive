import React from "react";
import { useApp, useData } from "../context/AppContext";
import { Loading } from "../components/Loading";
import { date } from "../utils";

export function Scoreboard() {
  const { user } = useApp();
  const result = useData("/scoreboard");

  return (
    <>
      <h1>Bảng xếp hạng</h1>
      <p className="muted">Xếp theo số đề đã giải, sau đó theo thời điểm đạt.</p>
      <Loading result={result}>
        {(rows) => (
          <div className="table-wrap">
            <table className="scoreboard-table">
              <thead>
                <tr>
                  <th>Hạng</th>
                  <th>Người dùng</th>
                  <th>Số đề AC</th>
                  <th>Thời điểm đạt</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={r.username} className={r.username === user.username ? "current-user" : ""}>
                    <td className={i < 3 ? "podium" : ""}>{i + 1}</td>
                    <td>{r.username} {r.username === user.username && <span className="you-label">Bạn</span>}</td>
                    <td>{r.solved}</td>
                    <td>{date(r.last_ac)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Loading>
      <p className="muted">Mỗi đề chỉ tính một lần.</p>
    </>
  );
}
