import React from "react";
import { useData } from "../context/AppContext";
import { Loading } from "../components/Loading";
import { date } from "../utils";

export function Scoreboard() {
  const result = useData("/scoreboard");

  return (
    <>
      <h1>Bảng xếp hạng</h1>
      <p className="muted">Ưu tiên số đề AC, sau đó thời điểm đạt số đề đó. Mỗi đề chỉ tính một lần.</p>
      <Loading result={result}>
        {(rows) => (
          <div className="table-wrap">
            <table>
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
                  <tr key={r.username}>
                    <td>{i + 1}</td>
                    <td>{r.username}</td>
                    <td>{r.solved}</td>
                    <td>{date(r.last_ac)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Loading>
    </>
  );
}
