"""Nền quản lý người dùng (D30): tài khoản bị khóa, kiểm lại khi tạo phiên, hủy phiên theo người (README 3.2, 3.3).

Chưa có endpoint nào đặt `disabled` (PR sau), nên test đặt cột bằng SQL và giả lập admin đổi thông tin bằng cách ghi thẳng DB."""

import threading

import pytest

from _rest_support import PASSWORD, add_account
from server import auth
from server.db import Database
from server.rest import sessions

NOW = 1_800_000_000_000
TTL = 3600


def set_column(db, name, column, value):
    with db.write() as c:
        c.execute(f"UPDATE users SET {column} = ? WHERE username = ?", (value, name))


def session_count(db, name):
    return db.conn().execute("SELECT COUNT(*) FROM sessions s JOIN users u ON u.id = s.user_id WHERE u.username = ?",
                             (name,)).fetchone()[0]


# ---- đăng nhập và phiên của tài khoản bị khóa --------------------------------------------------------------------

def test_a_disabled_account_gets_the_same_401_as_a_wrong_password_even_with_the_right_password(web):
    wrong = web.post("/api/login", {"username": "alice", "password": "wrong-password"})
    set_column(web.db, "alice", "disabled", 1)
    r = web.post("/api/login", {"username": "alice", "password": PASSWORD})
    assert (r.status, r.error_code) == (401, "INVALID_CREDENTIALS")
    assert r.body == wrong.body and "set-cookie" not in r.headers
    assert session_count(web.db, "alice") == 0


def test_a_disabled_account_still_pays_for_a_real_hash_check(web, monkeypatch):
    """Thời gian trả lời xấp xỉ mật khẩu sai: server vẫn gọi `verify_password` với hash thật của tài khoản."""
    seen = []
    real = auth.verify_password

    def spy(password, stored):
        seen.append(stored)
        return real(password, stored)

    monkeypatch.setattr(auth, "verify_password", spy)
    set_column(web.db, "alice", "disabled", 1)
    stored = web.db.conn().execute("SELECT password_hash FROM users WHERE username = 'alice'").fetchone()[0]
    assert web.post("/api/login", {"username": "alice", "password": PASSWORD}).status == 401
    assert seen == [stored]


def test_enabling_the_account_again_lets_it_log_in(web):
    set_column(web.db, "alice", "disabled", 1)
    assert web.post("/api/login", {"username": "alice", "password": PASSWORD}).status == 401
    set_column(web.db, "alice", "disabled", 0)
    assert web.post("/api/login", {"username": "alice", "password": PASSWORD}).status == 200


def test_an_existing_session_of_a_disabled_account_stops_working_and_comes_back_only_with_the_account(web):
    cookie = web.login("alice")
    assert web.get("/api/me", cookie=cookie).status == 200
    set_column(web.db, "alice", "disabled", 1)
    r = web.get("/api/me", cookie=cookie)
    assert (r.status, r.error_code) == (401, "UNAUTHENTICATED")
    assert web.get("/api/problems", cookie=cookie).status == 401
    assert web.post("/api/logout", {}, cookie=cookie).status == 401
    # Giả lập bằng SQL: cột `disabled` đổi mà phiên không bị xóa, nên mở lại là dùng được tiếp. Đây KHÔNG phải hành vi cuối:
    # khóa qua endpoint admin (PR sau) xóa mọi phiên của người đó (D30(3)). Dòng này chỉ ghim rằng `lookup` lọc theo cột.
    set_column(web.db, "alice", "disabled", 0)
    assert web.get("/api/me", cookie=cookie).status == 200


def test_only_the_disabled_account_is_affected(web):
    bob = web.login("bob")
    set_column(web.db, "alice", "disabled", 1)
    assert web.get("/api/me", cookie=bob).status == 200
    assert web.post("/api/login", {"username": "bob", "password": PASSWORD}).status == 200


# ---- create_session kiểm lại trong cùng transaction ---------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "d30.db"))
    database.migrate()
    add_account(database, "alice")
    add_account(database, "bob")
    return database


def stored_of(db, name):
    return tuple(db.conn().execute("SELECT password_hash, role FROM users WHERE username = ?", (name,)).fetchone())


def alice_id(db):
    return db.conn().execute("SELECT id FROM users WHERE username = 'alice'").fetchone()[0]


def test_create_session_with_matching_expectation_creates_the_session(db):
    token = sessions.create_session(db, alice_id(db), NOW, TTL, expect=stored_of(db, "alice"))
    assert token and session_count(db, "alice") == 1
    assert sessions.lookup(db, token, NOW + 1).username == "alice"


def test_create_session_without_expectation_still_creates_it(db):
    assert sessions.create_session(db, alice_id(db), NOW, TTL)


@pytest.mark.parametrize("change", [
    ("password_hash", "scrypt$n=16384,r=8,p=1$AAAA$BBBB"),
    ("role", "admin"),
    ("disabled", 1),
], ids=["password-changed", "role-changed", "disabled"])
def test_create_session_refuses_when_the_account_changed_after_it_was_read(db, change):
    expect = stored_of(db, "alice")
    set_column(db, "alice", *change)
    assert sessions.create_session(db, alice_id(db), NOW, TTL, expect=expect) is None
    assert session_count(db, "alice") == 0


def test_create_session_refuses_a_disabled_account_even_without_expectation(db):
    set_column(db, "alice", "disabled", 1)
    assert sessions.create_session(db, alice_id(db), NOW, TTL) is None
    assert session_count(db, "alice") == 0


