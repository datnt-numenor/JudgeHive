"""REST: rejudge của admin, `POST /api/admin/submissions/{id}/rejudge` và `POST /api/admin/problems/{id}/rejudge`
(README 3.6, D6, D22, D36).

Server thật (uvicorn, dispatcher và cổng worker thật) với worker giả qua TCP thật. Các test tích hợp chứng minh bài vào
`PriorityQueue` TRONG BỘ NHỚ của dispatcher (bài rejudge được chấm không cần restart), không chỉ ghi DB."""

import logging

import pytest

from _rest_support import uuid_token
from _support import insert_submission, wait_until
from server.rest import admin_rejudge_routes
from server.dispatcher import LEVEL_REJUDGE

KEYS_SUBMISSION = {"submission_id", "outcome"}
KEYS_PROBLEM = {"problem_id", "queued", "after_current", "already_queued", "remaining"}


def root_cookie(web):
    if not hasattr(web, "_root_cookie"):
        web._root_cookie = web.login("root")
    return web._root_cookie


def uid(web, name):
    return web.db.conn().execute("SELECT id FROM users WHERE username = ?", (name,)).fetchone()[0]


def row(web, sid):
    return dict(web.db.conn().execute("SELECT * FROM submissions WHERE id = ?", (sid,)).fetchone())


def done(web, user="alice", problem="sum", verdict="AC", **extra):
    columns = dict(state="done", verdict=verdict, time_ms=7, memory_kib=2048, verdict_test_hash="old-hash",
                   finished_at=1_500, delivery_count=2, total_deliveries=5, attempt_id=3)
    columns.update(extra)
    return insert_submission(web.db, uid(web, user), problem, **columns)


def rejudge_sub(web, sid, cookie=None, obj=None):
    return web.post(f"/api/admin/submissions/{sid}/rejudge", {} if obj is None else obj,
                    cookie=cookie or root_cookie(web))


def rejudge_problem(web, pid="sum", cookie=None, obj=None):
    return web.post(f"/api/admin/problems/{pid}/rejudge", {} if obj is None else obj, cookie=cookie or root_cookie(web))


class Recorder:
    """Ghi lại mọi `STATUS` và `scoreboard_dirty` mà dispatcher và REST gọi (bọc hooks thật, vẫn chuyển tiếp)."""

    def __init__(self, web):
        self.events, self.dirty = [], 0
        hooks = web.app.state.hooks
        orig_status, orig_dirty = hooks.status, hooks.scoreboard_dirty

        def status(submission_id, user_id, event, **info):
            self.events.append((submission_id, event))
            orig_status(submission_id, user_id, event, **info)

        def scoreboard_dirty():
            self.dirty += 1
            orig_dirty()

        hooks.status, hooks.scoreboard_dirty = status, scoreboard_dirty

    def names(self, sid):
        return [e for s, e in self.events if s == sid]


def queued_ids(web):
    """Nội dung PriorityQueue của dispatcher: [(mức, id)] theo thứ tự lấy ra."""
    return sorted(web.app.state.dispatcher.queue.queue)


# ---- phân quyền, id, body -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/api/admin/submissions/1/rejudge", "/api/admin/problems/sum/rejudge",
                                  "/api/admin/submissions/99999/rejudge", "/api/admin/problems/nope/rejudge"])
def test_only_admin_and_403_comes_before_404(web, path):
    sid = done(web)
    assert web.post(path, {}).status == 401
    alice = web.login("alice")  # kể cả bài của chính họ, và kể cả id/đề không tồn tại
    r = web.post(path.replace("/1/", f"/{sid}/"), {}, cookie=alice)
    assert (r.status, r.error_code) == (403, "FORBIDDEN"), path
    assert row(web, sid)["state"] == "done"


@pytest.mark.parametrize("sid", ["0", "00", "abc", "01", "-1", "1.5", "99999999999999999999", "99999"])
def test_bad_or_unknown_submission_id_is_404(web, sid):
    r = web.post(f"/api/admin/submissions/{sid}/rejudge", {}, cookie=root_cookie(web))
    assert (r.status, r.error_code) == (404, "SUBMISSION_NOT_FOUND")


@pytest.mark.parametrize("pid", ["nope", "bad.id", "a" * 65])
def test_bad_or_unknown_problem_id_is_404(web, pid):
    r = web.post(f"/api/admin/problems/{pid}/rejudge", {}, cookie=root_cookie(web))
    assert (r.status, r.error_code) == (404, "PROBLEM_NOT_FOUND")


@pytest.mark.parametrize("make", [lambda web, sid: f"/api/admin/submissions/{sid}/rejudge",
                                  lambda web, sid: "/api/admin/problems/sum/rejudge"])
