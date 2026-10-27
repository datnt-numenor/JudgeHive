"""`POST /api/login`, `POST /api/logout`, `GET /api/me`, `GET /api/health` (README mục 3.3)."""

from fastapi import APIRouter, Depends, Request

from server import auth
from server.rest import sessions
from server.rest.body import body_default, utf8_size
from server.rest.errors import ApiJSONResponse, ApiError, invalid, no_content, rate_limited

router = APIRouter()

USERNAME_MAX_CHARS = 64


@router.get("/api/health")
def health():
    # Công khai cho healthcheck của compose. Chỉ trạng thái tối thiểu: không version, số worker hay đường dẫn.
    return ApiJSONResponse({"status": "ok"})


def _credentials(body):
    username, password = body.get("username"), body.get("password")
    if not isinstance(username, str) or not 1 <= len(username) <= USERNAME_MAX_CHARS:
        raise invalid("username")
    if not isinstance(password, str):
        raise invalid("password")
    utf8_size(username, "username")
    size = utf8_size(password, "password")
    # Mật khẩu quá dài hoặc có NUL không thể khớp mật khẩu nào, nhưng vẫn đi đường băm giả để thời gian trả lời như nhau.
    return username, password, size <= auth.MAX_PASSWORD_BYTES and "\x00" not in password


def _close_websockets(request, token_hash):
    """Đóng (4401) mọi WebSocket của một phiên vừa bị hủy. Gọi trong threadpool nên đi qua `call_soon_threadsafe`."""
    hub = getattr(request.app.state, "ws", None)
    if hub is not None:
        hub.close_session_threadsafe(token_hash)


@router.post("/api/login")
def login(request: Request, body: dict = Depends(body_default)):
    state = request.app.state.rest
    username, password, fits = _credentials(body)  # 400 và 413 không tính vào giới hạn
    if not state.login_gate.try_enter():
        raise rate_limited(1)
    try:
        wait = state.login_limiter.hit(username, state.clock())
        if wait:
            raise rate_limited(wait)  # kiểm tra trước mật khẩu, kể cả khi mật khẩu đúng
        row = state.db.conn().execute("SELECT id, username, password_hash, role FROM users WHERE username = ?",
                                      (username,)).fetchone()
        # Username không tồn tại hoặc mật khẩu quá dài vẫn băm một lần với cùng tham số: thời gian xấp xỉ nhau.
        if row is not None and fits:
            ok = auth.verify_password(password, row["password_hash"])
        else:
            auth.verify_password("x", state.dummy_hash)
            ok = False
    finally:
        state.login_gate.leave()
    if not ok:
        raise ApiError(401, "INVALID_CREDENTIALS", "invalid username or password")
    now = state.clock()
    sessions.purge_expired_sessions(state.db, now)
    previous = sessions.lookup(state.db, request.cookies.get(sessions.COOKIE_NAME), now)
    if previous is not None:
        sessions.delete_session(state.db, previous.token_hash)
        _close_websockets(request, previous.token_hash)  # README 4.5: WebSocket của phiên bị thay đóng 4401
    token = sessions.create_session(state.db, row["id"], now, state.cfg.session_ttl_s)
    user = {"id": row["id"], "username": row["username"], "role": row["role"]}
    return ApiJSONResponse({"user": user},
                           headers={"Set-Cookie": sessions.set_cookie_header(token, state.cfg.session_ttl_s)})


@router.post("/api/logout")
def logout(request: Request, user: sessions.SessionUser = Depends(sessions.current_user),
           body: dict = Depends(body_default)):
    sessions.delete_session(request.app.state.rest.db, user.token_hash)
    _close_websockets(request, user.token_hash)
    return no_content({"Set-Cookie": sessions.clear_cookie_header()})


@router.get("/api/me")
def me(user: sessions.SessionUser = Depends(sessions.current_user)):
    return ApiJSONResponse({"user": user.public()})
