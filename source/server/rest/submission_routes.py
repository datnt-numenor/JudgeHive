"""`POST /api/submissions`, `GET /api/submissions`, `GET /api/submissions/{id}` (README mục 3.3, D2, D8, D11)."""

import re
import urllib.parse

from fastapi import APIRouter, Depends, Request

from common import protocol
from server.dispatcher import LEVEL_NEW
from server.rest import sessions, views
from server.rest.body import body_submit, utf8_size
from server.rest.errors import ApiError, ApiJSONResponse, invalid, log, problem_not_found, rate_limited, \
    submission_not_found
from server.rest.problem_routes import PROBLEM_ID

router = APIRouter()

LANGUAGES = ("python", "cpp", "java")
CLIENT_TOKEN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
DIGITS = re.compile(r"[0-9]+")  # chỉ chữ số ASCII (int() chấp nhận cả chữ số Unicode, dấu, khoảng trắng)
PATH_ID = re.compile(r"[1-9][0-9]{0,18}")
SQLITE_MAX_INT = 2**63 - 1
LIST_DEFAULT_LIMIT, LIST_MAX_LIMIT = 50, 200


def accept_submission(state, user, problem_id, language, code, token):
    """Một transaction `BEGIN IMMEDIATE` theo đúng thứ tự README 3.2: trùng token, rồi đề, rồi rate limit, rồi ghi.
    Đếm rate limit bằng DB nên đúng sau restart và nguyên tử với việc ghi bài (không cần lock riêng).
    Trả `(id, state, received_ms, created)`."""
    now = state.clock()
    cfg = state.cfg
    window_ms = int(cfg.submit_rate_window_s * 1000)
    with state.db.write() as c:
        dup = c.execute("SELECT id, state, submitted_at FROM submissions WHERE user_id = ? AND client_token = ?",
                        (user.id, token)).fetchone()
        if dup is not None:
            return dup["id"], dup["state"], dup["submitted_at"], False
        problem = c.execute("SELECT hidden FROM problems WHERE id = ?", (problem_id,)).fetchone()
        if problem is None or (problem["hidden"] and user.role != "admin"):
            raise problem_not_found()
        # Bài thứ `limit` gần nhất mà còn trong cửa sổ: nếu có thì đã đủ `limit` bài; nộp được khi nó rời cửa sổ.
        edge = c.execute("SELECT submitted_at FROM submissions WHERE user_id = ? AND submitted_at > ? "
                         "ORDER BY submitted_at DESC LIMIT 1 OFFSET ?",
                         (user.id, now - window_ms, cfg.submit_rate_limit - 1)).fetchone()
        if edge is not None:
            raise rate_limited(-(-(edge["submitted_at"] + window_ms - now) // 1000))
        sid = c.execute("INSERT INTO submissions (user_id, problem_id, language, code, client_token, submitted_at, "
                        "state) VALUES (?, ?, ?, ?, ?, ?, 'queued')",
                        (user.id, problem_id, language, code, token, now)).lastrowid
    return sid, "queued", now, True


@router.post("/api/submissions")
def submit(request: Request, user: sessions.SessionUser = Depends(sessions.current_user),
           body: dict = Depends(body_submit)):
    state = request.app.state.rest
    problem_id, language, code, token = (body.get(k) for k in ("problem_id", "language", "code", "client_token"))
    if not isinstance(problem_id, str) or not PROBLEM_ID.fullmatch(problem_id):
        raise invalid("problem_id")
    if not isinstance(language, str) or language not in LANGUAGES:
        raise invalid("language")
    if not isinstance(code, str) or code == "":
        raise invalid("code")
    code_bytes = utf8_size(code, "code")
    if not isinstance(token, str) or not CLIENT_TOKEN.fullmatch(token.lower()):
        raise invalid("client_token")
    if code_bytes > protocol.MAX_CODE_BYTES:
        raise ApiError(413, "CODE_TOO_LARGE", "field 'code' is too large")
    sid, internal_state, received_ms, created = accept_submission(state, user, problem_id, language, code,
                                                                   token.lower())
    if created:
        # Bài đã nằm trong DB: process chết ngay sau commit thì lần khởi động sau `load_pending` nạp lại.
        state.dispatcher.enqueue(sid, LEVEL_NEW)
        _notify_queued(state, sid, user.id)
    return ApiJSONResponse({"submission_id": sid, "state": views.PUBLIC_STATE[internal_state],
                            "received_at": views.iso_ms(received_ms)}, status_code=201 if created else 200)


def _notify_queued(state, sid, user_id):
    try:
        state.hooks.status(sid, user_id, "queued")
    except Exception:
        log.exception("hook status lỗi")


def _int_param(params, name, high):
    values = params.get(name)
    if values is None:
        return None
    if len(values) != 1:  # tham số trùng: 400 (D21)
        raise invalid(name)
    raw = values[0]
    if not DIGITS.fullmatch(raw) or not 1 <= int(raw) <= high:
        raise invalid(name)
    return int(raw)


@router.get("/api/submissions")
def list_submissions(request: Request, user: sessions.SessionUser = Depends(sessions.current_user)):
    params = urllib.parse.parse_qs(request.scope["query_string"].decode("latin-1"), keep_blank_values=True)
    limit = _int_param(params, "limit", LIST_MAX_LIMIT)
    before_id = _int_param(params, "before_id", SQLITE_MAX_INT)
    limit = LIST_DEFAULT_LIMIT if limit is None else limit
    sql = (f"SELECT {views.LIST_COLUMNS} FROM submissions s JOIN problems p ON p.id = s.problem_id "
           "WHERE s.user_id = ?")
    args = [user.id]
    if before_id is not None:
        sql += " AND s.id < ?"
        args.append(before_id)
    rows = request.app.state.rest.db.conn().execute(sql + " ORDER BY s.id DESC LIMIT ?", (*args, limit + 1)).fetchall()
    page = rows[:limit]
    return ApiJSONResponse({"submissions": [views.submission_view(r) for r in page],
                            "next_before_id": page[-1]["id"] if len(rows) > limit else None})


@router.get("/api/submissions/{submission_id}")
def get_submission(submission_id: str, request: Request, user: sessions.SessionUser = Depends(sessions.current_user)):
    # Id sai định dạng, không tồn tại, và bài của người khác (kể cả admin gọi): cùng một 404.
    if not PATH_ID.fullmatch(submission_id) or int(submission_id) > SQLITE_MAX_INT:
        raise submission_not_found()
    conn = request.app.state.rest.db.conn()
    row = conn.execute(f"SELECT {views.DETAIL_COLUMNS} FROM submissions s JOIN problems p ON p.id = s.problem_id "
                       "WHERE s.id = ? AND s.user_id = ?", (int(submission_id), user.id)).fetchone()
    if row is None:
        raise submission_not_found()
    return ApiJSONResponse(views.detail_view(conn, row, user.role == "admin"))
