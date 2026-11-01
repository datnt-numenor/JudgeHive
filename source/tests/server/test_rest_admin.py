"""REST: nền admin (`require_admin`, `/api/admin/`) và ẩn/hiện đề `PATCH /api/admin/problems/{id}` (README 3.6, D8, D23).

Không khóa. Cách kiểm hook: bọc `hooks` của `RestState` sau khi server chạy, để đếm `scoreboard_dirty` mà vẫn gọi hook thật."""

import logging

import pytest

from _rest_support import add_account, populate, uuid_token
from _support import wait_until

PATH = "/api/admin/problems/{}"


class SpyHooks:
    """Bọc hooks thật: đếm `scoreboard_dirty`, mọi thứ khác chuyển tiếp."""

    def __init__(self, real):
        self.real, self.dirty = real, 0

    def scoreboard_dirty(self):
        self.dirty += 1
        self.real.scoreboard_dirty()

    def __getattr__(self, name):
        return getattr(self.real, name)


@pytest.fixture
def admin_web(make_web):
    w = make_web(scoreboard_min_interval_s=0.1)
    populate(w.db)
    w.start()
    w.spy = SpyHooks(w.app.state.rest.hooks)
    w.app.state.rest.hooks = w.spy
    return w


def patch(web, cookie, problem_id, obj, **kw):
    return web.request("PATCH", PATH.format(problem_id), obj=obj, cookie=cookie, **kw)


def hidden_in_db(web, problem_id):
    return web.db.conn().execute("SELECT hidden FROM problems WHERE id = ?", (problem_id,)).fetchone()[0]


def get_board(web, cookie):
    r = web.get("/api/scoreboard", cookie=cookie)
    assert r.status == 200, (r.status, r.body)
    return r.json()


# ---- nền: phiên, role, thứ tự kiểm tra -------------------------------------------------------------------------

def test_admin_routes_require_a_session(admin_web):
    r = patch(admin_web, None, "sum", {"hidden": True})
    assert r.status == 401 and r.error_code == "UNAUTHENTICATED"
    assert hidden_in_db(admin_web, "sum") == 0


def test_a_user_gets_403_forbidden_with_the_common_error_format(admin_web):
    r = patch(admin_web, admin_web.login("alice"), "sum", {"hidden": True})
    assert r.status == 403 and r.error_code == "FORBIDDEN"
    assert r.json() == {"error": {"code": "FORBIDDEN", "message": "forbidden"}}
    assert r.headers["cache-control"] == "no-store"
    assert hidden_in_db(admin_web, "sum") == 0 and admin_web.spy.dirty == 0


def test_every_admin_route_answers_401_without_a_session_and_403_to_a_user(admin_web):
    """Quét mọi route `/api/admin/*` đang có: PR sau thêm route thì tự được kiểm."""
    cookie = admin_web.login("alice")
    seen = 0
    for route in admin_web.app.routes:
        if not route.path.startswith("/api/admin/"):
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            path = route.path.replace("{problem_id}", "sum").replace("{submission_id}", "1").replace("{user_id}", "1")
            assert "{" not in path, path
            body = {} if method != "GET" else None
            assert admin_web.request(method, path, obj=body).status == 401, (method, path)
            r = admin_web.request(method, path, obj=body, cookie=cookie)
            assert (r.status, r.error_code) == (403, "FORBIDDEN"), (method, path, r.status)
            seen += 1
    assert seen >= 1


@pytest.mark.parametrize("kwargs", [
    {"obj": {"hidden": "not-a-bool"}},
    {"body": b"{broken", "content_type": "json"},
    {"body": b"[1, 2]", "content_type": "json"},
    {"obj": {"hidden": True}, "content_type": "text/plain"},
    {"body": b'{"hidden": true, "pad": "' + b"p" * (200 * 1024) + b'"}', "content_type": "json"},
])
def test_a_user_gets_403_before_any_body_or_content_type_error(admin_web, kwargs):
    for pid in ("sum", "no-such-problem", "bad id!"):
        r = admin_web.request("PATCH", PATH.format(pid.replace(" ", "%20")), cookie=admin_web.login("alice"), **kwargs)
        assert (r.status, r.error_code) == (403, "FORBIDDEN"), (pid, r.status)


def test_the_role_is_read_from_the_database_on_every_request(admin_web):
    cookie = admin_web.login("root")
    assert patch(admin_web, cookie, "sum", {"hidden": False}).status == 200
    with admin_web.db.write() as c:
        c.execute("UPDATE users SET role = 'user' WHERE username = 'root'")
    assert patch(admin_web, cookie, "sum", {"hidden": False}).status == 403
    with admin_web.db.write() as c:
        c.execute("UPDATE users SET role = 'admin' WHERE username = 'root'")
    assert patch(admin_web, cookie, "sum", {"hidden": False}).status == 200


