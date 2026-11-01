"""REST: theo dõi của admin, chỉ đọc: `GET /api/admin/workers` và `GET /api/admin/submissions` (README 3.6, D7, D11, D22).

Không khóa. Các test về lõi mạng (`WorkerServer.admin_view`) dùng worker giả thật qua cổng TCP thật."""

import json
import time

import pytest

from _rest_support import uuid_token
from _support import insert_submission, wait_until
from server.rest.admin_routes import QUEUE_COUNTS_SQL

WORKER_KEYS = {"worker_id", "name", "addr", "state", "connected_s", "last_seen_s", "jobs_done", "job"}
JOB_KEYS = {"submission_id", "attempt_id", "confirmed", "tests_done", "tests_total", "is_rejudge"}
USER_KEYS = {"submission_id", "problem_id", "problem_title", "language", "received_at", "state", "verdict", "time_ms",
             "memory_kib", "failed_test", "failed_is_sample", "re_kind", "rejudged_at"}
ADMIN_EXTRA = {"user_id", "username", "attempt_id", "delivery_count", "total_deliveries", "is_rejudge", "rejudge_failed"}


def admin_cookie(web):
    """Đăng nhập root một lần cho mỗi server (đăng nhập có rate limit 10 lần mỗi 60 giây)."""
    if not hasattr(web, "_root_cookie"):
        web._root_cookie = web.login("root")
    return web._root_cookie


def workers(web, cookie=None):
    r = web.get("/api/admin/workers", cookie=cookie or admin_cookie(web))
    assert r.status == 200, (r.status, r.body)
    return r.json()


def subs(web, query="", cookie=None):
    r = web.get("/api/admin/submissions" + query, cookie=cookie or admin_cookie(web))
    assert r.status == 200, (query, r.status, r.body)
    return r.json()


def user_id(web, name):
    return web.db.conn().execute("SELECT id FROM users WHERE username = ?", (name,)).fetchone()[0]


# ---- phân quyền -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/api/admin/workers", "/api/admin/submissions"])
def test_monitor_endpoints_are_admin_only(web, path):
    assert web.get(path).status == 401
    alice = web.login("alice")
    for query in ("", "?bogus=1", "?limit=0"):  # 403 trước khi tham số query được kiểm
        r = web.get(path + query, cookie=alice)
        assert (r.status, r.error_code) == (403, "FORBIDDEN"), (path, query)


def test_a_user_cannot_see_their_own_submissions_through_the_admin_list(web):
    alice = web.login("alice")
    web.submit(alice, uuid_token(1))
    assert web.get("/api/admin/submissions?user_id=%d" % user_id(web, "alice"), cookie=alice).status == 403


# ---- GET /api/admin/workers ---------------------------------------------------------------------------------------

def test_no_worker_and_an_empty_queue(web):
    assert workers(web) == {"workers": [], "queue": {"queued": 0, "rejudge_queued": 0, "judging": 0}}


def test_an_idle_worker_has_exactly_the_documented_fields(web):
    w = web.worker("alpha")
    wait_until(lambda: workers(web)["workers"])
    [row] = workers(web)["workers"]
    assert set(row) == WORKER_KEYS
    assert (row["worker_id"], row["name"], row["state"], row["job"]) == (w.worker_id, "alpha", "idle", None)
    assert row["addr"].startswith("127.0.0.1:") and row["addr"].split(":")[1].isdigit()
    assert all(type(row[k]) is int and row[k] >= 0 for k in ("connected_s", "last_seen_s", "jobs_done"))
    assert row["jobs_done"] == 0


def test_every_idle_worker_is_idle_even_though_the_dispatcher_reserves_one_while_waiting(web):
    """Dispatcher giữ chỗ worker rảnh lâu nhất khi hàng đợi rỗng (state nội bộ `reserved`); admin vẫn thấy `idle`."""
    fakes = [web.worker(name) for name in ("one", "two", "three")]  # giữ tham chiếu: bị thu gom thì socket đóng
    assert wait_until(lambda: len(workers(web)["workers"]) == 3)
    time.sleep(0.5)  # đủ để dispatcher đã giữ chỗ một worker
    server = web.app.state.worker_server
    assert "reserved" in {s["state"] for s in server.snapshot()}  # xác nhận nội bộ đúng là có reserved
    assert [r["state"] for r in workers(web)["workers"]] == ["idle"] * 3
    assert len(fakes) == 3


