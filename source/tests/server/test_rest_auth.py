"""REST: đăng nhập, phiên, đăng xuất, giới hạn đăng nhập (README 3.2, 3.3, D9, D10)."""

import logging
import threading

import pytest

from _rest_support import PASSWORD, FakeClock, add_account, populate
from _support import wait_until
from server import auth

WEEK_S = 7 * 24 * 3600


def user_of(resp):
    return resp.json()["user"]


def test_login_success_shape_and_cookie(web):
    r = web.post("/api/login", {"username": "alice", "password": PASSWORD})
    assert r.status == 200
    assert r.json() == {"user": {"id": 1, "username": "alice", "role": "user"}}
    assert r.headers["content-type"] == "application/json; charset=utf-8" and r.headers["cache-control"] == "no-store"
    cookie = r.headers["set-cookie"]
    token = r.cookie_value()
    assert cookie == f"jh_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={WEEK_S}"
    assert "Secure" not in cookie and len(token) >= 40


def test_admin_role_is_reported(web):
    assert user_of(web.post("/api/login", {"username": "root", "password": PASSWORD}))["role"] == "admin"


def test_each_login_gets_a_fresh_unpredictable_token(web):
    tokens = {web.login() for _ in range(5)}
    assert len(tokens) == 5


def test_only_a_hash_of_the_token_is_stored(web):
    token = web.login()
    rows = web.db.conn().execute("SELECT * FROM sessions").fetchall()
    assert len(rows) == 1 and token not in "".join(str(v) for v in tuple(rows[0]))
    assert rows[0]["user_id"] == 1


@pytest.mark.parametrize("creds", [
    {"username": "alice", "password": "wrong-password"},
    {"username": "nobody", "password": PASSWORD},
    {"username": "Alice", "password": PASSWORD},  # phân biệt hoa thường
    {"username": "alice", "password": ""},
    {"username": "alice", "password": "x" * 129},
    {"username": "alice", "password": "ñ" * 65},  # 130 byte UTF-8
])
def test_bad_credentials_are_indistinguishable(web, creds):
    reference = web.post("/api/login", {"username": "alice", "password": "wrong-password"})
    r = web.post("/api/login", creds)
    assert r.status == 401 and r.error_code == "INVALID_CREDENTIALS"
    assert r.body == reference.body and "set-cookie" not in r.headers


def test_a_password_over_128_bytes_cannot_log_in_even_if_it_was_stored(web):
    long_password = "p" * 200
    web.db.create_user("carol", auth.hash_password("p" * 128), "user")
    r = web.post("/api/login", {"username": "carol", "password": long_password})
    assert r.status == 401 and r.error_code == "INVALID_CREDENTIALS"


@pytest.mark.parametrize("body", [
    {}, {"username": "alice"}, {"password": PASSWORD}, {"username": 1, "password": PASSWORD},
    {"username": "alice", "password": 5}, {"username": None, "password": PASSWORD}, {"username": "", "password": "x"},
    {"username": "u" * 65, "password": "x"}, {"username": ["alice"], "password": PASSWORD},
    {"username": "alice", "password": ["x"]}, {"username": "alice", "password": {"a": 1}},
])
def test_malformed_login_is_400(web, body):
    r = web.post("/api/login", body)
    assert r.status == 400 and r.error_code == "INVALID_REQUEST"


def test_username_of_64_characters_is_accepted(web):
    r = web.post("/api/login", {"username": "u" * 64, "password": "x"})
    assert r.status == 401  # định dạng hợp lệ, chỉ là không có user này


def test_lone_surrogates_in_credentials_are_400_not_500(web):
    for body in (b'{"username": "\\ud800", "password": "x"}', b'{"username": "alice", "password": "\\udfff"}'):
        r = web.post("/api/login", body=body)
        assert r.status == 400 and r.error_code == "INVALID_REQUEST"


def test_nul_in_credentials_is_just_wrong_credentials(web, monkeypatch):
    r = web.post("/api/login", {"username": "alice\u0000", "password": PASSWORD})
    assert r.status == 401
    r = web.post("/api/login", {"username": "alice", "password": PASSWORD + "\u0000"})
    assert r.status == 401