def test_a_client_cannot_claim_a_role_or_a_user_id(admin_web):
    cookie = admin_web.login("alice")
    for extra in ({"role": "admin"}, {"user_id": 3}, {"admin": True}):
        r = patch(admin_web, cookie, "sum", {"hidden": True, **extra})
        assert r.status == 403, extra
    r = admin_web.request("PATCH", PATH.format("sum"), obj={"hidden": True}, cookie=cookie,
                          headers={"X-Role": "admin", "X-User-Id": "3"})
    assert r.status == 403 and hidden_in_db(admin_web, "sum") == 0


def test_only_patch_is_allowed_on_the_problem_route(admin_web):
    cookie = admin_web.login("root")
    for method in ("GET", "POST", "PUT", "DELETE"):
        r = admin_web.request(method, PATH.format("sum"), obj={"hidden": True} if method != "GET" else None, cookie=cookie)
        assert r.status == 405, method
    assert hidden_in_db(admin_web, "sum") == 0


# ---- ẩn / hiện đề -------------------------------------------------------------------------------------------------

def test_hiding_returns_the_problem_summary_and_hides_it_from_users(admin_web):
    root, alice = admin_web.login("root"), admin_web.login("alice")
    r = patch(admin_web, root, "sum", {"hidden": True})
    assert r.status == 200
    assert r.json() == {"id": "sum", "title": "Sum", "time_limit_ms": 1000, "memory_limit_kib": 262144, "hidden": True}
    assert hidden_in_db(admin_web, "sum") == 1
    assert admin_web.get("/api/problems", cookie=alice).json() == {"problems": []}
    r = admin_web.get("/api/problems/sum", cookie=alice)
    assert (r.status, r.error_code) == (404, "PROBLEM_NOT_FOUND")
    r = admin_web.submit(alice, uuid_token(1))
    assert (r.status, r.error_code) == (404, "PROBLEM_NOT_FOUND")
    # admin vẫn thấy và nộp được vào đề ẩn
    assert [p["id"] for p in admin_web.get("/api/problems", cookie=root).json()["problems"]] == ["secret", "sum"]
    assert admin_web.submit(root, uuid_token(2)).status == 201


def test_unhiding_makes_the_problem_visible_again(admin_web):
    root, alice = admin_web.login("root"), admin_web.login("alice")
    r = patch(admin_web, root, "secret", {"hidden": False})
    assert r.status == 200 and r.json()["hidden"] is False
    assert sorted(p["id"] for p in admin_web.get("/api/problems", cookie=alice).json()["problems"]) == ["secret", "sum"]
    assert admin_web.get("/api/problems/secret", cookie=alice).status == 200
    assert admin_web.submit(alice, uuid_token(3), problem_id="secret").status == 201


def test_a_change_notifies_the_scoreboard_once_and_the_same_value_does_not(admin_web):
    root = admin_web.login("root")
    assert patch(admin_web, root, "sum", {"hidden": False}).status == 200  # đang hiện sẵn
    assert patch(admin_web, root, "secret", {"hidden": True}).status == 200  # đang ẩn sẵn
    assert admin_web.spy.dirty == 0
    assert patch(admin_web, root, "sum", {"hidden": True}).status == 200
    assert admin_web.spy.dirty == 1
    assert patch(admin_web, root, "sum", {"hidden": True}).status == 200
    assert admin_web.spy.dirty == 1
    assert patch(admin_web, root, "sum", {"hidden": False}).status == 200
    assert admin_web.spy.dirty == 2


def test_a_failing_hook_does_not_fail_the_request(admin_web, caplog):
    def boom():
        raise RuntimeError("hook hỏng")

    admin_web.spy.scoreboard_dirty = boom
    with caplog.at_level(logging.ERROR, logger="judgehive.rest"):
        r = patch(admin_web, admin_web.login("root"), "sum", {"hidden": True})
    assert r.status == 200 and hidden_in_db(admin_web, "sum") == 1
    assert any("scoreboard_dirty" in rec.getMessage() for rec in caplog.records)


