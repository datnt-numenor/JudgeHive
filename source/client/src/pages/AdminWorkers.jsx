import React, { useState } from "react";
import { useData } from "../context/AppContext";
import { Loading } from "../components/Loading";
import { stateName } from "../utils";
import { SubmissionPreview } from "../components/SubmissionPreview";

export function AdminWorkers() {
  const result = useData("/admin/workers");
  const [selected, setSelected] = useState(null);

  return (
    <>
      <h1>Máy chấm</h1>
      <Loading result={result}>
        {(data) => (
          <>
            <p>
              Hàng đợi: <strong>{data.queue}</strong> bài đang chờ
            </p>
            <div className="table-wrap"><table><thead><tr><th>Kết nối</th><th>Tên worker</th><th>Trạng thái</th><th>Bài đang chấm</th></tr></thead><tbody>
              {data.workers.map((w) => (
                <tr key={w.id}><td><span className="live-dot"/> #{w.id}</td><td>{w.name}</td><td className={w.state === "idle" ? "positive" : "amber"}>{stateName[w.state]}</td><td>{w.submission_id ? <button className="table-link" onClick={() => setSelected(w.submission_id)}>#{w.submission_id}</button> : "—"}</td></tr>
              ))}
            </tbody></table></div>
            {!data.workers.length && <p className="muted">Chưa có máy chấm kết nối.</p>}
            <p className="muted worker-note">Trạng thái theo thông tin server hiện có. Worker rảnh mất mạng có thể chưa được phát hiện cho đến khi giao bài.</p>
            {selected && <SubmissionPreview key={selected} id={selected} compact/>}
          </>
        )}
      </Loading>
    </>
  );
}
