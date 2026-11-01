"""Endpoint của admin dưới `/api/admin/` (README mục 3.6, D23).

Mọi route khai báo `Depends(sessions.require_admin)` TRƯỚC `Depends(body_default)`: người không phải admin nhận 403 trước
mọi lỗi của body. Body của nhóm này từ chối field thừa. Endpoint là `def` nên chạy trong threadpool: chỉ gọi hook
(`state.hooks`), không bao giờ gọi thẳng `ScoreboardService.mark_dirty` (hàm đó chỉ chạy trên event loop)."""

from fastapi import APIRouter, Depends, Request

from server.rest import sessions
from server.rest.body import body_default
from server.rest.errors import ApiJSONResponse, invalid, log, problem_not_found
from server.rest.problem_routes import PROBLEM_COLUMNS, PROBLEM_ID, summary

router = APIRouter()


@router.patch("/api/admin/problems/{problem_id}")
def set_problem_hidden(problem_id: str, request: Request, admin: sessions.SessionUser = Depends(sessions.require_admin),
                       body: dict = Depends(body_default)):
    state = request.app.state.rest
    hidden = body.get("hidden")
    if set(body) != {"hidden"} or type(hidden) is not bool:  # boolean thật; field thừa hay thiếu đều 400
        raise invalid("hidden")
    if not PROBLEM_ID.fullmatch(problem_id):
        raise problem_not_found()
    with state.db.write() as c:
        row = c.execute(f"SELECT {PROBLEM_COLUMNS} FROM problems WHERE id = ?", (problem_id,)).fetchone()
        if row is None:
            raise problem_not_found()
        changed = bool(row["hidden"]) != hidden
        if changed:
            c.execute("UPDATE problems SET hidden = ? WHERE id = ?", (1 if hidden else 0, problem_id))
    if changed:
        log.info("ADMIN admin=%s hidden problem=%s %s -> %s", admin.id, problem_id, not hidden, hidden)
        try:
            state.hooks.scoreboard_dirty()  # sau commit, nên lần tính kế tiếp thấy giá trị mới
        except Exception:
            log.exception("hook scoreboard_dirty lỗi")
    view = summary(row, True)
    view["hidden"] = hidden
    return ApiJSONResponse(view)
