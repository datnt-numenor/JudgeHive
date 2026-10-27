"""REST: GET /api/scoreboard (README 3.3) và đường chấm bài thật tới bảng. Ứng viên khóa: dạng response, 401/405, luật loại
(admin, đề ẩn), không tính lại theo request, `version` đơn điệu và bắt đầu lại khi restart. KHÔNG khóa: các test đợi theo
thời gian thật (coalesce)."""

import json
import re

import pytest

from _rest_support import add_account, populate, uuid_token
from _support import wait_until

ISO_MS = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z")


@pytest.fixture
def board_web(make_web):
    """Server REST đã chạy với bảng tính lại sau tối đa 0,1 giây."""
    w = make_web(scoreboard_min_interval_s=0.1)
    populate(w.db)
    add_account(w.db, "carol")
    return w.start()


def get_board(web, cookie):
    r = web.get("/api/scoreboard", cookie=cookie)
    assert r.status == 200, (r.status, r.body)
    return r.json()


def finish(web, worker, verdict="AC", **fields):
    job = worker.take_job()
    if verdict == "AC":
        worker.finish_ac(job)
    else:
        worker.progress(job, 0)
        worker.result(job, verdict, **fields)
    return job


def wait_for_board(web, cookie, predicate, timeout=6.0):
    last = []

    def check():
        last[:] = [get_board(web, cookie)]
        return predicate(last[0])

    assert wait_until(check, timeout), last
    return last[0]


def test_requires_a_session(board_web):
    r = board_web.get("/api/scoreboard")
    assert r.status == 401 and r.error_code == "UNAUTHENTICATED"


def test_only_get_is_allowed(board_web):
    cookie = board_web.login("alice")
    r = board_web.post("/api/scoreboard", {}, cookie=cookie)
    assert r.status == 405 and r.error_code == "METHOD_NOT_ALLOWED" and "GET" in r.headers["allow"]


def test_initial_board_is_empty_and_has_version_1(board_web):
    r = board_web.get("/api/scoreboard", cookie=board_web.login("alice"))
    assert r.headers["content-type"] == "application/json; charset=utf-8" and r.headers["cache-control"] == "no-store"
    body = r.json()
    assert list(body) == ["version", "generated_at", "rows"]
    assert body["version"] == 1 and body["rows"] == [] and ISO_MS.fullmatch(body["generated_at"])


def test_every_logged_in_user_including_admin_sees_the_same_board(board_web):
    worker = board_web.worker()
    cookie = board_web.login("alice")
    board_web.submit(cookie, uuid_token(1))
    finish(board_web, worker)
    expected = wait_for_board(board_web, cookie, lambda b: b["rows"])
    for name in ("bob", "root"):
        body = get_board(board_web, board_web.login(name))
        assert body["rows"] == expected["rows"]


def test_an_ac_appears_with_the_exact_received_time(board_web):
    worker = board_web.worker()
    cookie = board_web.login("alice")
    sid = board_web.submit(cookie, uuid_token(1)).json()["submission_id"]
    finish(board_web, worker)
    body = wait_for_board(board_web, cookie, lambda b: b["rows"])
    received = board_web.get(f"/api/submissions/{sid}", cookie=cookie).json()["received_at"]
    assert body["rows"] == [{"rank": 1, "username": "alice", "solved": 1, "wrong": 0, "last_ac_at": received}]
    assert body["version"] >= 2


def test_wrong_attempts_before_the_ac_are_counted_end_to_end(board_web):
    worker = board_web.worker()
    cookie = board_web.login("alice")
    board_web.submit(cookie, uuid_token(1))
    finish(board_web, worker, "WA", time_ms=1, memory_kib=1, failed_test=1, failed_is_sample=True, sample_stdout="x")
    board_web.submit(cookie, uuid_token(2))
    finish(board_web, worker, "TLE", time_ms=1, memory_kib=1, failed_test=3, failed_is_sample=False)
    board_web.submit(cookie, uuid_token(3))
    finish(board_web, worker)
    board_web.submit(cookie, uuid_token(4))
    finish(board_web, worker, "WA", time_ms=1, memory_kib=1, failed_test=3, failed_is_sample=False)  # sau AC: bỏ qua
    body = wait_for_board(board_web, cookie, lambda b: b["rows"] and b["rows"][0]["wrong"] == 2
                          and board_web.get("/api/submissions?limit=1", cookie=cookie).json()["submissions"][0]["state"]
                          == "judged")
    assert [(r["username"], r["solved"], r["wrong"]) for r in body["rows"]] == [("alice", 1, 2)]


