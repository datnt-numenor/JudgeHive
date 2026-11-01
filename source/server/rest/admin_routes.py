"""Endpoint của admin dưới `/api/admin/` (README mục 3.6, D23).

Mọi route khai báo `Depends(sessions.require_admin)` TRƯỚC `Depends(body_default)`: người không phải admin nhận 403 trước
mọi lỗi của body. Body của nhóm này từ chối field thừa. Endpoint là `def` nên chạy trong threadpool: chỉ gọi hook
(`state.hooks`), không bao giờ gọi thẳng `ScoreboardService.mark_dirty` (hàm đó chỉ chạy trên event loop)."""

import urllib.parse

from fastapi import APIRouter, Depends, Request

from server.rest import sessions, views
from server.rest.body import body_default
from server.rest.errors import ApiJSONResponse, invalid, log, problem_not_found
from server.rest.parse import INT64_MAX, parse_uint
from server.rest.problem_routes import PROBLEM_COLUMNS, PROBLEM_ID, summary
from server.rest.submission_routes import LIST_DEFAULT_LIMIT, LIST_MAX_LIMIT

PUBLIC_TO_INTERNAL_STATE = {public: internal for internal, public in views.PUBLIC_STATE.items()}

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


def _strict_query(request, allowed):
    """Tham số query của nhóm admin: tên lạ hoặc trùng là 400 (README 3.6), chặt hơn các endpoint thí sinh. Trả dict
    tên -> chuỗi. Giá trị rỗng được giữ nguyên để từng tham số tự từ chối."""
    params = urllib.parse.parse_qs(request.scope["query_string"].decode("latin-1"), keep_blank_values=True)
    for name, values in params.items():
        if name not in allowed or len(values) != 1:
            raise invalid(name if name in allowed else None)
    return {name: values[0] for name, values in params.items()}


def _uint_param(raw, name, low, high):
    value = parse_uint(raw, low=low, high=high)
    if value is None:
        raise invalid(name)
    return value


QUEUE_COUNTS_SQL = ("SELECT "
                    "COALESCE(SUM(state = 'queued' AND is_rejudge = 0), 0), "
                    "COALESCE(SUM(state = 'queued' AND is_rejudge = 1), 0), "
                    "COALESCE(SUM(state = 'judging'), 0) FROM submissions WHERE state IN ('queued', 'judging')")


@router.get("/api/admin/workers")
def list_workers(request: Request, admin: sessions.SessionUser = Depends(sessions.require_admin)):
    _strict_query(request, ())
    workers = request.app.state.worker_server.admin_view()  # RAM, dưới lock ngắn của WorkerServer
    queued, rejudge_queued, judging = request.app.state.rest.db.conn().execute(QUEUE_COUNTS_SQL).fetchone()
    return ApiJSONResponse({"workers": workers,
                            "queue": {"queued": queued, "rejudge_queued": rejudge_queued, "judging": judging}})


@router.get("/api/admin/submissions")
def list_admin_submissions(request: Request, admin: sessions.SessionUser = Depends(sessions.require_admin)):
    q = _strict_query(request, ("state", "problem_id", "user_id", "before_id", "limit"))
    where, args = [], []
    if "state" in q:
        internal = PUBLIC_TO_INTERNAL_STATE.get(q["state"])
        if internal is None:
            raise invalid("state")
        where.append("s.state = ?")
        args.append(internal)
    if "problem_id" in q:
        if not PROBLEM_ID.fullmatch(q["problem_id"]):
            raise invalid("problem_id")
        where.append("s.problem_id = ?")
        args.append(q["problem_id"])
    if "user_id" in q:
        where.append("s.user_id = ?")
        args.append(_uint_param(q["user_id"], "user_id", 1, INT64_MAX))
    if "before_id" in q:
        where.append("s.id < ?")
        args.append(_uint_param(q["before_id"], "before_id", 1, INT64_MAX))
    limit = _uint_param(q["limit"], "limit", 1, LIST_MAX_LIMIT) if "limit" in q else LIST_DEFAULT_LIMIT
    sql = (f"SELECT {views.ADMIN_LIST_COLUMNS} FROM submissions s JOIN problems p ON p.id = s.problem_id "
           "JOIN users u ON u.id = s.user_id" + (" WHERE " + " AND ".join(where) if where else "")
           + " ORDER BY s.id DESC LIMIT ?")
    rows = request.app.state.rest.db.conn().execute(sql, (*args, limit + 1)).fetchall()
    page = rows[:limit]
    return ApiJSONResponse({"submissions": [views.admin_submission_view(r) for r in page],
                            "next_before_id": page[-1]["id"] if len(rows) > limit else None})
