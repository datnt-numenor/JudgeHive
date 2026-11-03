"""Rejudge của admin dưới `/api/admin/` (README 3.6, D6, D22, D36).

Hai endpoint chỉ KHỞI ĐỘNG rejudge; việc chấm và ghi verdict mới là của dispatcher (không sửa ở đây). Mỗi bài `done` được
đưa vào hàng rejudge bằng hai việc: ghi DB trong transaction (`state = 'queued'`, `is_rejudge = 1`, đặt lại cả
`delivery_count` lẫn `total_deliveries`, giữ nguyên verdict, D6) rồi, SAU commit, `dispatcher.enqueue(id, LEVEL_REJUDGE)`
để bài vào `PriorityQueue` trong bộ nhớ ngay (không đợi restart). Crash giữa hai bước không mất bài: `load_pending` nạp lại
mọi bài `queued`. Bài đang `judging` chỉ được gắn `rejudge_requested = 1` (dispatcher tự xếp hàng khi `RESULT` commit và
đặt lại bộ đếm lúc đó). D6: xếp hàng rejudge KHÔNG đẩy `STATUS` và không gọi `scoreboard_dirty` (verdict chưa đổi).

Rejudge cả đề chia lô để không giữ khóa ghi duy nhất của cả dispatcher quá lâu: mỗi lô một transaction, tối đa
`BATCH_MAX` bài mỗi request."""

from fastapi import APIRouter, Depends, Request

from server.dispatcher import LEVEL_REJUDGE
from server.rest import sessions
from server.rest.admin_user_routes import _require_active_admin
from server.rest.body import body_default
from server.rest.errors import ApiJSONResponse, invalid, log, problem_not_found, submission_not_found
from server.rest.parse import parse_uint
from server.rest.problem_routes import PROBLEM_ID

router = APIRouter()

BATCH_MAX = 1000  # D36 [ĐỀ XUẤT]: số bài tối đa xử lý mỗi request rejudge cả đề
BATCH_SIZE = 100  # D36 [ĐỀ XUẤT]: số bài mỗi transaction

# Bài cần làm gì đó: đã có kết quả, hoặc đang chấm mà chưa gắn cờ. Bài `queued` và bài đang chấm đã gắn cờ thì không.
ACTIONABLE = "(state = 'done' OR (state = 'judging' AND rejudge_requested = 0))"


def _apply(c, sid):
    """Trong transaction ghi: đưa một bài vào rejudge theo trạng thái HIỆN TẠI (đọc lại trong transaction, vì trạng thái
    có thể đã đổi từ lúc chọn). Trả `queued` (đã xếp hàng; người gọi phải enqueue sau commit), `after_current` hoặc
    `already_queued`."""
    row = c.execute("SELECT state, rejudge_requested FROM submissions WHERE id = ?", (sid,)).fetchone()
    if row is None:
        return None
    if row["state"] == "done":
        c.execute("UPDATE submissions SET state = 'queued', is_rejudge = 1, rejudge_requested = 0, delivery_count = 0, "
                  "total_deliveries = 0 WHERE id = ?", (sid,))
        return "queued"
    if row["state"] == "judging":
        if not row["rejudge_requested"]:
            c.execute("UPDATE submissions SET rejudge_requested = 1 WHERE id = ?", (sid,))
        return "after_current"
    return "already_queued"


def _run(state, admin_id, ids):
    """Xử lý `ids` thành các lô, mỗi lô một transaction; enqueue sau mỗi lô đã commit. Trả bộ đếm theo kết quả."""
    counts = {"queued": 0, "after_current": 0, "already_queued": 0}
    for start in range(0, len(ids), BATCH_SIZE):
        queued = []
        with state.db.write() as c:
            _require_active_admin(c, admin_id)
            for sid in ids[start:start + BATCH_SIZE]:
                outcome = _apply(c, sid)
                if outcome is None:
                    continue
                counts[outcome] += 1
                if outcome == "queued":
                    queued.append(sid)
        for sid in queued:
            state.dispatcher.enqueue(sid, LEVEL_REJUDGE)  # bộ nhớ của dispatcher, như luồng nộp bài
    return counts


@router.post("/api/admin/submissions/{submission_id}/rejudge")
def rejudge_submission(submission_id: str, request: Request,
                       admin: sessions.SessionUser = Depends(sessions.require_admin),
                       body: dict = Depends(body_default)):
    state = request.app.state.rest
    if body:
        raise invalid()
    sid = parse_uint(submission_id, low=1, canonical=True)  # id sai định dạng và bài không tồn tại: cùng một 404
    if sid is None:
        raise submission_not_found()
    queued = []
    with state.db.write() as c:
        _require_active_admin(c, admin.id)
        outcome = _apply(c, sid)
        if outcome is None:
            raise submission_not_found()
        if outcome == "queued":
            queued.append(sid)
    for item in queued:
        state.dispatcher.enqueue(item, LEVEL_REJUDGE)
    log.info("ADMIN admin=%s rejudge submission=%s outcome=%s", admin.id, sid, outcome)
    return ApiJSONResponse({"submission_id": sid, "outcome": outcome})


@router.post("/api/admin/problems/{problem_id}/rejudge")
def rejudge_problem(problem_id: str, request: Request, admin: sessions.SessionUser = Depends(sessions.require_admin),
                    body: dict = Depends(body_default)):
    state = request.app.state.rest
    if body:
        raise invalid()
    if not PROBLEM_ID.fullmatch(problem_id):
        raise problem_not_found()
    conn = state.db.conn()
    if conn.execute("SELECT 1 FROM problems WHERE id = ?", (problem_id,)).fetchone() is None:
        raise problem_not_found()
    already_queued, flagged_before = conn.execute(
        "SELECT COALESCE(SUM(state = 'queued'), 0), COALESCE(SUM(state = 'judging' AND rejudge_requested = 1), 0) "
        "FROM submissions WHERE problem_id = ?", (problem_id,)).fetchone()
    ids = [r["id"] for r in conn.execute(
        f"SELECT id FROM submissions WHERE problem_id = ? AND {ACTIONABLE} ORDER BY id LIMIT ?",
        (problem_id, BATCH_MAX))]
    counts = _run(state, admin.id, ids)
    remaining = state.db.conn().execute(
        f"SELECT COUNT(*) FROM submissions WHERE problem_id = ? AND {ACTIONABLE}", (problem_id,)).fetchone()[0]
    result = {"problem_id": problem_id, "queued": counts["queued"],
              "after_current": counts["after_current"] + flagged_before,
              "already_queued": already_queued, "remaining": remaining}
    log.info("ADMIN admin=%s rejudge problem=%s queued=%d after_current=%d already_queued=%d remaining=%d", admin.id,
             problem_id, result["queued"], result["after_current"], result["already_queued"], remaining)
    return ApiJSONResponse(result)
