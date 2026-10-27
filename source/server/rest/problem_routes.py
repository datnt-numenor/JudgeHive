"""`GET /api/problems`, `GET /api/problems/{id}` (README mục 3.3, D8: đề ẩn)."""

import re

from fastapi import APIRouter, Depends, Request

from server.rest import sessions
from server.rest.errors import ApiJSONResponse, problem_not_found

router = APIRouter()

PROBLEM_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
PROBLEM_COLUMNS = "id, title, time_limit_ms, memory_limit_kib, hidden"


def _summary(row, is_admin):
    return {"id": row["id"], "title": row["title"], "time_limit_ms": row["time_limit_ms"],
            "memory_limit_kib": row["memory_limit_kib"], "hidden": bool(row["hidden"]) if is_admin else False}


def _text(value):
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


@router.get("/api/problems")
def list_problems(request: Request, user: sessions.SessionUser = Depends(sessions.current_user)):
    is_admin = user.role == "admin"
    where = "" if is_admin else " WHERE hidden = 0"
    rows = request.app.state.rest.db.conn().execute(
        f"SELECT {PROBLEM_COLUMNS} FROM problems{where} ORDER BY id").fetchall()
    return ApiJSONResponse({"problems": [_summary(r, is_admin) for r in rows]})


@router.get("/api/problems/{problem_id}")
def get_problem(problem_id: str, request: Request, user: sessions.SessionUser = Depends(sessions.current_user)):
    # Id sai định dạng, không tồn tại, và đề ẩn với người không phải admin: cùng một 404, cùng message.
    if not PROBLEM_ID.fullmatch(problem_id):
        raise problem_not_found()
    conn = request.app.state.rest.db.conn()
    row = conn.execute(f"SELECT {PROBLEM_COLUMNS}, statement FROM problems WHERE id = ?", (problem_id,)).fetchone()
    is_admin = user.role == "admin"
    if row is None or (row["hidden"] and not is_admin):
        raise problem_not_found()
    samples = conn.execute("SELECT input, output FROM tests WHERE problem_id = ? AND is_sample = 1 ORDER BY position",
                           (problem_id,)).fetchall()
    view = _summary(row, is_admin)
    view["statement"] = row["statement"]
    view["samples"] = [{"input": _text(s["input"]), "output": _text(s["output"])} for s in samples]
    return ApiJSONResponse(view)
