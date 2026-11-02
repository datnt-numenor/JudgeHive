"""Phiên đăng nhập (README mục 3.2, D9): cookie `jh_session` chứa token ngẫu nhiên; DB chỉ lưu SHA-256 của token, nên
lộ DB không lộ phiên dùng được. Hạn tuyệt đối 7 ngày từ lúc đăng nhập (dùng không gia hạn). Quyền (`role`) luôn đọc
từ DB ở mỗi request, không cache.

Mọi hàm ở đây đụng SQLite và chạy trong threadpool của FastAPI (endpoint và dependency khai báo bằng `def`). Mỗi thread
của pool có kết nối riêng qua `Database.conn()` (mô hình PR 4: thread-local, không chia sẻ giữa thread); ghi dùng
`Database.write()`."""

import hashlib
import re
import secrets
from dataclasses import dataclass

from fastapi import Depends, Request

from server.rest.errors import forbidden, unauthenticated

COOKIE_NAME = "jh_session"
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")  # token_urlsafe(32)


@dataclass(frozen=True)
class SessionUser:
    id: int
    username: str
    role: str
    token_hash: str

    def public(self):
        return {"id": self.id, "username": self.username, "role": self.role}


def new_token():
    return secrets.token_urlsafe(32)


def hash_token(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def set_cookie_header(token, max_age_s):
    return f"{COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age_s}"


def clear_cookie_header():
    return f"{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"


def create_session(db, user_id, now_ms, ttl_s, expect=None):
    """Tạo phiên, hoặc trả None nếu tài khoản không còn đủ điều kiện (D30). Kiểm lại trong cùng transaction ghi:
    tài khoản còn tồn tại và chưa bị khóa; và nếu có `expect = (password_hash, role)` (đăng nhập truyền vào giá trị nó đã
    đọc trước khi băm mật khẩu) thì hai giá trị đó chưa đổi. Băm mất hàng trăm ms, nên nếu không kiểm lại thì một đăng nhập
    đang chạy có thể để lại phiên còn sống với mật khẩu hoặc quyền cũ sau khi admin vừa đổi chúng."""
    token = new_token()
    with db.write() as c:
        row = c.execute("SELECT password_hash, role, disabled FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None or row["disabled"]:
            return None
        if expect is not None and (row["password_hash"], row["role"]) != tuple(expect):
            return None
        c.execute("INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                  (hash_token(token), user_id, now_ms, now_ms + ttl_s * 1000))
    return token


def lookup(db, token, now_ms):
    """SessionUser của token còn hiệu lực, hoặc None (thiếu, sai định dạng, không có, hết hạn)."""
    if not token or not _TOKEN.fullmatch(token):
        return None
    digest = hash_token(token)
    row = db.conn().execute("SELECT u.id, u.username, u.role FROM sessions s JOIN users u ON u.id = s.user_id "
                            "WHERE s.token_hash = ? AND s.expires_at > ? AND u.disabled = 0",
                            (digest, now_ms)).fetchone()
    if row is None:
        return None
    return SessionUser(row["id"], row["username"], row["role"], digest)


def revoke_user_sessions(c, user_id):
    """Xóa mọi phiên của một người. `c` là connection đang trong transaction ghi (`db.write()`), để việc đổi mật khẩu,
    role hoặc khóa tài khoản và việc xóa phiên là một transaction (D30). Trả số phiên đã xóa."""
    return c.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,)).rowcount


def delete_session(db, token_hash):
    with db.write() as c:
        c.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))


def purge_expired_sessions(db, now_ms):
    """Xóa phiên hết hạn (gọi lúc đăng nhập). Trả số dòng đã xóa."""
    with db.write() as c:
        return c.execute("DELETE FROM sessions WHERE expires_at <= ?", (now_ms,)).rowcount


def current_user(request: Request):
    """Dependency: người gọi từ cookie, hoặc 401 `UNAUTHENTICATED`."""
    state = request.app.state.rest
    user = lookup(state.db, request.cookies.get(COOKIE_NAME), state.clock())
    if user is None:
        raise unauthenticated()
    return user


def require_admin(user: SessionUser = Depends(current_user)):
    """Dependency của mọi route `/api/admin/*` (README 3.6, D23): 401 khi thiếu phiên, 403 `FORBIDDEN` khi không phải admin.
    `lookup` đọc `role` từ DB ở mỗi request nên đổi role có hiệu lực ngay. Phải đứng TRƯỚC dependency đọc body trong chữ
    ký của route, để người không phải admin luôn nhận 403 dù body hỏng (không dò được gì từ 400, 413, 415, 404)."""
    if user.role != "admin":
        raise forbidden()
    return user