def test_unicode_username_and_password_work(web):
    web.db.create_user("Bạn-Đạt", auth.hash_password("mật-khẩu-tiếng-việt"), "user")
    r = web.post("/api/login", {"username": "Bạn-Đạt", "password": "mật-khẩu-tiếng-việt"})
    assert r.status == 200 and user_of(r)["username"] == "Bạn-Đạt"


# ---- /api/me, phiên -----------------------------------------------------------------------------------------------

def test_me_returns_the_session_user(web):
    cookie = web.login("bob")
    r = web.get("/api/me", cookie=cookie)
    assert r.status == 200 and r.json() == {"user": {"id": 2, "username": "bob", "role": "user"}}


@pytest.mark.parametrize("cookie", [None, "", "garbage", "a" * 43, "a" * 5000, "../../etc/passwd", "x y"])
def test_missing_or_bad_cookie_is_401(web, cookie):
    r = web.get("/api/me", cookie=cookie) if cookie is not None else web.get("/api/me")
    assert r.status == 401 and r.error_code == "UNAUTHENTICATED"


def test_every_endpoint_but_login_and_health_needs_a_session(web):
    for method, path in (("GET", "/api/me"), ("POST", "/api/logout"), ("GET", "/api/problems"),
                         ("GET", "/api/problems/sum"), ("GET", "/api/submissions"), ("GET", "/api/submissions/1"),
                         ("POST", "/api/submissions")):
        r = web.request(method, path, obj={} if method == "POST" else None)
        assert r.status == 401 and r.error_code == "UNAUTHENTICATED", path


def test_unauthenticated_beats_content_type_and_body_errors(web):
    r = web.request("POST", "/api/submissions", body=b"not json", content_type="text/plain")
    assert r.status == 401
    r = web.request("POST", "/api/logout", body=b"x" * 600_000)
    assert r.status == 401


def test_logout_destroys_the_session_on_the_server(web):
    cookie = web.login()
    r = web.post("/api/logout", {}, cookie=cookie)
    assert r.status == 204 and r.body == b""
    assert r.headers["set-cookie"] == "jh_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"
    assert r.headers["cache-control"] == "no-store"
    assert web.get("/api/me", cookie=cookie).status == 401  # dùng lại cookie cũ
    assert web.db.conn().execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_logout_only_ends_the_current_session(web):
    first, second = web.login(), web.login()
    assert web.post("/api/logout", {}, cookie=first).status == 204
    assert web.get("/api/me", cookie=first).status == 401
    assert web.get("/api/me", cookie=second).status == 200


def test_logout_without_session_is_401(web):
    assert web.post("/api/logout", {}).status == 401


def test_logout_needs_a_json_object_body(web):
    cookie = web.login()
    assert web.post("/api/logout", body=b"", cookie=cookie).status == 400
    assert web.post("/api/logout", body=b"{}", cookie=cookie, content_type="text/plain").status == 415
    assert web.get("/api/me", cookie=cookie).status == 200  # lỗi thì phiên còn nguyên


def test_several_sessions_can_coexist(web):
    cookies = [web.login("alice") for _ in range(3)] + [web.login("bob")]
    assert all(web.get("/api/me", cookie=c).status == 200 for c in cookies)


def test_login_with_a_valid_session_replaces_it(web):
    old = web.login("alice")
    r = web.post("/api/login", {"username": "bob", "password": PASSWORD}, cookie=old)
    new = r.cookie_value()
    assert r.status == 200 and new != old
    assert web.get("/api/me", cookie=old).status == 401
    assert user_of(web.get("/api/me", cookie=new))["username"] == "bob"


def test_login_with_a_stale_cookie_just_logs_in(web):
    r = web.post("/api/login", {"username": "alice", "password": PASSWORD}, cookie="b" * 43)
    assert r.status == 200


def test_failed_login_does_not_touch_the_existing_session(web):
    cookie = web.login()
    assert web.post("/api/login", {"username": "alice", "password": "bad"}, cookie=cookie).status == 401
    assert web.get("/api/me", cookie=cookie).status == 200


def test_role_comes_from_the_database_on_every_request(web):
    cookie = web.login("alice")
    assert user_of(web.get("/api/me", cookie=cookie))["role"] == "user"
    with web.db.write() as c:
        c.execute("UPDATE users SET role = 'admin' WHERE username = 'alice'")
    assert user_of(web.get("/api/me", cookie=cookie))["role"] == "admin"


