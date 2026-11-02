import { useState } from "react";
import { api } from "../lib/api.js";
import { errorText } from "../lib/format.js";

/**
 * Nút ẩn/hiện một đề (README 3.6, `PATCH /api/admin/problems/{id}`). Ẩn phải xác nhận vì bảng xếp hạng thôi tính các bài
 * trên đề đó; hiện lại thì không hỏi. Không cập nhật lạc quan: giao diện chỉ đổi theo đối tượng đề server trả về.
 * `onUpdated(đề)`: đề mới từ server. `onGone()`: đề không còn (404), người gọi nên tải lại danh sách.
 */
export default function HideToggle({ problem, onUpdated, onGone }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const click = async () => {
    const hide = !problem.hidden;
    if (
      hide &&
      !window.confirm(
        `Ẩn đề "${problem.title}"? Người khác sẽ không thấy đề này nữa và bảng xếp hạng thôi tính các bài trên đề. ` +
          "Bài đã nộp vẫn được chấm xong.",
      )
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      onUpdated(await api.setProblemHidden(problem.id, hide));
    } catch (e) {
      if (e?.code === "PROBLEM_NOT_FOUND") onGone();
      else setError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <button type="button" onClick={click} disabled={busy}>
        {problem.hidden ? "Hiện đề" : "Ẩn đề"}
      </button>
      {error && (
        <span role="alert" className="error">
          {" "}
          {errorText(error)}
        </span>
      )}
    </>
  );
}