def test_a_busy_worker_shows_its_job_progress_and_confirmation(web):
    w = web.worker()
    cookie = web.login("alice")
    sid = web.submit(cookie, uuid_token(1)).json()["submission_id"]
    job = w.take_job()

    def current():
        return workers(web)["workers"][0]

    assert wait_until(lambda: current()["state"] == "busy")
    row = current()
    assert set(row["job"]) == JOB_KEYS
    assert row["job"] == {"submission_id": sid, "attempt_id": job["attempt_id"], "confirmed": False,
                          "tests_done": None, "tests_total": len(job["tests"]), "is_rejudge": False}
    assert workers(web)["queue"] == {"queued": 0, "rejudge_queued": 0, "judging": 1}
    w.progress(job, 0)
    assert wait_until(lambda: current()["job"]["confirmed"] is True)
    assert current()["job"]["tests_done"] == 0
    for done in (1, 2):  # PROGRESS phải đúng thứ tự: tests_done tăng đúng 1 mỗi lần
        w.progress(job, done)
    assert wait_until(lambda: current()["job"]["tests_done"] == 2)
    for done in range(3, len(job["tests"]) + 1):
        w.progress(job, done)
    w.result(job)
    assert wait_until(lambda: current()["state"] == "idle")
    assert (current()["job"], current()["jobs_done"]) == (None, 1)


def test_workers_are_sorted_by_worker_id_and_a_dead_worker_disappears(web):
    first, second = web.worker("one"), web.worker("two")
    assert wait_until(lambda: len(workers(web)["workers"]) == 2)
    assert [r["worker_id"] for r in workers(web)["workers"]] == sorted([first.worker_id, second.worker_id])
    first.close()
    assert wait_until(lambda: [r["name"] for r in workers(web)["workers"]] == ["two"])


def test_the_worker_view_leaks_no_secret_user_or_code(web):
    from _support import SECRET

    w = web.worker()
    cookie = web.login("alice")
    web.submit(cookie, uuid_token(1), code="print('CANDIDATE-CODE-77')\n")
    w.take_job()
    assert wait_until(lambda: workers(web)["workers"][0]["job"] is not None)
    text = json.dumps(workers(web))
    for forbidden in (SECRET, "CANDIDATE-CODE-77", "user_id", "alice", "HIDDEN-IN", "HIDDEN-OUT"):
        assert forbidden not in text, forbidden


def test_admin_view_does_not_change_snapshot(web):
    """`snapshot()` giữ nguyên kiểu trả về: test đã khóa dùng `len(snapshot())` (tests/invariants)."""
    w = web.worker()
    server = web.app.state.worker_server
    assert wait_until(lambda: len(server.snapshot()) == 1)
    [snap] = server.snapshot()
    assert set(snap) == {"worker_id", "name", "addr", "state", "submission_id", "attempt_id"}
    assert isinstance(snap["addr"], tuple)  # admin_view đổi sang chuỗi, snapshot() thì không
    [view] = server.admin_view()
    assert view["worker_id"] == snap["worker_id"] == w.worker_id and isinstance(view["addr"], str)


def test_queue_counts_split_new_rejudge_and_judging(web):
    alice = user_id(web, "alice")
    for _ in range(2):
        insert_submission(web.db, alice, state="queued")
    for _ in range(3):
        insert_submission(web.db, alice, state="queued", is_rejudge=1)
    insert_submission(web.db, alice, state="judging")
    insert_submission(web.db, alice, state="judging", is_rejudge=1)
    insert_submission(web.db, alice, state="done", verdict="AC")
    assert workers(web)["queue"] == {"queued": 2, "rejudge_queued": 3, "judging": 2}


