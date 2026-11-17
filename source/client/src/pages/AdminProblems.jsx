import React, { useState } from "react";
import { api } from "../api";
import { useApp, useData } from "../context/AppContext";
import { Loading } from "../components/Loading";
import { ErrorText } from "../components/ErrorText";

// Đề được nạp bằng lệnh `python -m server.manage problem FILE.json`; web chỉ xem và ẩn/hiện.
export function AdminProblems() {
  const { refresh } = useApp();
  const result = useData("/problems");
  const [error, setError] = useState("");

  async function hide(p) {
    setError("");
    try {
      await api(`/admin/problems/${p.id}`, "PATCH", { hidden: !p.hidden });
      refresh();
    } catch (e) {
      setError(e.message);
    }
  }

  return (
    <>
      <h1>Quản lý đề bài</h1>
      <p className="muted">
        Thêm đề bằng lệnh nạp file JSON ở server (xem README). Đề đang ẩn không hiện với USER và không tính
        vào bảng xếp hạng.
      </p>
      <ErrorText message={error} />
      <Loading result={result}>
        {(rows) => (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Đề</th>
                  <th>Hiển thị</th>
                  <th>Thao tác</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((p) => (
                  <tr key={p.id}>
                    <td>
                      #{p.id} {p.title}
                    </td>
                    <td>{p.hidden ? "Ẩn" : "Hiện"}</td>
                    <td className="actions">
                      <button onClick={() => hide(p)}>{p.hidden ? "Hiện đề" : "Ẩn đề"}</button>
                    </td>
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
