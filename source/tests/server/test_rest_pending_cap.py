"""REST: trần số bài đang chờ chấm của `POST /api/submissions` (README 3.2, 3.3, 3.5, D25).

Các test khác của tests/server chạy với trần rất cao (`make_config`); chỉ file này đặt trần thấp. Không có worker nào kết
nối, nên bài nộp ở lại `queued` cho tới khi test tự đổi state trong DB."""

import threading

import pytest

from _rest_support import FakeClock, populate, uuid_token
from _support import insert_submission
from server import config
from server.rest.submission_routes import PENDING_OF_USER_SQL, QUEUED_GLOBAL_SQL

USER_CAP, GLOBAL_CAP = 3, 5


def count_rows(web):
    return web.db.conn().execute("SELECT COUNT(*) FROM submissions").fetchone()[0]


def set_state(web, sid, state):
    with web.db.write() as c:
        c.execute("UPDATE submissions SET state = ? WHERE id = ?", (state, sid))


def fill(web, cookie, count, first=1):
    """Nộp `count` bài liên tiếp (token `first`..), kỳ vọng tất cả 201; trả danh sách submission_id."""
    ids = []
    for n in range(first, first + count):
        r = web.submit(cookie, uuid_token(n))
        assert r.status == 201, (n, r.status, r.body)
        ids.append(r.json()["submission_id"])
    return ids


@pytest.fixture
def capped(make_web):
    w = make_web(pending_per_user=USER_CAP, pending_global=GLOBAL_CAP, submit_rate_limit=1000)
    populate(w.db)
    return w.start()


def fill_global(web):
    """alice 3 bài + bob 2 bài = 5 bài `queued`: đầy trần toàn hệ thống nhưng root chưa có bài nào."""
    alice, bob = web.login("alice"), web.login("bob")
    return fill(web, alice, 3, first=1), fill(web, bob, 2, first=10)


# ---- trần của một người ---------------------------------------------------------------------------------------

def test_the_submission_after_the_user_cap_is_429_with_retry_after_and_a_json_error(capped):
    cookie = capped.login("alice")
    fill(capped, cookie, USER_CAP)
    r = capped.submit(cookie, uuid_token(99))
    assert r.status == 429 and r.error_code == "TOO_MANY_PENDING"
    assert r.headers["retry-after"] == "5" and r.headers["cache-control"] == "no-store"
    assert set(r.json()) == {"error"} and set(r.json()["error"]) == {"code", "message"}
    assert count_rows(capped) == USER_CAP  # không lưu bài


def test_judging_counts_toward_the_user_cap_and_done_does_not(capped):
    cookie = capped.login("alice")
    ids = fill(capped, cookie, USER_CAP)
    set_state(capped, ids[0], "judging")
    assert capped.submit(cookie, uuid_token(50)).status == 429  # judging vẫn đang chờ
    set_state(capped, ids[0], "done")
    assert capped.submit(cookie, uuid_token(51)).status == 201  # có kết quả thì nộp thêm được
    assert capped.submit(cookie, uuid_token(52)).status == 429


def test_the_user_cap_is_per_user(capped):
    fill(capped, capped.login("alice"), USER_CAP)
    assert capped.submit(capped.login("bob"), uuid_token(60)).status == 201


def test_the_admin_is_not_exempt_from_the_user_cap(capped):
    root = capped.login("root")
    fill(capped, root, USER_CAP)
    r = capped.submit(root, uuid_token(70))
    assert r.status == 429 and r.error_code == "TOO_MANY_PENDING"


def test_rejudge_rows_are_not_counted_and_do_not_block_anyone(capped):
    cookie = capped.login("alice")
    alice_id = capped.get("/api/me", cookie=cookie).json()["user"]["id"]
    for _ in range(GLOBAL_CAP + 3):
        insert_submission(capped.db, alice_id, is_rejudge=1)  # đang chờ chấm lại: không tính vào cả hai trần
    assert capped.submit(cookie, uuid_token(1)).status == 201
    assert capped.submit(capped.login("bob"), uuid_token(2)).status == 201


def test_an_ordinary_queued_row_of_another_origin_is_counted(capped):
    cookie = capped.login("alice")
    alice_id = capped.get("/api/me", cookie=cookie).json()["user"]["id"]
    for _ in range(USER_CAP):
        insert_submission(capped.db, alice_id)  # is_rejudge = 0, queued, không qua REST
    assert capped.submit(cookie, uuid_token(1)).error_code == "TOO_MANY_PENDING"


# ---- trần toàn hệ thống ---------------------------------------------------------------------------------------

def test_the_global_cap_is_503_with_retry_after_and_a_json_error(capped):
    fill_global(capped)
    root = capped.login("root")  # root chưa có bài nào nên không dính trần người dùng
    r = capped.submit(root, uuid_token(80))
    assert r.status == 503 and r.error_code == "QUEUE_FULL"
    assert r.headers["retry-after"] == "10" and r.headers["cache-control"] == "no-store"
    assert set(r.json()["error"]) == {"code", "message"}
    assert count_rows(capped) == GLOBAL_CAP


def test_judging_does_not_count_toward_the_global_cap(capped):
    alice_ids, _ = fill_global(capped)
    root = capped.login("root")
    assert capped.submit(root, uuid_token(81)).status == 503
    set_state(capped, alice_ids[0], "judging")  # còn 4 bài queued
    assert capped.submit(root, uuid_token(82)).status == 201
    assert capped.submit(root, uuid_token(83)).status == 503  # lại 5 bài queued


def test_the_global_cap_has_no_exception_for_the_admin(capped):
    fill_global(capped)
    assert capped.submit(capped.login("root"), uuid_token(84)).error_code == "QUEUE_FULL"


