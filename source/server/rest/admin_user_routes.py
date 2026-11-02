"""Quản lý người dùng của admin dưới `/api/admin/users` (README 3.6, D30).

Cùng quy ước với `admin_routes.py`: mọi route khai báo `Depends(sessions.require_admin)` TRƯỚC `Depends(body_default)`, nên
người không phải admin nhận 403 trước mọi lỗi của body. Endpoint là `def` (threadpool): chỉ chuyển sang event loop bằng hook
(`state.hooks.scoreboard_dirty`) và `hub.close_user_threadsafe` (`call_soon_threadsafe` một hàm đồng bộ), không gọi coroutine
hay `ScoreboardService` trực tiếp từ thread.

Thứ tự mỗi thao tác ghi: kiểm body (400) → kiểm id (404) → băm mật khẩu NGOÀI transaction (scrypt mất hàng trăm ms, còn
`BEGIN IMMEDIATE` là khóa ghi duy nhất của cả dispatcher) → một transaction ghi (kiểm lại admin đang gọi vẫn là admin hoạt động: 403; đọc lại
đối tượng, kiểm 404 và 409, ghi, xóa phiên) → sau commit: đóng WebSocket 4401, `scoreboard_dirty`, log. Không bao giờ ghi mật khẩu, hash hay body vào response hoặc log."""

import sqlite3

from fastapi import APIRouter, Depends, Request

from server import auth
from server.rest import sessions, views
from server.rest.admin_routes import _strict_query, _uint_param
from server.rest.body import body_default, utf8_size
from server.rest.errors import (ApiJSONResponse, forbidden, invalid, last_admin, log, no_content, user_not_found,
                                username_taken)
from server.rest.parse import INT64_MAX, parse_uint
from server.rest.submission_routes import LIST_DEFAULT_LIMIT, LIST_MAX_LIMIT

router = APIRouter()

USER_COLUMNS = "id, username, role, disabled, created_at"
ROLES = ("user", "admin")


def user_view(row):
    """Whitelist từng cột theo tên: không bao giờ có `password_hash`."""
    return {"id": row["id"], "username": row["username"], "role": row["role"], "disabled": bool(row["disabled"]),
            "created_at": views.iso_ms(row["created_at"])}


def _password(body):
    password = body.get("password")
    if not isinstance(password, str):
        raise invalid("password")
    utf8_size(password, "password")  # surrogate lẻ là 400, không phải 500
    try:
        auth.check_password_length(password)
    except auth.PasswordError:
        raise invalid("password") from None
    return password


def _user_id(raw):
    """Id sai định dạng và tài khoản không tồn tại là cùng một 404."""
    uid = parse_uint(raw, low=1, canonical=True)
    if uid is None:
        raise user_not_found()
    return uid


def _close_websockets(request, user_id):
    hub = getattr(request.app.state, "ws", None)
    if hub is not None:
        hub.close_user_threadsafe(user_id)  # 4401, không bao giờ ném


def _scoreboard_dirty(state):
    try:
        state.hooks.scoreboard_dirty()  # sau commit, nên lần tính kế tiếp thấy giá trị mới
    except Exception:
        log.exception("hook scoreboard_dirty lỗi")


def _fetch(conn, uid):
    return conn.execute(f"SELECT {USER_COLUMNS} FROM users WHERE id = ?", (uid,)).fetchone()


def _require_active_admin(c, admin_id):
    """Kiểm lại TRONG transaction ghi rằng người gọi vẫn là admin hoạt động. `require_admin` chạy ngoài transaction, nên một
    admin vừa bị hạ quyền hoặc khóa bởi request khác (nhưng request của họ đã qua `require_admin`) phải bị chặn ở đây: 403,
    không ghi gì (README 3.6). Gọi ngay đầu mỗi `with state.db.write()` để 403 đứng trước 404 và 409."""
    row = c.execute("SELECT 1 FROM users WHERE id = ? AND role = 'admin' AND disabled = 0", (admin_id,)).fetchone()
    if row is None:
        raise forbidden()


@router.get("/api/admin/users")
def list_users(request: Request, admin: sessions.SessionUser = Depends(sessions.require_admin)):
    q = _strict_query(request, ("before_id", "limit"))
    where, args = "", []
    if "before_id" in q:
        where = " WHERE id < ?"
        args.append(_uint_param(q["before_id"], "before_id", 1, INT64_MAX))
    limit = _uint_param(q["limit"], "limit", 1, LIST_MAX_LIMIT) if "limit" in q else LIST_DEFAULT_LIMIT
    rows = request.app.state.rest.db.conn().execute(
        f"SELECT {USER_COLUMNS} FROM users{where} ORDER BY id DESC LIMIT ?", (*args, limit + 1)).fetchall()
    page = rows[:limit]
    return ApiJSONResponse({"users": [user_view(r) for r in page],
                            "next_before_id": page[-1]["id"] if len(rows) > limit else None})


