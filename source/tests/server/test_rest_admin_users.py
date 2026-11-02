"""REST: quản lý người dùng của admin `/api/admin/users` (README 3.6, D30).

Không khóa. WebSocket thật (uvicorn) để kiểm đóng 4401; hook `scoreboard_dirty` được bọc để đếm; race dùng `Event`/`Barrier`
thay vì sleep."""

import contextlib
import json
import logging
import os
import re
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "e2e"))

from ws_client import WsClient  # noqa: E402

from _rest_support import PASSWORD, add_account, populate  # noqa: E402
from _support import insert_submission, wait_until  # noqa: E402
from server import auth  # noqa: E402

ISO_MS = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z")
USER_KEYS = {"id", "username", "role", "disabled", "created_at"}
NEW_PASSWORD = "brand-new-pass-1"


class SpyHooks:
    def __init__(self, real):
        self.real, self.dirty = real, 0

    def scoreboard_dirty(self):
        self.dirty += 1
        self.real.scoreboard_dirty()

    def __getattr__(self, name):
        return getattr(self.real, name)


@pytest.fixture
def aw(make_web):
    """Server đã chạy: alice, bob (user), root (admin); alice có một bài AC nên có hàng trên bảng xếp hạng."""
    w = make_web(scoreboard_min_interval_s=0.1, ws_ping_interval_s=30)
    populate(w.db)
    alice = w.db.conn().execute("SELECT id FROM users WHERE username = 'alice'").fetchone()[0]
    h = w.db.conn().execute("SELECT tests_hash FROM problems WHERE id = 'sum'").fetchone()[0]
    insert_submission(w.db, alice, "sum", state="done", verdict="AC", time_ms=1, memory_kib=1, verdict_test_hash=h)
    w.start()
    w.spy = SpyHooks(w.app.state.rest.hooks)
    w.app.state.rest.hooks = w.spy
    return w


def root_cookie(web):
    if not hasattr(web, "_root"):
        web._root = web.login("root")
    return web._root


def call(web, method, path, obj=None, cookie="root", **kw):
    cookie = root_cookie(web) if cookie == "root" else cookie
    return web.request(method, path, obj=obj, cookie=cookie, **kw)


def uid(web, name):
    return web.db.conn().execute("SELECT id FROM users WHERE username = ?", (name,)).fetchone()[0]


def row(web, name):
    return web.db.conn().execute("SELECT * FROM users WHERE username = ?", (name,)).fetchone()


def sessions_of(web, name):
    return web.db.conn().execute("SELECT COUNT(*) FROM sessions s JOIN users u ON u.id = s.user_id WHERE u.username = ?",
                                 (name,)).fetchone()[0]


def patch(web, name_or_id, obj, **kw):
    target = name_or_id if isinstance(name_or_id, (int, str)) and str(name_or_id).isdigit() else uid(web, name_or_id)
    return call(web, "PATCH", f"/api/admin/users/{target}", obj, **kw)


def set_password(web, name_or_id, password, **kw):
    target = name_or_id if isinstance(name_or_id, int) else uid(web, name_or_id)
    return call(web, "POST", f"/api/admin/users/{target}/password", {"password": password}, **kw)


def open_ws(web, cookie):
    return WsClient("127.0.0.1", web.port, origin=f"http://127.0.0.1:{web.port}", cookie=cookie)


def expect_close(client, code, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        kind, value = client.recv(timeout=max(0.05, deadline - time.monotonic()))
        if kind == "close":
            assert value == code, f"mã đóng {value}, mong {code}"
            return
        if kind == "eof":
            pytest.fail("kết nối đóng không có frame đóng")
    pytest.fail(f"không thấy frame đóng trong {timeout} giây")


def connected(web, name):
    cookie = web.login(name)
    client = open_ws(web, cookie)
    client.recv_json()  # SCOREBOARD đầu tiên
    return cookie, client


def stays_open(client):
    kind, value = client.recv(timeout=0.6)
    return kind != "close" and kind != "eof"


# ---- phân quyền ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("method,path,obj", [
    ("GET", "/api/admin/users", None),
    ("POST", "/api/admin/users", {"username": "zed", "password": PASSWORD}),
    ("PATCH", "/api/admin/users/{alice}", {"disabled": True}),
    ("POST", "/api/admin/users/{alice}/password", {"password": NEW_PASSWORD}),
])
def test_only_admins_may_use_the_user_endpoints_and_a_user_changes_nothing(aw, method, path, obj):
    path = path.replace("{alice}", str(uid(aw, "alice")))
    assert aw.request(method, path, obj=obj).status == 401
    cookie = aw.login("alice")
    r = aw.request(method, path, obj=obj, cookie=cookie)
    assert (r.status, r.error_code) == (403, "FORBIDDEN")
    assert row(aw, "alice")["disabled"] == 0 and row(aw, "zed") is None
    assert auth.verify_password(PASSWORD, row(aw, "alice")["password_hash"])
    assert aw.get("/api/me", cookie=cookie).status == 200  # phiên của chính họ không bị đụng


