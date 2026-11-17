import React, { useState } from "react";
import { api } from "../api";
import { useApp, useData } from "../context/AppContext";
import { Loading } from "../components/Loading";
import { ErrorText } from "../components/ErrorText";

const EMPTY = {
  title: "",
  statement: "",
  difficulty: "medium",
  time_limit_ms: 1000,
  memory_mib: 256,
  hidden: false,
  tests: [{ input: "", output: "", is_sample: true }],
};

// Admin thêm/sửa đề ngay trên web (bộ test lớn vẫn có thể nạp bằng `python -m server.manage problem FILE.json`).
export function AdminProblems() {
  const { refresh } = useApp();
  const result = useData("/problems");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // editing: null = chưa chọn; "new" = thêm đề; số = id đề đang sửa.
  const [editing, setEditing] = useState(null);
  const [draft, setDraft] = useState(EMPTY);
  const [locked, setLocked] = useState(false);

  async function open(id) {
    setError("");
    if (id === "new") {
      setEditing("new");
      setDraft(EMPTY);
      setLocked(false);
      return;
    }
    try {
      const p = await api(`/admin/problems/${id}`);
      setEditing(id);
      setLocked(p.locked);
      setDraft({
        title: p.title,
        statement: p.statement,
        difficulty: p.difficulty,
        time_limit_ms: p.time_limit_ms,
        memory_mib: p.memory_limit_kib / 1024,
        hidden: !!p.hidden,
        tests: p.tests,
      });
    } catch (e) {
      setError(e.message);
    }
  }

  const set = (field) => (event) =>
    setDraft({
      ...draft,
      [field]: event.target.type === "checkbox" ? event.target.checked : event.target.value,
    });
  const setTest = (index, field, value) =>
    setDraft({ ...draft, tests: draft.tests.map((t, i) => (i === index ? { ...t, [field]: value } : t)) });

  async function save(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (locked) {
        // Đề đã có bài nộp: server chỉ cho đổi ẩn/hiện.
        await api(`/admin/problems/${editing}`, "PATCH", { hidden: draft.hidden });
      } else {
        const body = {
          title: draft.title,
          statement: draft.statement,
          difficulty: draft.difficulty,
          time_limit_ms: Number(draft.time_limit_ms),
          memory_limit_kib: Number(draft.memory_mib) * 1024,
          hidden: draft.hidden,
          tests: draft.tests,
        };
        const saved = await api(
          editing === "new" ? "/admin/problems" : `/admin/problems/${editing}`,
          editing === "new" ? "POST" : "PUT",
          body,
        );
        setEditing(saved.id);
      }
      refresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="page-heading">
        <h1>Quản lý đề bài</h1>
        <button onClick={() => open("new")}>+ Thêm đề</button>
      </div>
      <Loading result={result}>
        {(rows) => (
          <div className="admin-split">
            <section>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>ID</th>
                      <th>Tên đề</th>
                      <th>Hiển thị</th>
                      <th>Thao tác</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((p) => (
                      <tr key={p.id} className={editing === p.id ? "selected-row" : ""}>
                        <td>{String(p.id).padStart(2, "0")}</td>
                        <td>{p.title}</td>
                        <td className={p.hidden ? "amber" : ""}>{p.hidden ? "Ẩn" : "Công khai"}</td>
                        <td>
                          <button className="text-button" onClick={() => open(p.id)}>
                            Sửa
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="muted">Đề đang ẩn không hiện với USER và không tính vào bảng xếp hạng.</p>
            </section>
            <aside className="admin-panel">
              {editing === null ? (
                <>
                  <h2>Thông tin đề</h2>
                  <p className="muted">Chọn "Sửa" ở một đề, hoặc "+ Thêm đề" để tạo đề mới.</p>
                </>
              ) : (
                <form className="problem-form" onSubmit={save}>
                  <h2>{editing === "new" ? "Thêm đề mới" : `Sửa đề #${String(editing).padStart(2, "0")}`}</h2>
                  {locked && (
                    <p className="muted">
                      Đề đã có bài nộp nên chỉ đổi được ẩn/hiện. Muốn đổi nội dung hoặc bộ test, hãy tạo đề
                      mới.
                    </p>
                  )}
                  <ErrorText message={error} />
                  <fieldset disabled={busy}>
                    <label>
                      Tên đề
                      <input
                        value={draft.title}
                        onChange={set("title")}
                        required
                        maxLength={160}
                        readOnly={locked}
                      />
                    </label>
                    <label>
                      Độ khó
                      <select value={draft.difficulty} onChange={set("difficulty")} disabled={locked}>
                        <option value="easy">Dễ</option>
                        <option value="medium">Trung bình</option>
                        <option value="hard">Khó</option>
                      </select>
                    </label>
                    <div className="form-row">
                      <label>
                        Thời gian (ms)
                        <input
                          type="number"
                          min="100"
                          max="10000"
                          value={draft.time_limit_ms}
                          onChange={set("time_limit_ms")}
                          required
                          readOnly={locked}
                        />
                      </label>
                      <label>
                        Bộ nhớ (MiB)
                        <input
                          type="number"
                          min="64"
                          max="512"
                          value={draft.memory_mib}
                          onChange={set("memory_mib")}
                          required
                          readOnly={locked}
                        />
                      </label>
                    </div>
                    <label>
                      Nội dung đề
                      <textarea
                        rows="4"
                        value={draft.statement}
                        onChange={set("statement")}
                        required
                        readOnly={locked}
                      />
                    </label>
                    <label className="check">
                      <input type="checkbox" checked={draft.hidden} onChange={set("hidden")} />
                      Ẩn đề bài
                    </label>
                    <details className="tests-editor" open={editing === "new"}>
                      <summary>Ví dụ và bộ test ({draft.tests.length})</summary>
                      {draft.tests.map((t, i) => (
                        <div className="test-case" key={i}>
                          <div className="test-case-head">
                            <strong>Test {i + 1}</strong>
                            <label className="check">
                              <input
                                type="checkbox"
                                checked={t.is_sample}
                                disabled={locked}
                                onChange={(e) => setTest(i, "is_sample", e.target.checked)}
                              />
                              Ví dụ (USER xem được)
                            </label>
                            {!locked && draft.tests.length > 1 && (
                              <button
                                type="button"
                                className="text-button"
                                onClick={() =>
                                  setDraft({ ...draft, tests: draft.tests.filter((_, j) => j !== i) })
                                }
                              >
                                Xóa
                              </button>
                            )}
                          </div>
                          <div className="form-row">
                            <label>
                              Dữ liệu vào
                              <textarea
                                rows="2"
                                spellCheck="false"
                                value={t.input}
                                readOnly={locked}
                                onChange={(e) => setTest(i, "input", e.target.value)}
                              />
                            </label>
                            <label>
                              Kết quả ra
                              <textarea
                                rows="2"
                                spellCheck="false"
                                value={t.output}
                                readOnly={locked}
                                onChange={(e) => setTest(i, "output", e.target.value)}
                              />
                            </label>
                          </div>
                        </div>
                      ))}
                      {!locked && draft.tests.length < 20 && (
                        <button
                          type="button"
                          className="outline"
                          onClick={() =>
                            setDraft({
                              ...draft,
                              tests: [...draft.tests, { input: "", output: "", is_sample: false }],
                            })
                          }
                        >
                          + Thêm test
                        </button>
                      )}
                      <p className="muted">
                        Tối đa 20 test, tổng 1 MiB. Bộ test lớn hơn: nạp file JSON bằng lệnh server.manage.
                      </p>
                    </details>
                    <div className="actions">
                      <button>{busy ? "Đang lưu…" : editing === "new" ? "Tạo đề" : "Lưu thay đổi"}</button>
                      <button type="button" className="outline" onClick={() => setEditing(null)}>
                        Hủy
                      </button>
                    </div>
                  </fieldset>
                </form>
              )}
            </aside>
          </div>
        )}
      </Loading>
    </>
  );
}