# ---- hạn phiên (đồng hồ giả) --------------------------------------------------------------------------------------

@pytest.fixture
def timed(make_web):
    clock = FakeClock()
    w = make_web(clock=clock)
    populate(w.db)
    w.clock = clock
    return w.start()


def test_session_expires_exactly_seven_days_after_login(timed):
    cookie = timed.login()
    timed.clock.advance(WEEK_S - 1)
    assert timed.get("/api/me", cookie=cookie).status == 200
    timed.clock.advance(1)
    assert timed.get("/api/me", cookie=cookie).status == 401


def test_activity_does_not_extend_the_session(timed):
    cookie = timed.login()
    for _ in range(6):
        timed.clock.advance(24 * 3600)
        assert timed.get("/api/me", cookie=cookie).status == 200
    timed.clock.advance(24 * 3600)
    assert timed.get("/api/me", cookie=cookie).status == 401


def test_login_purges_expired_sessions(timed):
    timed.login("alice")
    timed.login("bob")
    timed.clock.advance(WEEK_S + 1)
    assert timed.db.conn().execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 2
    timed.login("alice")
    assert timed.db.conn().execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1


def test_sessions_survive_a_server_restart(web):
    cookie = web.login("bob")
    web.restart()
    r = web.get("/api/me", cookie=cookie)
    assert r.status == 200 and user_of(r)["username"] == "bob"


# ---- giới hạn đăng nhập -------------------------------------------------------------------------------------------

@pytest.fixture
def limited(make_web):
    clock = FakeClock()
    w = make_web(clock=clock, login_rate_limit=3, login_rate_window_s=60)
    populate(w.db)
    w.clock = clock
    return w.start()


def test_login_rate_limit_per_username_even_with_the_right_password(limited):
    for _ in range(3):
        assert limited.post("/api/login", {"username": "alice", "password": "bad"}).status == 401
    for password in ("bad", PASSWORD):
        r = limited.post("/api/login", {"username": "alice", "password": password})
        assert r.status == 429 and r.error_code == "RATE_LIMITED" and "set-cookie" not in r.headers
        assert r.headers["retry-after"] == "60"


def test_other_usernames_are_not_affected(limited):
    for _ in range(4):
        limited.post("/api/login", {"username": "alice", "password": "bad"})
    assert limited.post("/api/login", {"username": "bob", "password": PASSWORD}).status == 200


def test_unknown_usernames_are_limited_too(limited):
    for _ in range(3):
        assert limited.post("/api/login", {"username": "ghost", "password": "x"}).status == 401
    assert limited.post("/api/login", {"username": "ghost", "password": "x"}).status == 429


def test_the_window_slides_and_retry_after_counts_down(limited):
    for _ in range(3):
        limited.post("/api/login", {"username": "alice", "password": "bad"})
        limited.clock.advance(10)
    r = limited.post("/api/login", {"username": "alice", "password": PASSWORD})
    assert r.status == 429 and r.headers["retry-after"] == "30"
    limited.clock.advance(29.5)
    assert limited.post("/api/login", {"username": "alice", "password": PASSWORD}).status == 429
    limited.clock.advance(0.5)
    assert limited.post("/api/login", {"username": "alice", "password": PASSWORD}).status == 200


def test_rejected_attempts_do_not_extend_the_lock(limited):
    for _ in range(3):
        limited.post("/api/login", {"username": "alice", "password": "bad"})
    for _ in range(20):
        assert limited.post("/api/login", {"username": "alice", "password": "bad"}).status == 429
    limited.clock.advance(60)
    assert limited.post("/api/login", {"username": "alice", "password": PASSWORD}).status == 200


def test_malformed_or_oversized_requests_do_not_count(limited):
    for _ in range(10):
        assert limited.post("/api/login", {"username": "alice"}).status == 400
        assert limited.post("/api/login", body=b"x" * 200_000).status == 413
        assert limited.post("/api/login", body=b"{}", content_type="text/plain").status == 415
    assert limited.post("/api/login", {"username": "alice", "password": PASSWORD}).status == 200