@pytest.mark.parametrize("kwargs", [
    {"obj": {"disabled": "x"}}, {"body": b"{broken", "content_type": "json"}, {"body": b"[1]", "content_type": "json"},
    {"obj": {"disabled": True}, "content_type": "text/plain"},
])
def test_a_user_gets_403_before_any_body_error_even_for_unknown_ids(aw, kwargs):
    cookie = aw.login("alice")
    for target in (str(uid(aw, "bob")), "99999", "abc"):
        r = aw.request("PATCH", f"/api/admin/users/{target}", cookie=cookie, **kwargs)
        assert (r.status, r.error_code) == (403, "FORBIDDEN"), (target, kwargs)


def test_a_client_cannot_claim_a_role_to_use_the_endpoints(aw):
    cookie = aw.login("alice")
    r = aw.request("PATCH", f"/api/admin/users/{uid(aw, 'alice')}?role=admin", obj={"role": "admin", "user_id": 3},
                   cookie=cookie, headers={"X-Role": "admin"})
    assert r.status == 403 and row(aw, "alice")["role"] == "user"


# ---- GET /api/admin/users ----------------------------------------------------------------------------------------

def test_list_has_exactly_the_documented_fields_newest_first_and_never_a_secret(aw):
    r = call(aw, "GET", "/api/admin/users")
    assert r.status == 200
    body = r.json()
    assert [u["username"] for u in body["users"]] == ["root", "bob", "alice"] and body["next_before_id"] is None
    for u in body["users"]:
        assert set(u) == USER_KEYS and ISO_MS.fullmatch(u["created_at"]) and u["disabled"] is False
    text = r.body.decode()
    assert "scrypt" not in text and "password" not in text
    assert r.headers["cache-control"] == "no-store"


def test_list_paginates_like_the_submission_history(aw):
    for i in range(5):
        add_account(aw.db, f"extra{i}")
    first = call(aw, "GET", "/api/admin/users?limit=3").json()
    assert len(first["users"]) == 3 and first["next_before_id"] == first["users"][-1]["id"]
    second = call(aw, "GET", f"/api/admin/users?limit=3&before_id={first['next_before_id']}").json()
    ids = [u["id"] for u in first["users"] + second["users"]]
    assert ids == sorted(ids, reverse=True) and len(set(ids)) == 6
    rest = call(aw, "GET", f"/api/admin/users?before_id={second['next_before_id']}").json()
    assert rest["next_before_id"] is None and len(first["users"] + second["users"] + rest["users"]) == 8


def test_list_default_limit_is_50_and_max_is_200(aw):
    for i in range(210):
        add_account(aw.db, f"bulk{i:03d}")
    assert len(call(aw, "GET", "/api/admin/users").json()["users"]) == 50
    assert len(call(aw, "GET", "/api/admin/users?limit=200").json()["users"]) == 200
    assert call(aw, "GET", "/api/admin/users?limit=201").status == 400


@pytest.mark.parametrize("query", ["?limit=0", "?limit=x", "?before_id=0", "?before_id=-1", "?bogus=1", "?limit=5&limit=6",
                                   "?before_id=" + "9" * 30, "?role=admin"])
def test_list_rejects_bad_unknown_or_repeated_parameters(aw, query):
    r = call(aw, "GET", "/api/admin/users" + query)
    assert (r.status, r.error_code) == (400, "INVALID_REQUEST"), query


# ---- POST /api/admin/users ---------------------------------------------------------------------------------------

