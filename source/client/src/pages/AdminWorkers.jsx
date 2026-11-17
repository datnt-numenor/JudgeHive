import React from "react";
import { Link } from "react-router-dom";
import { useData } from "../context/AppContext";
import { Loading } from "../components/Loading";
import { stateName } from "../utils";

export function AdminWorkers() {
  const result = useData("/admin/workers");

  return (
    <>
      <h1>Máy chấm</h1>
      <p className="muted">
        Trạng thái theo thông tin server hiện có. Worker rảnh mất mạng có thể chưa được phát hiện cho đến khi
        giao bài.
      </p>
      <Loading result={result}>
        {(data) => (
          <>
            <p>
              Đang chờ: <strong>{data.queue}</strong> bài
            </p>
            <div className="grid">
              {data.workers.map((w) => (
                <section className="card" key={w.id}>
                  <span className="eyebrow">KẾT NỐI #{w.id}</span>
                  <h2>{w.name}</h2>
                  <p>{stateName[w.state]}</p>
                  {w.submission_id && (
                    <Link to={`/submissions/${w.submission_id}`}>Bài #{w.submission_id}</Link>
                  )}
                </section>
              ))}
            </div>
          </>
        )}
      </Loading>
    </>
  );
}
