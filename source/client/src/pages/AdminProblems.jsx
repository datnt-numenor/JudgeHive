import React, { useState } from "react";
import { api } from "../api";
import { useApp, useData } from "../context/AppContext";
import { Loading } from "../components/Loading";
import { ErrorText } from "../components/ErrorText";
import { Difficulty } from "../components/Difficulty";
import { Link } from "react-router-dom";

// Đề được nạp bằng lệnh `python -m server.manage problem FILE.json`; web chỉ xem và ẩn/hiện.
export function AdminProblems() {
  const { refresh } = useApp();
  const result = useData("/problems");
  const [error, setError] = useState("");
  const [selected, setSelected] = useState(null);
  const [busy, setBusy] = useState(false);

  async function hide(p) {
    setBusy(true);
    setError("");
    try {
      await api(`/admin/problems/${p.id}`, "PATCH", { hidden: !p.hidden });
      refresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h1>Quản lý đề bài</h1>
      <ErrorText message={error} />
      <Loading result={result}>
        {(rows) => (
          <div className="admin-split"><section><div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>ID</th><th>Tên đề</th>
                  <th>Hiển thị</th>
                  <th>Thao tác</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((p) => (
                  <tr key={p.id} className={selected === p.id ? "selected-row" : ""}>
                    <td>{String(p.id).padStart(2, "0")}</td><td>{p.title}</td>
                    <td className={p.hidden ? "amber" : ""}>{p.hidden ? "Ẩn" : "Công khai"}</td>
                    <td>
                      <button className="text-button" onClick={() => setSelected(p.id)}>Chi tiết</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div><p className="muted">Đề đang ẩn không hiện với USER và không tính vào bảng xếp hạng.</p></section><aside className="admin-panel"><h2>Thông tin đề {selected ? `#${String(selected).padStart(2, "0")}` : ""}</h2>{rows.filter(p => p.id === selected).map(p => <section key={p.id}><label>Tên đề<input value={p.title} readOnly/></label><label>Độ khó</label><p><Difficulty value={p.difficulty}/></p><div className="grid"><label>Thời gian (ms)<input value={p.time_limit_ms} readOnly/></label><label>Bộ nhớ (MiB)<input value={p.memory_limit_kib / 1024} readOnly/></label></div><p>Hiển thị: {p.hidden ? "Ẩn" : "Công khai"}</p><div className="actions"><button disabled={busy} onClick={() => hide(p)}>{p.hidden ? "Hiện đề" : "Ẩn đề"}</button><Link to={`/problems/${p.id}`}>Xem đề →</Link></div></section>)}{!selected && <p className="muted">Chọn một đề trong danh sách để xem thông tin và đổi trạng thái hiển thị.</p>}<details className="import-help"><summary>Thêm hoặc cập nhật đề</summary><p>Nạp file JSON trên server theo hướng dẫn README.</p><pre>python -m server.manage problem problems/ten-de.json</pre></details></aside></div>
        )}
      </Loading>
    </>
  );
}