def test_create_returns_201_with_the_user_and_the_new_user_can_log_in(aw):
    r = call(aw, "POST", "/api/admin/users", {"username": "dave", "password": "dave-pass-9"})
    assert r.status == 201
    body = r.json()
    assert set(body) == USER_KEYS and body["username"] == "dave" and body["role"] == "user" and body["disabled"] is False
    assert body["id"] == uid(aw, "dave") and ISO_MS.fullmatch(body["created_at"])
    assert "dave-pass-9" not in r.body.decode() and "scrypt" not in r.body.decode()
    assert aw.post("/api/login", {"username": "dave", "password": "dave-pass-9"}).status == 200
    stored = row(aw, "dave")["password_hash"]
    assert stored.startswith("scrypt$") and "dave-pass-9" not in stored


def test_create_admin_role_and_case_sensitive_usernames(aw):
    r = call(aw, "POST", "/api/admin/users", {"username": "Alice", "password": "alice-two-9", "role": "admin"})
    assert r.status == 201 and r.json()["role"] == "admin"  # "Alice" khác "alice"
    admin_cookie = aw.login("Alice", "alice-two-9")
    assert aw.request("GET", "/api/admin/users", cookie=admin_cookie).status == 200


def test_create_a_duplicate_username_is_409_and_changes_nothing(aw):
    before = row(aw, "alice")["password_hash"]
    r = call(aw, "POST", "/api/admin/users", {"username": "alice", "password": "other-pass-9"})
    assert (r.status, r.error_code) == (409, "USERNAME_TAKEN")
    assert row(aw, "alice")["password_hash"] == before and aw.post("/api/login", {"username": "alice", "password": PASSWORD}).status == 200


@pytest.mark.parametrize("body", [
    {}, {"username": "zed"}, {"password": "zed-pass-99"},
    {"username": "", "password": "zed-pass-99"}, {"username": "a" * 33, "password": "zed-pass-99"},
    {"username": "has space", "password": "zed-pass-99"}, {"username": "bad/slash", "password": "zed-pass-99"},
    {"username": "zed\n", "password": "zed-pass-99"}, {"username": "é", "password": "zed-pass-99"},
    {"username": 5, "password": "zed-pass-99"}, {"username": None, "password": "zed-pass-99"},
    {"username": "zed", "password": "short"}, {"username": "zed", "password": "x" * 129},
    {"username": "zed", "password": "pass\u0000word-9"}, {"username": "zed", "password": 12345678},
    {"username": "zed", "password": "\ud800-pass-99"},
    {"username": "zed", "password": "zed-pass-99", "role": "root"}, {"username": "zed", "password": "zed-pass-99", "role": 5},
    {"username": "zed", "password": "zed-pass-99", "role": None},
    {"username": "zed", "password": "zed-pass-99", "disabled": True},
    {"username": "zed", "password": "zed-pass-99", "id": 77},
])
def test_create_rejects_bad_bodies_without_creating_anything(aw, body):
    r = call(aw, "POST", "/api/admin/users", body)
    assert (r.status, r.error_code) == (400, "INVALID_REQUEST"), body
    assert row(aw, "zed") is None and aw.db.conn().execute("SELECT COUNT(*) FROM users").fetchone()[0] == 3
    text = r.body.decode()
    assert "zed-pass-99" not in text and "short" not in text  # message không lặp lại giá trị client


def test_two_creates_of_the_same_username_at_once_give_one_201_and_one_409(aw):
    root_cookie(aw)
    results = []
    barrier = threading.Barrier(2)

    def go():
        barrier.wait(10)
        results.append(call(aw, "POST", "/api/admin/users", {"username": "twin", "password": "twin-pass-9"}).status)

    threads = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in threads]
    [t.join(30) for t in threads]
    assert sorted(results) == [201, 409]
    assert aw.db.conn().execute("SELECT COUNT(*) FROM users WHERE username = 'twin'").fetchone()[0] == 1


# ---- PATCH: khóa -------------------------------------------------------------------------------------------------

