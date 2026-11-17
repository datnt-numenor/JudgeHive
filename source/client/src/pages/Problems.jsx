import React, { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useData } from "../context/AppContext";
import { Loading } from "../components/Loading";
import { Difficulty } from "../components/Difficulty";
import { difficulties, percent, problemStatus } from "../utils";

export function Problems() {
  const result = useData("/problems");
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [level, setLevel] = useState("all");
  const [sort, setSort] = useState("id");

  return (
    <>
      <h1>Kho đề bài</h1>
      <p className="muted">Chọn một bài và gửi lời giải bằng Python, C++ hoặc Java.</p>
      <Loading result={result}>
        {(rows) => {
          // Lọc và sắp xếp hoàn toàn ở client trên danh sách đã tải.
          const text = query.trim().toLowerCase();
          const shown = rows
            .filter((p) => !text || p.title.toLowerCase().includes(text) || String(p.id).startsWith(text))
            .filter((p) => status === "all" || (p.status ?? "todo") === status)
            .filter((p) => level === "all" || p.difficulty === level)
            .sort((a, b) => {
              if (sort === "rate") return (b.ac_rate ?? -1) - (a.ac_rate ?? -1) || a.id - b.id;
              if (sort === "difficulty")
                return difficulties[a.difficulty].rank - difficulties[b.difficulty].rank || a.id - b.id;
              return a.id - b.id;
            });
          const solved = rows.filter((p) => p.status === "solved").length;
          return (
            <>
              <div className="toolbar">
                <input
                  type="search"
                  placeholder="Tìm theo tên hoặc số đề"
                  aria-label="Tìm đề"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
                <select
                  aria-label="Lọc trạng thái"
                  value={status}
                  onChange={(e) => setStatus(e.target.value)}
                >
                  <option value="all">Tất cả</option>
                  <option value="solved">Đã giải</option>
                  <option value="attempted">Đã thử</option>
                  <option value="todo">Chưa làm</option>
                </select>
                <select aria-label="Lọc độ khó" value={level} onChange={(e) => setLevel(e.target.value)}>
                  <option value="all">Mọi độ khó</option>
                  <option value="easy">Dễ</option>
                  <option value="medium">Trung bình</option>
                  <option value="hard">Khó</option>
                </select>
                <select aria-label="Sắp xếp" value={sort} onChange={(e) => setSort(e.target.value)}>
                  <option value="id">Theo số đề</option>
                  <option value="rate">Theo tỉ lệ AC</option>
                  <option value="difficulty">Theo độ khó</option>
                </select>
                <span className="solved-count">
                  <span className="status-solved">✓</span> {solved}/{rows.length} đã giải
                </span>
              </div>
              <div className="table-wrap">
                <table className="problem-table">
                  <thead>
                    <tr>
                      <th className="status-col">Trạng thái</th>
                      <th>Đề</th>
                      <th>Tỉ lệ AC</th>
                      <th>Độ khó</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((p) => (
                      // Cả hàng bấm được; link trong ô tên để dùng được bàn phím.
                      <tr key={p.id} className="row-link" onClick={() => navigate(`/problems/${p.id}`)}>
                        <td className="status-col" title={problemStatus[p.status]?.label ?? "Chưa làm"}>
                          <span className={`status-${p.status ?? "todo"}`}>
                            {problemStatus[p.status]?.icon ?? ""}
                          </span>
                        </td>
                        <td>
                          <Link to={`/problems/${p.id}`} onClick={(e) => e.stopPropagation()}>
                            {p.id}. {p.title}
                          </Link>
                          {p.hidden ? <span className="badge">Ẩn</span> : null}
                        </td>
                        <td>{percent(p.ac_rate)}</td>
                        <td>
                          <Difficulty value={p.difficulty} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {!shown.length && <p className="muted">Không có đề nào khớp bộ lọc.</p>}
            </>
          );
        }}
      </Loading>
    </>
  );
}