@pytest.mark.parametrize("obj", [
    {}, {"hidden": 1}, {"hidden": 0}, {"hidden": "true"}, {"hidden": "false"}, {"hidden": None}, {"hidden": []},
    {"hidden": True, "title": "x"}, {"hide": True},
])
def test_hidden_must_be_the_only_field_and_a_real_boolean(admin_web, obj):
    r = patch(admin_web, admin_web.login("root"), "sum", obj)
    assert (r.status, r.error_code) == (400, "INVALID_REQUEST"), obj
    assert hidden_in_db(admin_web, "sum") == 0 and admin_web.spy.dirty == 0
    assert "x" not in r.json()["error"]["message"]  # không lặp lại giá trị client


@pytest.mark.parametrize("body", [b"{broken", b"[true]", b"true", b""])
def test_a_broken_body_is_400_for_the_admin(admin_web, body):
    r = admin_web.request("PATCH", PATH.format("sum"), body=body, cookie=admin_web.login("root"))
    assert (r.status, r.error_code) == (400, "INVALID_REQUEST")


def test_content_type_and_size_are_checked_for_the_admin(admin_web):
    root = admin_web.login("root")
    r = patch(admin_web, root, "sum", {"hidden": True}, content_type="text/plain")
    assert (r.status, r.error_code) == (415, "UNSUPPORTED_MEDIA_TYPE")
    r = admin_web.request("PATCH", PATH.format("sum"), body=b'{"hidden": true, "pad": "' + b"p" * (200 * 1024) + b'"}',
                          cookie=root)
    assert (r.status, r.error_code) == (413, "BODY_TOO_LARGE")
    assert hidden_in_db(admin_web, "sum") == 0


def test_unknown_and_malformed_problem_ids_are_404_but_a_bad_body_is_400_first(admin_web):
    root = admin_web.login("root")
    for pid in ("no-such-problem", "bad%20id", "x" * 65):
        r = patch(admin_web, root, pid, {"hidden": True})
        assert (r.status, r.error_code) == (404, "PROBLEM_NOT_FOUND"), pid
    r = patch(admin_web, root, "no-such-problem", {"hidden": "yes"})
    assert (r.status, r.error_code) == (400, "INVALID_REQUEST")
    assert admin_web.spy.dirty == 0


def test_the_action_is_logged_with_the_admin_and_the_problem_but_not_the_body(admin_web, caplog):
    with caplog.at_level(logging.INFO, logger="judgehive.rest"):
        assert patch(admin_web, admin_web.login("root"), "sum", {"hidden": True}).status == 200
        assert patch(admin_web, admin_web.login("root"), "sum", {"hidden": True}).status == 200  # không đổi: không log
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("ADMIN")]
    admin_id = admin_web.db.conn().execute("SELECT id FROM users WHERE username = 'root'").fetchone()[0]
    assert lines == [f"ADMIN admin={admin_id} hidden problem=sum False -> True"]


# ---- đề ẩn và bài đã nộp, bảng xếp hạng --------------------------------------------------------------------------

def wait_for_board(web, cookie, predicate, timeout=6.0):
    last = []

    def check():
        last[:] = [get_board(web, cookie)]
        return predicate(last[0])

    assert wait_until(check, timeout), last
    return last[0]


def test_hiding_removes_the_problem_from_the_board_by_itself_and_unhiding_brings_it_back(admin_web):
    worker = admin_web.worker()
    alice, root = admin_web.login("alice"), admin_web.login("root")
    admin_web.submit(alice, uuid_token(1))
    worker.finish_ac(worker.take_job())
    wait_for_board(admin_web, alice, lambda b: len(b["rows"]) == 1)
    assert patch(admin_web, root, "sum", {"hidden": True}).status == 200
    wait_for_board(admin_web, alice, lambda b: b["rows"] == [])  # không ai gọi mark_dirty bằng tay
    assert patch(admin_web, root, "sum", {"hidden": False}).status == 200
    wait_for_board(admin_web, alice, lambda b: len(b["rows"]) == 1)


def test_a_submission_waiting_when_the_problem_is_hidden_is_still_judged_and_stays_in_history(admin_web):
    worker = admin_web.worker()
    alice, root = admin_web.login("alice"), admin_web.login("root")
    sid = admin_web.submit(alice, uuid_token(1)).json()["submission_id"]
    job = worker.take_job()  # đã giao: đang chấm
    assert patch(admin_web, root, "sum", {"hidden": True}).status == 200
    worker.finish_ac(job)
    admin_web.wait_state(alice, sid, "judged")
    row = admin_web.get(f"/api/submissions/{sid}", cookie=alice).json()
    assert row["verdict"] == "AC" and row["problem_id"] == "sum"
    ids = [s["submission_id"] for s in admin_web.get("/api/submissions", cookie=alice).json()["submissions"]]
    assert ids == [sid]
    wait_for_board(admin_web, alice, lambda b: b["rows"] == [])