def test_disabling_deletes_sessions_closes_websockets_and_blocks_login_but_not_others(aw):
    alice_cookie, alice_ws = connected(aw, "alice")
    bob_cookie, bob_ws = connected(aw, "bob")
    aw.login("alice")  # alice có hai phiên
    assert sessions_of(aw, "alice") == 2
    r = patch(aw, "alice", {"disabled": True})
    assert r.status == 200 and r.json()["disabled"] is True and set(r.json()) == USER_KEYS
    assert sessions_of(aw, "alice") == 0 and sessions_of(aw, "bob") == 1
    expect_close(alice_ws, 4401, timeout=3)  # đóng ngay, không đợi nhịp kiểm phiên 30 giây
    assert stays_open(bob_ws)
    assert aw.get("/api/me", cookie=alice_cookie).status == 401 and aw.get("/api/me", cookie=bob_cookie).status == 200
    login = aw.post("/api/login", {"username": "alice", "password": PASSWORD})
    assert (login.status, login.error_code) == (401, "INVALID_CREDENTIALS")
    assert aw.spy.dirty == 0  # khóa không đổi bảng xếp hạng


def test_a_disabled_account_stays_on_the_scoreboard(aw):
    def names():
        return [x["username"] for x in aw.get("/api/scoreboard", cookie=root_cookie(aw)).json()["rows"]]

    assert wait_until(lambda: "alice" in names(), 6)
    assert patch(aw, "alice", {"disabled": True}).status == 200
    time.sleep(0.4)  # quá nhịp tính lại 0,1 giây của fixture
    assert "alice" in names()


def test_enabling_lets_the_user_log_in_again_and_does_not_touch_sessions(aw):
    cookie = aw.login("alice")
    with aw.db.write() as c:  # khóa "ngoài đường" để còn phiên cũ trong DB
        c.execute("UPDATE users SET disabled = 1 WHERE username = 'alice'")
    assert aw.post("/api/login", {"username": "alice", "password": PASSWORD}).status == 401
    r = patch(aw, "alice", {"disabled": False})
    assert r.status == 200 and r.json()["disabled"] is False
    assert sessions_of(aw, "alice") == 1  # mở khóa không xóa phiên nào
    assert aw.get("/api/me", cookie=cookie).status == 200
    assert aw.post("/api/login", {"username": "alice", "password": PASSWORD}).status == 200
    assert aw.spy.dirty == 0


# ---- PATCH: role -------------------------------------------------------------------------------------------------

def test_promoting_deletes_sessions_closes_websockets_and_marks_the_scoreboard_dirty(aw):
    cookie, ws = connected(aw, "bob")
    r = patch(aw, "bob", {"role": "admin"})
    assert r.status == 200 and r.json()["role"] == "admin"
    assert sessions_of(aw, "bob") == 0 and aw.get("/api/me", cookie=cookie).status == 401
    expect_close(ws, 4401, timeout=3)
    assert aw.spy.dirty == 1
    new_cookie = aw.login("bob")  # đăng nhập lại để có quyền mới
    assert aw.request("GET", "/api/admin/users", cookie=new_cookie).status == 200


def test_demoting_an_admin_deletes_their_sessions_and_marks_dirty(aw):
    add_account(aw.db, "second", "admin")
    cookie = aw.login("second")
    assert patch(aw, "second", {"role": "user"}).status == 200
    assert aw.get("/api/me", cookie=cookie).status == 401 and aw.spy.dirty == 1
    assert aw.login("second") and aw.request("GET", "/api/admin/users", cookie=aw.login("second")).status == 403


def test_a_role_change_takes_effect_for_admin_endpoints_on_the_next_request(aw):
    add_account(aw.db, "second", "admin")
    second = aw.login("second")
    assert patch(aw, "second", {"role": "user"}).status == 200
    assert aw.request("GET", "/api/admin/users", cookie=second).status == 401  # phiên đã bị xóa


def test_both_fields_at_once(aw):
    cookie = aw.login("alice")
    r = patch(aw, "alice", {"disabled": True, "role": "admin"})
    assert r.status == 200 and (r.json()["disabled"], r.json()["role"]) == (True, "admin")
    assert aw.get("/api/me", cookie=cookie).status == 401 and aw.spy.dirty == 1