def test_admin_submissions_never_appear(board_web):
    worker = board_web.worker()
    root = board_web.login("root")
    board_web.submit(root, uuid_token(1))
    finish(board_web, worker)
    bob = board_web.login("bob")
    board_web.submit(bob, uuid_token(1))
    finish(board_web, worker)
    body = wait_for_board(board_web, bob, lambda b: b["rows"])
    assert [r["username"] for r in body["rows"]] == ["bob"]


def test_ranking_order_and_shared_ranks_through_the_api(board_web):
    worker = board_web.worker()
    cookies = {name: board_web.login(name) for name in ("alice", "bob", "carol")}
    for n, name in enumerate(("bob", "alice", "carol"), 1):
        board_web.submit(cookies[name], uuid_token(n))
        finish(board_web, worker)
    body = wait_for_board(board_web, cookies["alice"], lambda b: len(b["rows"]) == 3)
    assert [(r["rank"], r["username"]) for r in body["rows"]] == [(1, "bob"), (2, "alice"), (3, "carol")]


def test_hiding_a_problem_removes_it_from_the_board_once_the_service_is_told(board_web):
    worker = board_web.worker()
    cookie = board_web.login("alice")
    board_web.submit(cookie, uuid_token(1))
    finish(board_web, worker)
    wait_for_board(board_web, cookie, lambda b: b["rows"])
    with board_web.db.write() as c:
        c.execute("UPDATE problems SET hidden = 1 WHERE id = 'sum'")
    app = board_web.app
    app.state.loop.call_soon_threadsafe(app.state.scoreboard.mark_dirty)
    wait_for_board(board_web, cookie, lambda b: b["rows"] == [])
    with board_web.db.write() as c:
        c.execute("UPDATE problems SET hidden = 0 WHERE id = 'sum'")
    app.state.loop.call_soon_threadsafe(app.state.scoreboard.mark_dirty)
    wait_for_board(board_web, cookie, lambda b: len(b["rows"]) == 1)


def test_reading_the_board_does_not_recompute_it(board_web):
    cookie = board_web.login("alice")
    first = get_board(board_web, cookie)
    for _ in range(20):
        assert get_board(board_web, cookie) == first


def test_version_never_goes_backwards_and_a_burst_is_coalesced(board_web):
    worker = board_web.worker()
    cookie = board_web.login("alice")
    versions = [get_board(board_web, cookie)["version"]]
    for n in range(1, 7):
        board_web.submit(cookie, uuid_token(n))
        finish(board_web, worker)
        versions.append(get_board(board_web, cookie)["version"])
    final = wait_for_board(board_web, cookie, lambda b: b["rows"] and b["version"] >= versions[-1])
    versions.append(final["version"])
    assert versions == sorted(versions)
    assert final["version"] < versions[0] + 6 + 2  # 6 kết quả không thành 6 lần tính riêng lẻ trở lên


def test_non_ascii_usernames_round_trip(make_web):
    web = make_web(scoreboard_min_interval_s=0.05)
    populate(web.db)
    add_account(web.db, "Đạt-Việt")
    web.start()
    worker = web.worker()
    cookie = web.login("Đạt-Việt")
    web.submit(cookie, uuid_token(1))
    finish(web, worker)
    body = wait_for_board(web, cookie, lambda b: b["rows"])
    assert body["rows"][0]["username"] == "Đạt-Việt"
    assert json.loads(web.get("/api/scoreboard", cookie=cookie).body.decode("utf-8"))["rows"][0]["username"] == "Đạt-Việt"


def test_restart_rebuilds_the_board_from_the_database_and_restarts_the_version(board_web):
    worker = board_web.worker()
    cookie = board_web.login("alice")
    board_web.submit(cookie, uuid_token(1))
    finish(board_web, worker)
    before = wait_for_board(board_web, cookie, lambda b: b["rows"] and b["version"] >= 2)
    board_web.restart()
    cookie = board_web.login("alice")
    after = get_board(board_web, cookie)
    assert after["version"] == 1
    assert after["rows"] == before["rows"]


def test_the_first_snapshot_exists_before_the_worker_port_opens(make_web, monkeypatch):
    from server.worker_server import WorkerServer

    holder, seen = {}, []
    original = WorkerServer.start

    def checking_start(self):
        seen.append(holder["app"].state.scoreboard.latest())
        return original(self)

    monkeypatch.setattr(WorkerServer, "start", checking_start)
    web = make_web(scoreboard_min_interval_s=0.1, prepare=lambda app: holder.update(app=app))
    populate(web.db)
    web.start()
    assert len(seen) == 1 and seen[0] is not None and seen[0].version == 1