def test_queue_counts_use_the_covering_index(web):
    plan = [r[3] for r in web.db.conn().execute("EXPLAIN QUERY PLAN " + QUEUE_COUNTS_SQL)]
    assert plan and all("COVERING INDEX idx_submissions_pending" in line for line in plan), plan
    assert not any(line.startswith("SCAN") for line in plan), plan


@pytest.mark.parametrize("query", ["?x=1", "?limit=5", "?", "?a=1&a=2"])
def test_workers_rejects_any_query_parameter(web, query):
    r = web.get("/api/admin/workers" + query, cookie=admin_cookie(web))
    if query == "?":
        assert r.status == 200  # chuỗi query rỗng không có tham số nào
    else:
        assert (r.status, r.error_code) == (400, "INVALID_REQUEST"), query


def test_a_post_to_the_monitor_endpoints_is_405(web):
    root = admin_cookie(web)
    for path in ("/api/admin/workers", "/api/admin/submissions"):
        assert web.post(path, {}, cookie=root).status == 405


# ---- GET /api/admin/submissions ------------------------------------------------------------------------------------

def test_list_shows_everyones_submissions_newest_first_with_documented_fields(web):
    alice, bob, root = web.login("alice"), web.login("bob"), web.login("root")
    ids = [web.submit(alice, uuid_token(1)).json()["submission_id"],
           web.submit(bob, uuid_token(2)).json()["submission_id"],
           web.submit(root, uuid_token(3), problem_id="secret").json()["submission_id"]]  # đề ẩn, admin nộp
    body = subs(web)
    assert [s["submission_id"] for s in body["submissions"]] == ids[::-1] and body["next_before_id"] is None
    first = body["submissions"][-1]
    assert set(first) == USER_KEYS | ADMIN_EXTRA
    assert (first["username"], first["user_id"], first["state"], first["is_rejudge"], first["rejudge_failed"]) == (
        "alice", user_id(web, "alice"), "queued", False, False)
    assert (first["attempt_id"], first["delivery_count"], first["total_deliveries"]) == (0, 0, 0)
    assert {s["username"] for s in body["submissions"]} == {"alice", "bob", "root"}


def test_list_never_carries_code_messages_or_hidden_test_data(web):
    alice = user_id(web, "alice")
    insert_submission(web.db, alice, code="print('SECRET-CODE-1')", state="done", verdict="WA", failed_test=3,
                      failed_is_sample=0, sample_stdout="SECRET-STDOUT", ce_message="SECRET-CE", re_kind="EXIT_NONZERO",
                      time_ms=12, memory_kib=2048)
    insert_submission(web.db, alice, code="x", state="done", verdict="CE", ce_message="SECRET-CE-2")
    body = subs(web)
    text = json.dumps(body)
    for forbidden in ("SECRET-CODE-1", "SECRET-STDOUT", "SECRET-CE", "SECRET-CE-2", "HIDDEN-IN", "HIDDEN-OUT", "exit_code"):
        assert forbidden not in text, forbidden
    for s in body["submissions"]:
        assert not ({"code", "ce_message", "sample_stdout", "sample_input", "sample_expected_output"} & set(s)), s
    wa = [s for s in body["submissions"] if s["verdict"] == "WA"][0]
    assert (wa["failed_test"], wa["failed_is_sample"], wa["re_kind"]) == (3, False, None)  # whitelist theo verdict (D11)


def test_state_filter_uses_the_public_names(web):
    alice = user_id(web, "alice")
    for state, verdict in (("queued", None), ("judging", None), ("done", "AC")):
        insert_submission(web.db, alice, state=state, verdict=verdict)
    for public, internal in (("queued", "queued"), ("judging", "judging"), ("judged", "done")):
        got = subs(web, "?state=" + public)["submissions"]
        assert [s["state"] for s in got] == [public], public
    for bad in ("done", "QUEUED", "", "x"):
        assert web.get("/api/admin/submissions?state=" + bad, cookie=admin_cookie(web)).status == 400, bad