@router.post("/api/admin/users")
def create_user(request: Request, admin: sessions.SessionUser = Depends(sessions.require_admin),
                body: dict = Depends(body_default)):
    state = request.app.state.rest
    if not set(body) <= {"username", "password", "role"}:
        raise invalid()
    username = body.get("username")
    if not auth.is_valid_username(username):  # cùng quy tắc với `manage.py users create`, kiểm bằng fullmatch
        raise invalid("username")
    password = _password(body)
    role = body.get("role", "user")
    if not isinstance(role, str) or role not in ROLES:
        raise invalid("role")
    stored = auth.hash_password(password)  # ngoài transaction
    try:
        with state.db.write() as c:
            _require_active_admin(c, admin.id)
            uid = c.execute("INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                            (username, stored, role, state.clock())).lastrowid
            created = _fetch(c, uid)
    except sqlite3.IntegrityError:
        raise username_taken() from None
    log.info("ADMIN admin=%s create user=%s role=%s", admin.id, uid, role)
    return ApiJSONResponse(user_view(created), status_code=201)


@router.patch("/api/admin/users/{user_id}")
def update_user(user_id: str, request: Request, admin: sessions.SessionUser = Depends(sessions.require_admin),
                body: dict = Depends(body_default)):
    state = request.app.state.rest
    disabled, role = body.get("disabled"), body.get("role")
    if not body or not set(body) <= {"disabled", "role"}:
        raise invalid()
    if "disabled" in body and type(disabled) is not bool:  # boolean JSON thật: 0, 1, chuỗi, null bị từ chối
        raise invalid("disabled")
    if "role" in body and (not isinstance(role, str) or role not in ROLES):
        raise invalid("role")
    uid = _user_id(user_id)
    with state.db.write() as c:
        _require_active_admin(c, admin.id)
        row = _fetch(c, uid)
        if row is None:
            raise user_not_found()
        new_disabled = row["disabled"] if "disabled" not in body else (1 if disabled else 0)
        new_role = row["role"] if "role" not in body else role
        disabled_changed, role_changed = new_disabled != row["disabled"], new_role != row["role"]
        if not (disabled_changed or role_changed):
            return ApiJSONResponse(user_view(row))  # đặt đúng giá trị đang có: không ghi, không xóa phiên, không log
        was_active_admin = row["role"] == "admin" and not row["disabled"]
        will_be_active_admin = new_role == "admin" and not new_disabled
        if was_active_admin and not will_be_active_admin:
            # Đếm trong cùng transaction `BEGIN IMMEDIATE`: hai PATCH đồng thời không thể cùng làm mất admin cuối cùng.
            others = c.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND disabled = 0 AND id != ?",
                               (uid,)).fetchone()[0]
            if others == 0:
                raise last_admin()
        c.execute("UPDATE users SET disabled = ?, role = ? WHERE id = ?", (new_disabled, new_role, uid))
        # Khóa hoặc đổi role (kể cả nâng lên admin) xóa mọi phiên; mở khóa thì không (khóa đã xóa hết rồi). D30(3)
        revoke = role_changed or (disabled_changed and new_disabled == 1)
        if revoke:
            sessions.revoke_user_sessions(c, uid)
        updated = _fetch(c, uid)
    if revoke:
        _close_websockets(request, uid)
    if role_changed:
        _scoreboard_dirty(state)  # admin không có hạng; khóa không đổi bảng (D23(5))
    log.info("ADMIN admin=%s update user=%s role=%s->%s disabled=%s->%s", admin.id, uid, row["role"], new_role,
             row["disabled"], new_disabled)
    headers = {"Set-Cookie": sessions.clear_cookie_header()} if revoke and uid == admin.id else None
    return ApiJSONResponse(user_view(updated), headers=headers)


@router.post("/api/admin/users/{user_id}/password")
def set_user_password(user_id: str, request: Request, admin: sessions.SessionUser = Depends(sessions.require_admin),
                      body: dict = Depends(body_default)):
    state = request.app.state.rest
    if set(body) != {"password"}:
        raise invalid()
    password = _password(body)
    uid = _user_id(user_id)
    if _fetch(state.db.conn(), uid) is None:
        raise user_not_found()  # trước khi băm: id lạ không tốn một lần scrypt
    stored = auth.hash_password(password)  # ngoài transaction: scrypt không được giữ khóa ghi
    with state.db.write() as c:
        _require_active_admin(c, admin.id)
        if _fetch(c, uid) is None:
            raise user_not_found()
        c.execute("UPDATE users SET password_hash = ? WHERE id = ?", (stored, uid))
        sessions.revoke_user_sessions(c, uid)
    _close_websockets(request, uid)
    log.info("ADMIN admin=%s set-password user=%s", admin.id, uid)
    return no_content({"Set-Cookie": sessions.clear_cookie_header()} if uid == admin.id else None)