def test_body_must_be_an_empty_json_object(web, make):
    sid = done(web)
    path = make(web, sid)
    cookie = root_cookie(web)
    for obj in ({"x": 1}, {"outcome": "queued"}):
        r = web.post(path, obj, cookie=cookie)
        assert (r.status, r.error_code) == (400, "INVALID_REQUEST"), obj
    assert web.request("POST", path, body=b"[]", cookie=cookie).error_code == "INVALID_REQUEST"
    assert web.request("POST", path, body=b"{}", content_type="text/plain", cookie=cookie).status == 415
    assert row(web, sid)["state"] == "done"  # không thay đổi gì


def test_get_is_not_allowed(web):
    sid = done(web)
    assert web.get(f"/api/admin/submissions/{sid}/rejudge", cookie=root_cookie(web)).status == 405
    assert row(web, sid)["state"] == "done"


# ---- một bài: ba trạng thái ---------------------------------------------------------------------------------------

def test_done_submission_is_queued_keeping_verdict_and_resetting_both_counters(web):
    sid = done(web, verdict="WA", failed_test=2, rejudge_failed=1, rejudged_at=1_600)
    before = row(web, sid)
    r = rejudge_sub(web, sid)
    assert r.status == 200 and set(r.json()) == KEYS_SUBMISSION
    assert r.json() == {"submission_id": sid, "outcome": "queued"}
    after = row(web, sid)
    assert (after["state"], after["is_rejudge"], after["rejudge_requested"]) == ("queued", 1, 0)
    assert (after["delivery_count"], after["total_deliveries"]) == (0, 0)  # D22: cả hai
    for key in ("verdict", "time_ms", "memory_kib", "failed_test", "verdict_test_hash", "finished_at", "rejudge_failed",
                "rejudged_at", "attempt_id"):  # D6: kết quả cũ giữ nguyên tới khi có kết quả mới; attempt_id không reset
        assert after[key] == before[key], key


def test_rest_view_shows_queued_with_the_old_verdict(web):
    sid = done(web, verdict="WA", failed_test=1, failed_is_sample=1)
    rejudge_sub(web, sid)
    alice = web.login("alice")
    view = web.get(f"/api/submissions/{sid}", cookie=alice).json()
    assert (view["state"], view["verdict"]) == ("queued", "WA")


def test_queued_submission_is_left_alone(web):
    sid = insert_submission(web.db, uid(web, "alice"), "sum", state="queued")
    before = row(web, sid)
    assert rejudge_sub(web, sid).json() == {"submission_id": sid, "outcome": "already_queued"}
    assert row(web, sid) == before


def test_judging_submission_is_flagged_without_touching_counters_and_repeat_is_harmless(web):
    sid = insert_submission(web.db, uid(web, "alice"), "sum", state="judging", attempt_id=2, delivery_count=1,
                            total_deliveries=2)
    for _ in range(2):
        assert rejudge_sub(web, sid).json() == {"submission_id": sid, "outcome": "after_current"}
    after = row(web, sid)
    assert (after["state"], after["rejudge_requested"], after["is_rejudge"]) == ("judging", 1, 0)
    assert (after["delivery_count"], after["total_deliveries"], after["attempt_id"]) == (1, 2, 2)


def test_double_click_enqueues_once(web):
    sid = done(web)
    assert rejudge_sub(web, sid).json()["outcome"] == "queued"
    assert rejudge_sub(web, sid).json()["outcome"] == "already_queued"
    assert queued_ids(web) == [(LEVEL_REJUDGE, sid)]


def test_rejudge_pushes_no_status_and_does_not_touch_the_scoreboard(web):
    """Khóa D6: xếp hàng rejudge không đẩy STATUS (kể cả `queued`) và không gọi scoreboard_dirty (verdict chưa đổi)."""
    rec = Recorder(web)
    a = done(web)
    b = done(web, "bob")
    judging = insert_submission(web.db, uid(web, "alice"), "sum", state="judging")
    assert rejudge_sub(web, a).json()["outcome"] == "queued"
    assert rejudge_sub(web, judging).json()["outcome"] == "after_current"
    assert rejudge_problem(web).json()["queued"] == 1  # b
    assert (row(web, b)["state"], row(web, a)["state"]) == ("queued", "queued")
    assert rec.events == [] and rec.dirty == 0, (rec.events, rec.dirty)


def test_log_names_admin_action_and_ids_only(web, caplog):
    sid = done(web)
    with caplog.at_level(logging.INFO):
        rejudge_sub(web, sid)
        rejudge_problem(web)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert f"rejudge submission={sid} outcome=queued" in text
    assert "rejudge problem=sum queued=0" in text
    assert "print(1)" not in text


# ---- tích hợp với dispatcher (bộ nhớ, không restart) ----------------------------------------------------------------