def test_setting_the_current_values_changes_nothing(aw, caplog):
    caplog.set_level(logging.INFO, logger="judgehive.rest")
    cookie, ws = connected(aw, "alice")
    r = patch(aw, "alice", {"role": "user", "disabled": False})
    assert r.status == 200 and set(r.json()) == USER_KEYS
    assert sessions_of(aw, "alice") == 1 and aw.get("/api/me", cookie=cookie).status == 200
    assert stays_open(ws) and aw.spy.dirty == 0
    assert "update user" not in caplog.text


@pytest.mark.parametrize("body", [{}, {"disabled": "yes"}, {"disabled": 1}, {"disabled": 0}, {"disabled": None},
                                  {"role": "superuser"}, {"role": 1}, {"role": None}, {"role": ["admin"]},
                                  {"username": "x"}, {"id": 5}, {"disabled": True, "extra": 1}])
def test_patch_rejects_bad_bodies_and_changes_nothing(aw, body):
    cookie = aw.login("alice")
    r = patch(aw, "alice", body)
    assert (r.status, r.error_code) == (400, "INVALID_REQUEST"), body
    assert tuple(row(aw, "alice")[k] for k in ("role", "disabled", "username")) == ("user", 0, "alice")
    assert aw.get("/api/me", cookie=cookie).status == 200


@pytest.mark.parametrize("raw", ["99999", "0", "01", "abc", "-1", "1.5", "9" * 25, "%20"])
def test_unknown_or_malformed_ids_are_the_same_404(aw, raw):
    for method, path, obj in (("PATCH", f"/api/admin/users/{raw}", {"disabled": True}),
                              ("POST", f"/api/admin/users/{raw}/password", {"password": NEW_PASSWORD})):
        r = call(aw, method, path, obj)
        assert (r.status, r.error_code) == (404, "USER_NOT_FOUND"), (method, raw)


def test_the_body_is_validated_before_the_id(aw):
    assert call(aw, "PATCH", "/api/admin/users/99999", {"disabled": "x"}).status == 400
    assert call(aw, "POST", "/api/admin/users/99999/password", {"password": "short"}).status == 400


# ---- PATCH: admin cuối cùng ---------------------------------------------------------------------------------------

def test_the_last_active_admin_cannot_be_disabled_or_demoted_even_by_themselves(aw):
    cookie = root_cookie(aw)
    for body in ({"disabled": True}, {"role": "user"}, {"disabled": True, "role": "user"}):
        r = patch(aw, "root", body)
        assert (r.status, r.error_code) == (409, "LAST_ADMIN"), body
    assert tuple(row(aw, "root")[k] for k in ("role", "disabled")) == ("admin", 0)
    assert aw.get("/api/me", cookie=cookie).status == 200 and aw.spy.dirty == 0  # không đổi gì, không xóa phiên


def test_an_admin_may_disable_or_demote_themselves_when_another_active_admin_exists(aw):
    add_account(aw.db, "second", "admin")
    cookie = root_cookie(aw)
    r = patch(aw, "root", {"disabled": True})
    assert r.status == 200 and r.json()["disabled"] is True
    assert r.headers["set-cookie"] == "jh_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"
    assert aw.get("/api/me", cookie=cookie).status == 401  # phiên của chính họ đã bị xóa
    second = aw.login("second")
    assert patch(aw, "second", {"disabled": True}, cookie=second).status == 409  # giờ second là admin cuối


def test_a_disabled_admin_does_not_count_as_an_active_admin(aw):
    add_account(aw.db, "second", "admin")
    with aw.db.write() as c:
        c.execute("UPDATE users SET disabled = 1 WHERE username = 'second'")
    assert patch(aw, "root", {"role": "user"}).status == 409


def test_two_admins_demoting_each_other_at_once_cannot_both_succeed(aw):
    add_account(aw.db, "second", "admin")
    first, second = root_cookie(aw), aw.login("second")
    db = aw.app.state.rest.db
    real, barrier = db.write, threading.Barrier(2)

    @contextlib.contextmanager
    def gated():
        barrier.wait(10)  # cả hai request đã qua kiểm quyền rồi mới cùng xin khóa ghi
        with real() as c:
            yield c

    db.write = gated
    results = []

    def go(cookie, target):
        results.append(patch(aw, target, {"role": "user"}, cookie=cookie))

    try:
        threads = [threading.Thread(target=go, args=(first, "second")), threading.Thread(target=go, args=(second, "root"))]
        [t.start() for t in threads]
        [t.join(30) for t in threads]
    finally:
        db.write = real
    assert sorted(r.status for r in results) == [200, 409], [(r.status, r.error_code) for r in results]
    admins = aw.db.conn().execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND disabled = 0").fetchone()[0]
    assert admins == 1  # còn đúng một admin hoạt động