def test_create_session_refuses_an_unknown_user(db):
    assert sessions.create_session(db, 9999, NOW, TTL) is None


# ---- revoke_user_sessions -----------------------------------------------------------------------------------------

def test_revoke_user_sessions_deletes_only_that_users_sessions_and_counts_them(db):
    bob = db.conn().execute("SELECT id FROM users WHERE username = 'bob'").fetchone()[0]
    sessions.create_session(db, alice_id(db), NOW, TTL)
    sessions.create_session(db, alice_id(db), NOW, TTL)
    sessions.create_session(db, bob, NOW, TTL)
    with db.write() as c:
        assert sessions.revoke_user_sessions(c, alice_id(db)) == 2
    assert session_count(db, "alice") == 0 and session_count(db, "bob") == 1


def test_revoke_user_sessions_is_part_of_the_callers_transaction(db):
    sessions.create_session(db, alice_id(db), NOW, TTL)
    with pytest.raises(RuntimeError):
        with db.write() as c:
            sessions.revoke_user_sessions(c, alice_id(db))
            raise RuntimeError("rollback")
    assert session_count(db, "alice") == 1  # xóa phiên và thay đổi khác cùng sống hoặc cùng chết


# ---- đua đăng nhập với đổi thông tin tài khoản --------------------------------------------------------------------

class SlowVerify:
    """Chặn `verify_password` đầu tiên của đăng nhập cho tới khi test cho đi: giả lập lúc băm kéo dài hàng trăm ms."""

    def __init__(self, monkeypatch):
        self.entered, self.release = threading.Event(), threading.Event()
        real = auth.verify_password
        first = []

        def slow(password, stored):
            result = real(password, stored)  # kết quả đã quyết định bằng hash đọc trước khi admin đổi
            if not first:
                first.append(1)
                self.entered.set()
                assert self.release.wait(10), "test không thả đăng nhập"
            return result

        monkeypatch.setattr(auth, "verify_password", slow)


def login_during(web, monkeypatch, admin_change, cookie=None):
    """Bắt đầu đăng nhập đúng mật khẩu, đợi nó đang băm, để 'admin' đổi tài khoản, rồi thả. Trả response của đăng nhập."""
    gate = SlowVerify(monkeypatch)
    out = []
    thread = threading.Thread(target=lambda: out.append(web.post("/api/login", {"username": "alice", "password": PASSWORD}, cookie=cookie)))
    thread.start()
    assert gate.entered.wait(10), "đăng nhập không tới bước băm"
    admin_change()
    gate.release.set()
    thread.join(15)
    assert out, "đăng nhập không trả lời"
    return out[0]


def test_control_login_that_nothing_interrupts_succeeds_with_the_slow_verify(web, monkeypatch):
    r = login_during(web, monkeypatch, lambda: None)
    assert r.status == 200 and session_count(web.db, "alice") == 1


def test_login_racing_with_a_password_change_leaves_no_session(web, monkeypatch):
    new_hash = auth.hash_password("brand-new-pass")
    r = login_during(web, monkeypatch, lambda: set_column(web.db, "alice", "password_hash", new_hash))
    assert (r.status, r.error_code) == (401, "INVALID_CREDENTIALS") and "set-cookie" not in r.headers
    assert session_count(web.db, "alice") == 0


def test_login_racing_with_a_role_change_leaves_no_session(web, monkeypatch):
    r = login_during(web, monkeypatch, lambda: set_column(web.db, "alice", "role", "admin"))
    assert (r.status, r.error_code) == (401, "INVALID_CREDENTIALS")
    assert session_count(web.db, "alice") == 0


def test_login_racing_with_disabling_the_account_leaves_no_session(web, monkeypatch):
    r = login_during(web, monkeypatch, lambda: set_column(web.db, "alice", "disabled", 1))
    assert (r.status, r.error_code) == (401, "INVALID_CREDENTIALS")
    assert session_count(web.db, "alice") == 0


def test_a_refused_login_does_not_destroy_the_session_the_request_carried(web, monkeypatch):
    """Đăng nhập bị từ chối vì đua không có tác dụng phụ: phiên cũ mà request mang theo vẫn còn (README 3.3)."""
    old = web.login("alice")
    new_hash = auth.hash_password("brand-new-pass")
    r = login_during(web, monkeypatch, lambda: set_column(web.db, "alice", "password_hash", new_hash), cookie=old)
    assert r.status == 401 and "set-cookie" not in r.headers
    assert web.get("/api/me", cookie=old).status == 200 and session_count(web.db, "alice") == 1


def test_a_successful_login_still_replaces_the_session_the_request_carried(web):
    old = web.login("alice")
    r = web.post("/api/login", {"username": "alice", "password": PASSWORD}, cookie=old)
    assert r.status == 200 and r.cookie_value() != old
    assert web.get("/api/me", cookie=old).status == 401 and web.get("/api/me", cookie=r.cookie_value()).status == 200


def test_the_login_after_the_race_works_with_the_new_password(web, monkeypatch):
    new_hash = auth.hash_password("brand-new-pass")
    login_during(web, monkeypatch, lambda: set_column(web.db, "alice", "password_hash", new_hash))
    assert web.post("/api/login", {"username": "alice", "password": "brand-new-pass"}).status == 200
    assert web.post("/api/login", {"username": "alice", "password": PASSWORD}).status == 401