def test_rejudged_submission_is_graded_without_restarting_the_server(web):
    """Bài vào hàng đợi TRONG BỘ NHỚ của dispatcher: worker nhận JOB ngay, không cần restart."""
    rec = Recorder(web)
    sid = done(web, verdict="AC")
    worker = web.worker()
    assert rejudge_sub(web, sid).json()["outcome"] == "queued"
    job = worker.take_job()
    assert job["submission_id"] == sid and job["attempt_id"] == 4  # attempt_id tăng đơn điệu, không reset
    mid = row(web, sid)
    assert (mid["state"], mid["verdict"], mid["is_rejudge"], mid["total_deliveries"]) == ("judging", "AC", 1, 1)
    worker.progress(job, 0)
    worker.result(job, "WA", time_ms=9, memory_kib=2048, failed_test=3, failed_is_sample=False, sample_stdout=None)
    web.wait_state(web.login("alice"), sid, "judged")
    end = row(web, sid)
    assert (end["verdict"], end["is_rejudge"], end["rejudge_failed"], end["failed_test"]) == ("WA", 0, 0, 3)
    assert end["rejudged_at"] is not None and end["verdict_test_hash"] != "old-hash"
    names = rec.names(sid)
    assert "queued" not in names and names[0] == "judging" and names[-1] == "done"  # D6
    assert rec.dirty >= 1  # verdict đổi nên bảng xếp hạng được tính lại
    worker.close()


def test_rejudge_goes_after_new_submissions(web):
    """Mức thấp nhất (D1, D6): bài mới nộp sau khi rejudge vẫn được giao trước."""
    old = done(web)
    assert rejudge_sub(web, old).json()["outcome"] == "queued"
    alice = web.login("alice")
    new = web.submit(alice, uuid_token(1)).json()["submission_id"]
    assert [i for _, i in queued_ids(web)] == [new, old]
    worker = web.worker()
    first = worker.take_job()
    assert first["submission_id"] == new
    worker.finish_ac(first)
    assert worker.take_job()["submission_id"] == old
    worker.close()


def test_judging_flag_requeues_after_result_and_resets_both_counters(web):
    alice = web.login("alice")
    worker = web.worker()
    sid = web.submit(alice, uuid_token(2)).json()["submission_id"]
    job = worker.take_job()
    wait_until(lambda: row(web, sid)["state"] == "judging")
    assert rejudge_sub(web, sid).json()["outcome"] == "after_current"
    worker.progress(job, 0)
    worker.result(job)
    job2 = worker.take_job()  # tự vào hàng rejudge sau RESULT, không cần gọi lại
    assert job2["submission_id"] == sid and job2["attempt_id"] == job["attempt_id"] + 1
    mid = row(web, sid)
    assert (mid["is_rejudge"], mid["rejudge_requested"], mid["delivery_count"], mid["total_deliveries"]) == (1, 0, 0, 1)
    worker.progress(job2, 0)
    worker.result(job2)
    web.wait_state(alice, sid, "judged")
    assert row(web, sid)["rejudged_at"] is not None
    worker.close()


def test_se_during_rejudge_keeps_the_old_verdict(web):
    sid = done(web, verdict="AC")
    worker = web.worker()
    rejudge_sub(web, sid)
    job = worker.take_job()
    worker.progress(job, 0)
    worker.result(job, "SE", se_detail="boom")
    wait_until(lambda: row(web, sid)["state"] == "done")
    end = row(web, sid)
    assert (end["verdict"], end["rejudge_failed"], end["is_rejudge"], end["rejudged_at"]) == ("AC", 1, 0, None)
    worker.close()


def test_queued_rejudge_survives_a_restart(web):
    sid = done(web)
    rejudge_sub(web, sid)
    web.restart()
    worker = web.worker()
    assert worker.take_job()["submission_id"] == sid  # load_pending nạp lại từ DB
    worker.close()


def test_rejudge_does_not_count_against_the_pending_caps(make_web):
    from _rest_support import populate

    w = make_web(pending_per_user=1, pending_global=1)
    populate(w.db)
    w.start()
    sid = done(w)
    assert rejudge_sub(w, sid).json()["outcome"] == "queued"
    alice = w.login("alice")
    assert w.submit(alice, uuid_token(3)).status == 201  # bài rejudge không chiếm chỗ của người nộp


# ---- rejudge cả đề ------------------------------------------------------------------------------------------------