def test_the_limit_is_checked_before_the_password_is_hashed(limited, monkeypatch):
    calls = []
    real = auth.verify_password

    def counting(password, stored):
        calls.append(1)
        return real(password, stored)

    monkeypatch.setattr(auth, "verify_password", counting)
    for _ in range(3):
        limited.post("/api/login", {"username": "alice", "password": "bad"})
    assert len(calls) == 3
    limited.post("/api/login", {"username": "alice", "password": PASSWORD})
    assert len(calls) == 3  # lần thứ 4 bị chặn trước khi băm


def test_unknown_username_and_overlong_password_still_cost_one_hash(web, monkeypatch):
    calls = []
    real = auth.verify_password

    def counting(password, stored):
        calls.append(stored)
        return real(password, stored)

    monkeypatch.setattr(auth, "verify_password", counting)
    web.post("/api/login", {"username": "ghost", "password": "x"})
    web.post("/api/login", {"username": "alice", "password": "x" * 200})
    web.post("/api/login", {"username": "alice", "password": "wrong"})
    assert len(calls) == 3
    assert calls[0] == calls[1] == web.app.state.rest.dummy_hash  # băm giả cho cả hai trường hợp đầu


# ---- giới hạn đăng nhập đồng thời ---------------------------------------------------------------------------------

def test_too_many_concurrent_logins_are_429_with_retry_after(make_web, monkeypatch):
    gate, release = threading.Semaphore(0), threading.Event()
    real = auth.verify_password

    def blocking(password, stored):
        gate.release()
        release.wait(10)
        return real(password, stored)

    web = make_web(login_max_concurrent=2)
    populate(web.db)
    web.start()
    monkeypatch.setattr(auth, "verify_password", blocking)
    results = []
    threads = [threading.Thread(target=lambda: results.append(
        web.post("/api/login", {"username": "alice", "password": PASSWORD}))) for _ in range(2)]
    for t in threads:
        t.start()
    for _ in range(2):
        assert gate.acquire(timeout=10)  # cả hai đã vào trong vùng băm
    over = web.post("/api/login", {"username": "bob", "password": PASSWORD})
    assert over.status == 429 and over.error_code == "RATE_LIMITED" and over.headers["retry-after"] == "1"
    assert "set-cookie" not in over.headers
    release.set()
    for t in threads:
        t.join(15)
    assert sorted(r.status for r in results) == [200, 200]
    # hết tải thì đăng nhập được lại, và lượt bị từ chối không bị tính vào giới hạn theo username
    monkeypatch.setattr(auth, "verify_password", real)
    assert web.post("/api/login", {"username": "bob", "password": PASSWORD}).status == 200


def test_the_concurrency_slot_is_released_after_errors(make_web, monkeypatch):
    web = make_web(login_max_concurrent=1, login_rate_limit=2, login_rate_window_s=60)
    populate(web.db)
    web.start()
    for _ in range(2):
        assert web.post("/api/login", {"username": "alice", "password": "bad"}).status == 401
    assert web.post("/api/login", {"username": "alice", "password": "bad"}).status == 429  # ném giữa chừng
    assert web.post("/api/login", {"username": "bob", "password": PASSWORD}).status == 200  # chỗ đã được trả


# ---- log ----------------------------------------------------------------------------------------------------------

def test_passwords_and_login_bodies_never_reach_the_log(web, caplog):
    marker = "PW-MARKER-9f3a"
    with caplog.at_level(logging.DEBUG):
        web.post("/api/login", {"username": "alice", "password": marker})
        web.post("/api/login", {"username": "alice", "password": marker * 100})
        web.post("/api/login", body=b'{"password": "' + marker.encode() + b'", broken')
        web.post("/api/login", {"username": 5, "password": marker})
        web.post("/api/login", {"username": "alice", "password": marker, "pad": "p" * 200_000})
    assert marker not in caplog.text


def test_password_with_nul_takes_the_dummy_hash_path_like_an_unknown_user(web, monkeypatch):
    calls = []
    real = auth.verify_password

    def counting(password, stored):
        calls.append(stored)
        return real(password, stored)

    monkeypatch.setattr(auth, "verify_password", counting)
    web.post("/api/login", {"username": "alice", "password": PASSWORD + "\u0000"})
    assert calls == [web.app.state.rest.dummy_hash]