def test_a_requeued_submission_still_counts_toward_the_global_cap(capped):
    alice_ids, _ = fill_global(capped)
    set_state(capped, alice_ids[0], "judging")
    set_state(capped, alice_ids[0], "queued")  # như bài bị giao lại sau khi worker chết
    assert capped.submit(capped.login("root"), uuid_token(85)).error_code == "QUEUE_FULL"


# ---- thứ tự kiểm tra ------------------------------------------------------------------------------------------

def test_order_rate_limit_then_user_cap_then_global_cap(make_web):
    clock = FakeClock()
    w = make_web(clock=clock, pending_per_user=3, pending_global=3, submit_rate_limit=3, submit_rate_window_s=60)
    populate(w.db)
    w.start()
    alice = w.login("alice")
    fill(w, alice, 3)
    # cả ba giới hạn cùng bị chạm: rate limit đứng đầu
    assert w.submit(alice, uuid_token(10)).error_code == "RATE_LIMITED"
    clock.advance(61)
    # rate limit đã hết, trần người dùng và trần toàn hệ thống cùng bị chạm: trần người dùng đứng trước
    assert w.submit(alice, uuid_token(11)).error_code == "TOO_MANY_PENDING"
    # người chưa có bài nào chỉ chạm trần toàn hệ thống
    r = w.submit(w.login("bob"), uuid_token(12))
    assert r.status == 503 and r.error_code == "QUEUE_FULL"


def test_invalid_and_unknown_requests_come_before_the_caps(capped):
    cookie = capped.login("alice")
    fill(capped, cookie, USER_CAP)
    assert capped.submit(cookie, uuid_token(99), problem_id="nope").status == 404  # token chưa dùng: token 1 là bài đã nộp (200)
    assert capped.submit(cookie, "not-a-uuid").status == 400
    assert capped.post("/api/submissions", obj={"problem_id": "sum"}, cookie=cookie).status == 400


def test_a_duplicate_token_is_200_even_when_both_caps_are_full(capped):
    alice_ids, _ = fill_global(capped)
    cookie = capped.login("alice")
    r = capped.submit(cookie, uuid_token(1), code="print('khác')\n")
    assert r.status == 200 and r.json()["submission_id"] == alice_ids[0]
    assert count_rows(capped) == GLOBAL_CAP


# ---- bị từ chối thì không lưu, không tính rate limit ----------------------------------------------------------

def test_rejected_requests_are_not_stored_and_do_not_use_the_rate_limit(make_web):
    clock = FakeClock()
    w = make_web(clock=clock, pending_per_user=2, pending_global=1000, submit_rate_limit=3, submit_rate_window_s=60)
    populate(w.db)
    w.start()
    cookie = w.login("alice")
    ids = fill(w, cookie, 2)
    for _ in range(5):
        assert w.submit(cookie, uuid_token(3)).error_code == "TOO_MANY_PENDING"  # cùng token, bị từ chối
    assert count_rows(w) == 2
    set_state(w, ids[0], "done")
    # 5 lần bị từ chối không chiếm chỗ trong rate limit (3 bài mỗi 60 giây): bài thứ ba vẫn nộp được, dùng lại token cũ
    r = w.submit(cookie, uuid_token(3))
    assert r.status == 201 and count_rows(w) == 3


# ---- đồng thời, restart ---------------------------------------------------------------------------------------

def run_threads(web, jobs):
    results = []
    lock = threading.Lock()
    gate = threading.Barrier(len(jobs))

    def go(cookie, n):
        gate.wait()
        r = web.submit(cookie, uuid_token(n))
        with lock:
            results.append(r)

    threads = [threading.Thread(target=go, args=job) for job in jobs]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    return results


def test_concurrent_requests_of_one_user_never_exceed_the_user_cap(capped):
    cookie = capped.login("alice")
    results = run_threads(capped, [(cookie, n) for n in range(1, 11)])
    assert sorted(r.status for r in results) == [201] * USER_CAP + [429] * (10 - USER_CAP)
    assert count_rows(capped) == USER_CAP


def test_concurrent_requests_of_many_users_never_exceed_the_global_cap(make_web):
    w = make_web(pending_per_user=1000, pending_global=GLOBAL_CAP, submit_rate_limit=1000)
    populate(w.db)
    w.start()
    cookies = [w.login(name) for name in ("alice", "bob", "root")]
    results = run_threads(w, [(cookies[n % 3], n) for n in range(1, 13)])
    assert sorted(r.status for r in results) == [201] * GLOBAL_CAP + [503] * (12 - GLOBAL_CAP)
    assert count_rows(w) == GLOBAL_CAP


def test_pending_submissions_are_counted_from_the_database_after_a_restart(capped):
    cookie = capped.login("alice")
    fill(capped, cookie, USER_CAP)
    capped.restart()
    cookie = capped.login("alice")
    assert capped.submit(cookie, uuid_token(40)).error_code == "TOO_MANY_PENDING"


# ---- chỉ mục, mặc định ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("sql,args", [(PENDING_OF_USER_SQL, (1,)), (QUEUED_GLOBAL_SQL, ())],
                         ids=["pending_of_user", "queued_global"])
def test_the_counting_queries_use_the_covering_index_and_never_read_table_rows(web, sql, args):
    plan = [r[3] for r in web.db.conn().execute("EXPLAIN QUERY PLAN " + sql, args)]
    assert plan, plan
    assert all("COVERING INDEX idx_submissions_pending" in line for line in plan), plan
    assert not any(line.startswith("SCAN") for line in plan), plan


def test_production_defaults():
    cfg = config.ServerConfig()
    assert (cfg.pending_per_user, cfg.pending_global) == (5, 200)
    assert (cfg.pending_retry_after_s, cfg.queue_full_retry_after_s) == (5, 10)