def test_problem_and_user_filters(web):
    alice, bob = user_id(web, "alice"), user_id(web, "bob")
    a = insert_submission(web.db, alice, problem_id="sum")
    b = insert_submission(web.db, bob, problem_id="sum")
    c = insert_submission(web.db, alice, problem_id="secret")
    ids = lambda q: [s["submission_id"] for s in subs(web, q)["submissions"]]
    assert ids("?problem_id=sum") == [b, a]
    assert ids("?problem_id=secret") == [c]
    assert ids("?problem_id=no-such-problem") == []
    assert ids("?user_id=%d" % alice) == [c, a]
    assert ids("?user_id=%d&problem_id=sum" % alice) == [a]
    assert ids("?user_id=999999") == []


def test_pagination_matches_the_user_history_contract(web):
    alice = user_id(web, "alice")
    made = [insert_submission(web.db, alice) for _ in range(7)]
    page = subs(web, "?limit=3")
    assert [s["submission_id"] for s in page["submissions"]] == made[::-1][:3]
    assert page["next_before_id"] == made[::-1][2]
    page2 = subs(web, "?limit=3&before_id=%d" % page["next_before_id"])
    assert [s["submission_id"] for s in page2["submissions"]] == made[::-1][3:6]
    last = subs(web, "?limit=3&before_id=%d" % page2["next_before_id"])
    assert [s["submission_id"] for s in last["submissions"]] == made[:1] and last["next_before_id"] is None
    assert len(subs(web)["submissions"]) == 7  # mặc định 50


def test_default_limit_is_50_and_max_is_200(web):
    alice = user_id(web, "alice")
    with web.db.write() as c:
        c.executemany("INSERT INTO submissions (user_id, problem_id, language, code, submitted_at, state) "
                      "VALUES (?, 'sum', 'python', 'c', 1, 'queued')", [(alice,)] * 205)
    assert len(subs(web)["submissions"]) == 50
    assert len(subs(web, "?limit=200")["submissions"]) == 200
    assert web.get("/api/admin/submissions?limit=201", cookie=admin_cookie(web)).status == 400


@pytest.mark.parametrize("query", [
    "?limit=0", "?limit=-1", "?limit=abc", "?limit=1.5", "?limit=", "?limit=" + "9" * 5000,
    "?before_id=0", "?before_id=abc", "?before_id=", "?before_id=" + "1" * 5000,
    "?user_id=0", "?user_id=x", "?user_id=", "?user_id=" + "9" * 30, "?user_id=%EF%BC%95",
    "?problem_id=bad%20id", "?problem_id=", "?problem_id=" + "a" * 65,
    "?state=weird", "?state=",
    "?bogus=1", "?role=admin", "?limit=5&limit=6", "?state=queued&state=queued", "?user_id=1&user_id=2",
], ids=lambda q: q if len(q) <= 40 else q[:20] + f"...({len(q)} ký tự)")
def test_bad_or_unknown_or_repeated_query_parameters_are_400(web, query):
    r = web.get("/api/admin/submissions" + query, cookie=admin_cookie(web))
    assert (r.status, r.error_code) == (400, "INVALID_REQUEST"), (query, r.status)


def test_error_messages_never_echo_client_values(web):
    r = web.get("/api/admin/submissions?problem_id=%3Cscript%3E&zzz-private=1", cookie=admin_cookie(web))
    assert r.status == 400 and "script" not in r.body.decode() and "zzz-private" not in r.body.decode()


def test_admin_sees_rejudge_flags_and_delivery_counters(web):
    alice = user_id(web, "alice")
    insert_submission(web.db, alice, state="done", verdict="AC", is_rejudge=0, rejudge_failed=1, attempt_id=4,
                      delivery_count=2, total_deliveries=3)
    insert_submission(web.db, alice, state="queued", is_rejudge=1)
    newest, older = subs(web)["submissions"]
    assert newest["is_rejudge"] is True and newest["state"] == "queued"
    assert (older["rejudge_failed"], older["attempt_id"], older["delivery_count"], older["total_deliveries"]) == (
        True, 4, 2, 3)