# ---- POST password ------------------------------------------------------------------------------------------------

def test_password_reset_replaces_the_hash_deletes_sessions_and_closes_websockets(aw):
    alice_cookie, alice_ws = connected(aw, "alice")
    bob_cookie, bob_ws = connected(aw, "bob")
    r = set_password(aw, "alice", NEW_PASSWORD)
    assert r.status == 204 and r.body == b"" and "set-cookie" not in r.headers
    assert sessions_of(aw, "alice") == 0 and sessions_of(aw, "bob") == 1
    expect_close(alice_ws, 4401, timeout=3)
    assert stays_open(bob_ws)
    assert aw.get("/api/me", cookie=alice_cookie).status == 401 and aw.get("/api/me", cookie=bob_cookie).status == 200
    assert aw.post("/api/login", {"username": "alice", "password": PASSWORD}).status == 401
    assert aw.post("/api/login", {"username": "alice", "password": NEW_PASSWORD}).status == 200
    assert aw.spy.dirty == 0


def test_an_admin_resetting_their_own_password_is_logged_out_and_the_cookie_is_cleared(aw):
    cookie = root_cookie(aw)
    r = set_password(aw, "root", NEW_PASSWORD)
    assert r.status == 204 and r.headers["set-cookie"] == "jh_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"
    assert aw.get("/api/me", cookie=cookie).status == 401
    assert aw.post("/api/login", {"username": "root", "password": NEW_PASSWORD}).status == 200


def test_resetting_the_password_of_a_disabled_account_works_and_does_not_unlock_it(aw):
    with aw.db.write() as c:
        c.execute("UPDATE users SET disabled = 1 WHERE username = 'alice'")
    assert set_password(aw, "alice", NEW_PASSWORD).status == 204
    assert row(aw, "alice")["disabled"] == 1
    assert aw.post("/api/login", {"username": "alice", "password": NEW_PASSWORD}).status == 401


@pytest.mark.parametrize("body", [{}, {"password": ""}, {"password": "short"}, {"password": "x" * 129},
                                  {"password": "pass\u0000word-9"}, {"password": 12345678}, {"password": None},
                                  {"password": "\ud800-pass-99"}, {"password": NEW_PASSWORD, "extra": 1},
                                  {"pass": NEW_PASSWORD}])
def test_password_reset_rejects_bad_bodies_and_changes_nothing(aw, body):
    cookie = aw.login("alice")
    r = call(aw, "POST", f"/api/admin/users/{uid(aw, 'alice')}/password", body)
    assert (r.status, r.error_code) == (400, "INVALID_REQUEST"), body
    assert auth.verify_password(PASSWORD, row(aw, "alice")["password_hash"])
    assert aw.get("/api/me", cookie=cookie).status == 200
    assert NEW_PASSWORD not in r.body.decode()


def test_hashing_happens_outside_the_write_transaction(aw, monkeypatch):
    """Một đặt lại mật khẩu đang băm không được giữ khóa ghi: ghi khác vẫn qua trong lúc băm bị chặn (D30(8))."""
    entered, release = threading.Event(), threading.Event()
    real = auth.hash_password

    def slow(password):
        entered.set()
        assert release.wait(15), "test không thả lần băm"
        return real(password)

    monkeypatch.setattr(auth, "hash_password", slow)
    out = []
    thread = threading.Thread(target=lambda: out.append(set_password(aw, "alice", NEW_PASSWORD)))
    thread.start()
    try:
        assert entered.wait(10)
        started = time.monotonic()
        with aw.db.write() as c:  # nếu đang giữ BEGIN IMMEDIATE thì dòng này chờ tới hết busy_timeout hoặc hết băm
            c.execute("UPDATE users SET created_at = created_at WHERE username = 'bob'")
        assert time.monotonic() - started < 2.0
    finally:
        release.set()
        thread.join(30)
    assert out and out[0].status == 204