def test_problem_rejudge_covers_every_user_and_verdict_but_only_that_problem(web):
    ids = [done(web, "alice", verdict="AC"), done(web, "bob", verdict="WA"), done(web, "root", verdict="SE"),
           done(web, "alice", verdict="CE")]
    other = done(web, "alice", problem="secret")
    waiting = insert_submission(web.db, uid(web, "bob"), "sum", state="queued")
    running = insert_submission(web.db, uid(web, "alice"), "sum", state="judging")
    flagged = insert_submission(web.db, uid(web, "bob"), "sum", state="judging", rejudge_requested=1)
    r = rejudge_problem(web)
    assert r.status == 200 and set(r.json()) == KEYS_PROBLEM
    assert r.json() == {"problem_id": "sum", "queued": 4, "after_current": 2, "already_queued": 1, "remaining": 0}
    assert {row(web, i)["state"] for i in ids} == {"queued"}
    assert all(row(web, i)["is_rejudge"] == 1 and row(web, i)["total_deliveries"] == 0 for i in ids)
    assert row(web, other)["state"] == "done"  # đề khác không đụng tới
    assert row(web, waiting)["is_rejudge"] == 0 and row(web, waiting)["state"] == "queued"
    assert row(web, running)["rejudge_requested"] == 1 and row(web, flagged)["rejudge_requested"] == 1
    assert [i for _, i in queued_ids(web)] == ids  # theo id tăng dần, mức rejudge
    assert {lvl for lvl, _ in queued_ids(web)} == {LEVEL_REJUDGE}


def test_problem_rejudge_works_on_hidden_problems_and_empty_ones(web):
    sid = done(web, problem="secret")
    assert rejudge_problem(web, "secret").json() == {"problem_id": "secret", "queued": 1, "after_current": 0,
                                                     "already_queued": 0, "remaining": 0}
    assert row(web, sid)["state"] == "queued"
    again = rejudge_problem(web, "secret").json()
    assert (again["queued"], again["already_queued"], again["remaining"]) == (0, 1, 0)
    assert rejudge_problem(web, "sum").json() == {"problem_id": "sum", "queued": 0, "after_current": 0,
                                                  "already_queued": 0, "remaining": 0}


def test_documented_batch_constants():
    assert (admin_rejudge_routes.BATCH_MAX, admin_rejudge_routes.BATCH_SIZE) == (1000, 100)  # D36


@pytest.mark.parametrize("count, queued, remaining", [(4, 4, 0), (5, 5, 0), (6, 5, 1), (12, 5, 7)])
def test_problem_rejudge_stops_at_the_batch_limit_and_reports_remaining(web, monkeypatch, count, queued, remaining):
    monkeypatch.setattr(admin_rejudge_routes, "BATCH_MAX", 5)
    monkeypatch.setattr(admin_rejudge_routes, "BATCH_SIZE", 2)
    ids = [done(web) for _ in range(count)]
    r = rejudge_problem(web).json()
    assert (r["queued"], r["remaining"]) == (queued, remaining)
    assert [row(web, i)["state"] for i in ids].count("queued") == queued
    assert [i for _, i in queued_ids(web)] == ids[:queued]  # id tăng dần: những bài đầu được xử lý trước
    # gọi lại tiếp tục từ chỗ dở: bài đã xếp hàng không chặn bài chưa xử lý
    while remaining:
        r = rejudge_problem(web).json()
        assert r["queued"] == min(remaining, 5) and r["already_queued"] >= queued
        remaining = r["remaining"]
    assert {row(web, i)["state"] for i in ids} == {"queued"}
    assert len(queued_ids(web)) == count  # không xếp hàng trùng


def test_problem_rejudge_commits_in_batches_not_one_big_transaction(web, monkeypatch):
    monkeypatch.setattr(admin_rejudge_routes, "BATCH_MAX", 1000)
    monkeypatch.setattr(admin_rejudge_routes, "BATCH_SIZE", 2)
    for _ in range(7):
        done(web)
    db = web.app.state.rest.db
    writes, orig = [], db.write

    def counting():
        writes.append(1)
        return orig()

    monkeypatch.setattr(db, "write", counting)
    cookie = root_cookie(web)
    start = len(writes)
    assert rejudge_problem(web, "secret", cookie=cookie).json()["queued"] == 0  # đề không có bài: đo số lần ghi nền
    background = len(writes) - start
    start = len(writes)
    assert rejudge_problem(web, cookie=cookie).json()["queued"] == 7
    assert len(writes) - start - background == 4  # ceil(7 / 2) lô, mỗi lô một transaction


def test_problem_rejudge_results_reach_workers_without_restart(web):
    ids = [done(web, "alice"), done(web, "bob")]
    worker = web.worker()
    assert rejudge_problem(web).json()["queued"] == 2
    got = []
    for _ in ids:
        job = worker.take_job()
        got.append(job["submission_id"])
        worker.progress(job, 0)
        worker.result(job, "WA", time_ms=1, memory_kib=1, failed_test=3, failed_is_sample=False, sample_stdout=None)
    assert got == ids
    worker.close()