# ---- đua đăng nhập với endpoint admin -----------------------------------------------------------------------------

class SlowVerify:
    def __init__(self, monkeypatch):
        self.entered, self.release = threading.Event(), threading.Event()
        real, first = auth.verify_password, []

        def slow(password, stored):
            result = real(password, stored)
            if not first:
                first.append(1)
                self.entered.set()
                assert self.release.wait(15), "test không thả đăng nhập"
            return result

        monkeypatch.setattr(auth, "verify_password", slow)


def login_during(web, monkeypatch, admin_action):
    gate = SlowVerify(monkeypatch)
    out = []
    thread = threading.Thread(target=lambda: out.append(web.post("/api/login", {"username": "alice", "password": PASSWORD})))
    thread.start()
    assert gate.entered.wait(10), "đăng nhập không tới bước băm"
    result = admin_action()
    gate.release.set()
    thread.join(20)
    assert out, "đăng nhập không trả lời"
    return out[0], result


def test_login_racing_with_an_admin_password_reset_leaves_no_valid_session(aw, monkeypatch):
    root_cookie(aw)
    login, reset = login_during(aw, monkeypatch, lambda: set_password(aw, "alice", NEW_PASSWORD))
    assert reset.status == 204
    assert (login.status, login.error_code) == (401, "INVALID_CREDENTIALS") and "set-cookie" not in login.headers
    assert sessions_of(aw, "alice") == 0
    assert aw.post("/api/login", {"username": "alice", "password": NEW_PASSWORD}).status == 200


def test_login_racing_with_an_admin_disable_or_role_change_leaves_no_valid_session(aw, monkeypatch):
    root_cookie(aw)
    login, change = login_during(aw, monkeypatch, lambda: patch(aw, "alice", {"disabled": True}))
    assert change.status == 200 and login.status == 401 and sessions_of(aw, "alice") == 0


def test_login_racing_with_a_role_change_leaves_no_valid_session(aw, monkeypatch):
    root_cookie(aw)
    login, change = login_during(aw, monkeypatch, lambda: patch(aw, "alice", {"role": "admin"}))
    assert change.status == 200 and login.status == 401 and sessions_of(aw, "alice") == 0


# ---- log và rò thông tin ------------------------------------------------------------------------------------------

def test_every_write_logs_one_admin_line_without_passwords_usernames_or_bodies(aw, caplog):
    caplog.set_level(logging.INFO, logger="judgehive.rest")
    admin_id = uid(aw, "root")
    root_cookie(aw)
    created = call(aw, "POST", "/api/admin/users", {"username": "logme", "password": "log-secret-pass-1"}).json()["id"]
    patch(aw, created, {"role": "admin"})
    set_password(aw, created, "log-secret-pass-2")
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("ADMIN ")]
    assert lines == [f"ADMIN admin={admin_id} create user={created} role=user",
                     f"ADMIN admin={admin_id} update user={created} role=user->admin disabled=0->0",
                     f"ADMIN admin={admin_id} set-password user={created}"]
    for secret in ("log-secret-pass-1", "log-secret-pass-2", "logme", "scrypt"):
        assert secret not in caplog.text, secret


def test_refused_or_failed_writes_leave_no_admin_line(aw, caplog):
    caplog.set_level(logging.INFO, logger="judgehive.rest")
    cookie = aw.login("alice")
    aw.request("PATCH", f"/api/admin/users/{uid(aw, 'alice')}", obj={"disabled": True}, cookie=cookie)  # 403
    patch(aw, "root", {"disabled": True})  # 409
    patch(aw, "alice", {"disabled": "x"})  # 400
    call(aw, "PATCH", "/api/admin/users/99999", {"disabled": True})  # 404
    call(aw, "POST", "/api/admin/users", {"username": "alice", "password": "other-pass-9"})  # 409
    assert "ADMIN " not in caplog.text


def test_responses_never_contain_a_password_hash(aw):
    pieces = [call(aw, "GET", "/api/admin/users").body, patch(aw, "alice", {"role": "admin"}).body,
              call(aw, "POST", "/api/admin/users", {"username": "h", "password": "hash-check-9"}).body]
    for body in pieces:
        text = body.decode()
        assert "scrypt" not in text and "password" not in json.dumps(json.loads(text)).lower()
