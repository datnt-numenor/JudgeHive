"""Dispatcher (D1, D3, D6, D15, D16, D18): hàng đợi ba mức, transaction giao bài, giao lại, commit verdict."""

import threading
import time

import pytest

from _support import SUM_TESTS, add_problem, insert_submission, wait_until
from server import problems
from server.dispatcher import LEVEL_REJUDGE
from server.hooks import Hooks


def finish(stack, worker, job, verdict="AC", **fields):
    worker.result(job, verdict, **fields)
    return stack.wait_row(job["submission_id"], lambda r: r["state"] != "judging")


def test_job_message_has_exactly_the_documented_fields(stack):
    w = stack.worker()
    sid = stack.submit(language="cpp", code="int main(){}")
    job = w.take_job()
    assert set(job) == {"type", "submission_id", "attempt_id", "language", "code", "time_limit_ms",
                        "memory_limit_kib", "tests"}
    assert (job["submission_id"], job["attempt_id"], job["language"], job["code"]) == (sid, 1, "cpp", "int main(){}")
    assert (job["time_limit_ms"], job["memory_limit_kib"]) == (1000, 262144)
    assert job["tests"] == [{"input": "1 2\n", "output": "3\n", "is_sample": True},
                            {"input": "5 7\n", "output": "12\n", "is_sample": False},
                            {"input": "0 0\n", "output": "0\n", "is_sample": False}]


def test_sample_tests_are_sent_first_keeping_relative_order(stack):
    tests = [problems.TestCase(False, b"h1\n", b"1\n"), problems.TestCase(True, b"s1\n", b"2\n"),
             problems.TestCase(False, b"h2\n", b"3\n"), problems.TestCase(True, b"s2\n", b"4\n")]
    add_problem(stack.db, "mixed", tests=tests)
    w = stack.worker()
    stack.submit(problem_id="mixed")
    job = w.take_job()
    assert [(t["input"], t["is_sample"]) for t in job["tests"]] == [
        ("s1\n", True), ("s2\n", True), ("h1\n", False), ("h2\n", False)]


def test_attempt_and_hash_are_in_the_db_before_the_job_reaches_the_worker(stack):
    w = stack.worker()
    sid = stack.submit()
    job = w.take_job()
    row = stack.row(sid)  # đọc ngay khi worker nhận được JOB
    assert row["attempt_id"] == job["attempt_id"] == 1
    assert row["total_deliveries"] == 1 and row["delivery_count"] == 0 and row["state"] == "judging"
    assert row["delivered_test_hash"] == problems.compute_tests_hash(SUM_TESTS)


def test_ac_result_is_committed_with_verdict_and_hash(stack):
    w = stack.worker()
    sid = stack.submit()
    job = w.take_job()
    w.finish_ac(job)
    row = stack.wait_row(sid, lambda r: r["state"] == "done")
    assert row["verdict"] == "AC" and (row["time_ms"], row["memory_kib"]) == (5, 1024)
    assert row["verdict_test_hash"] == row["delivered_test_hash"] is not None
    assert row["failed_test"] is None and row["finished_at"] is not None
    assert wait_until(lambda: stack.hooks.dirty == 1)
    assert stack.hooks.names(sid) == ["judging", "progress", "progress", "progress", "progress", "done"]


@pytest.mark.parametrize("verdict,fields,expected", [
    ("WA", {"time_ms": 3, "memory_kib": 2048, "failed_test": 2, "failed_is_sample": False},
     {"time_ms": 3, "memory_kib": 2048, "failed_test": 2, "failed_is_sample": 0, "sample_stdout": None}),
    ("WA", {"time_ms": 3, "memory_kib": 2048, "failed_test": 1, "failed_is_sample": True, "sample_stdout": "4\n"},
     {"failed_test": 1, "failed_is_sample": 1, "sample_stdout": "4\n"}),
    ("TLE", {"time_ms": 5000, "memory_kib": 100, "failed_test": 3, "failed_is_sample": False},
     {"time_ms": 5000, "failed_test": 3}),
    ("MLE", {"time_ms": 9, "memory_kib": 5, "failed_test": 2, "failed_is_sample": False}, {"memory_kib": 5}),
    ("RE", {"failed_test": 2, "failed_is_sample": False, "re_kind": "SIGSEGV"},
     {"re_kind": "SIGSEGV", "time_ms": None, "memory_kib": None}),
    ("CE", {"ce_message": "Main.cpp:1: error: x"}, {"ce_message": "Main.cpp:1: error: x", "failed_test": None}),
])
def test_each_verdict_is_stored_with_its_fields(stack, verdict, fields, expected):
    w = stack.worker()
    sid = stack.submit()
    job = w.take_job()
    row = finish(stack, w, job, verdict, **fields)
    assert row["state"] == "done" and row["verdict"] == verdict
    for key, value in expected.items():
        assert row[key] == value, key


def test_a_worker_holds_at_most_one_job(stack):
    w = stack.worker()
    first, second = stack.submit(), stack.submit()
    job = w.take_job()
    assert job["submission_id"] == first
    with pytest.raises(TimeoutError):
        w.recv(0.5)  # chưa nhận RESULT thì chưa có JOB thứ hai
    w.finish_ac(job)
    assert w.take_job()["submission_id"] == second


def test_three_workers_take_three_jobs_in_parallel(stack):
    workers = [stack.worker(f"w{i}") for i in range(3)]
    sids = [stack.submit() for _ in range(3)]
    jobs = [w.take_job() for w in workers]
    assert sorted(j["submission_id"] for j in jobs) == sorted(sids)
    assert len({j["submission_id"] for j in jobs}) == 3


def test_jobs_wait_in_the_queue_until_a_worker_is_free(stack):
    sids = [stack.submit() for _ in range(3)]
    time.sleep(0.4)
    assert stack.dispatcher.queue.qsize() == 3  # giữ chỗ worker rồi mới get(): không worker thì không lấy bài ra
    w = stack.worker()
    assert [w.take_job()["submission_id"]] == sids[:1]


def test_priority_is_redelivery_then_new_then_rejudge(stack):
    first = stack.worker("a")
    s1 = stack.submit()
    job1 = first.take_job()
    s_rejudge = insert_submission(stack.db, stack.user_id, is_rejudge=1, verdict="WA", verdict_test_hash="old")
    stack.dispatcher.enqueue(s_rejudge, LEVEL_REJUDGE)
    s_new = stack.submit()
    first.close()  # s1 giao lại, mức cao nhất
    assert wait_until(lambda: not stack.server.snapshot())
    # Worker rời không có nghĩa là s1 đã nằm trong hàng đợi (xếp hàng giao lại là bất đồng bộ, thread dispatcher-lost);
    # hook `queued` được phát sau khi s1 vào hàng đợi, nên chờ nó trước khi có worker mới, không thì s_new có thể được
    # giao trước. Cùng cách với bản đã khóa trong tests/invariants.
    assert wait_until(lambda: "queued" in stack.hooks.names(s1))
    second = stack.worker("b")
    order = []
    for _ in range(3):
        job = second.take_job()
        order.append((job["submission_id"], job["attempt_id"]))
        second.finish_ac(job)
    assert order == [(s1, job1["attempt_id"] + 1), (s_new, 1), (s_rejudge, 1)]


def test_reserved_worker_that_dies_while_the_queue_is_empty_costs_nothing(stack):
    gone = stack.worker("gone")  # dispatcher giữ chỗ worker này rồi chờ bài
    time.sleep(0.2)
    gone.close()
    assert wait_until(lambda: not stack.server.snapshot())
    alive = stack.worker("alive")
    sid = stack.submit()
    job = alive.take_job()
    assert job["attempt_id"] == 1
    assert stack.row(sid)["total_deliveries"] == 1


def test_job_is_redelivered_with_a_new_attempt_when_the_worker_dies(stack):
    first = stack.worker("a")
    sid = stack.submit()
    job1 = first.take_job()
    second = stack.worker("b")
    first.close()
    job2 = second.take_job()
    assert job2["submission_id"] == sid and job2["attempt_id"] == job1["attempt_id"] + 1
    row = stack.row(sid)
    assert row["attempt_id"] == 2 and row["total_deliveries"] == 2 and row["delivery_count"] == 0  # chưa xác nhận
    assert "queued" in stack.hooks.names(sid)  # D1: STATUS queued khi giao lại
    second.finish_ac(job2)
    assert stack.wait_row(sid, lambda r: r["state"] == "done")["verdict"] == "AC"


def test_each_delivery_logs_submission_attempt_and_worker(stack, caplog):
    caplog.set_level("INFO", logger="judgehive.dispatcher")
    first = stack.worker("a")
    sid = stack.submit()
    first.take_job()
    second = stack.worker("b")
    first.close()
    second.take_job()
    lines = [r.getMessage() for r in caplog.records if "giao cho worker" in r.getMessage()]
    assert len(lines) == 2
    assert lines[0].startswith(f"submission {sid} attempt 1 giao cho worker #") and lines[0].endswith("('a')")
    assert lines[1].startswith(f"submission {sid} attempt 2 giao cho worker #") and lines[1].endswith("('b')")


def test_late_result_of_a_lost_attempt_is_not_applied(stack):
    first = stack.worker("a")
    sid = stack.submit()
    job1 = first.take_job()
    second = stack.worker("b")
    first.close()
    job2 = second.take_job()
    stale = stack.connect()
    stale.register("stale")
    stale.send({"type": "RESULT", "submission_id": sid, "attempt_id": job1["attempt_id"], "verdict": "WA",
                "time_ms": 1, "memory_kib": 1, "failed_test": 2, "failed_is_sample": False})
    stale.expect_error("UNEXPECTED_RESULT")
    assert stack.row(sid)["verdict"] is None
    second.finish_ac(job2)
    assert stack.wait_row(sid, lambda r: r["state"] == "done")["verdict"] == "AC"


def test_third_lost_delivery_ends_in_se_and_there_is_no_fourth_job(stack):
    sid = stack.submit()
    for number in (1, 2, 3):
        w = stack.worker(f"w{number}")
        job = w.take_job()
        assert job["attempt_id"] == number
        w.progress(job, 0)  # xác nhận nhận việc, nên lần mất này tính vào trần 3
        w.close()
        assert wait_until(lambda: not stack.server.snapshot())
    row = stack.wait_row(sid, lambda r: r["state"] == "done")
    assert row["verdict"] == "SE" and row["delivery_count"] == 3 and row["total_deliveries"] == 3
    assert row["verdict_test_hash"] == row["delivered_test_hash"]
    spare = stack.worker("spare")
    with pytest.raises(TimeoutError):
        spare.recv(0.5)
    assert "done" in stack.hooks.names(sid)


def test_se_reported_by_the_worker_is_final_and_not_redelivered(stack):
    w = stack.worker("a")
    other = stack.worker("b")
    sid = stack.submit()
    job = w.take_job()
    row = finish(stack, w, job, "SE", se_detail="boom")
    assert row["verdict"] == "SE" and row["total_deliveries"] == 1
    with pytest.raises(TimeoutError):
        other.recv(0.5)


def test_verdict_hash_is_the_hash_of_the_tests_that_were_delivered(stack):
    w = stack.worker()
    sid = stack.submit()
    job = w.take_job()
    delivered = stack.row(sid)["delivered_test_hash"]
    add_problem(stack.db, tests=[problems.TestCase(False, b"9 9\n", b"18\n")])  # admin sửa test giữa chừng
    assert stack.db.conn().execute("SELECT tests_hash FROM problems").fetchone()[0] != delivered
    w.finish_ac(job)
    row = stack.wait_row(sid, lambda r: r["state"] == "done")
    assert row["verdict_test_hash"] == delivered


def test_delivery_clears_rejudge_requested(stack):
    w = stack.worker()
    sid = stack.submit(rejudge_requested=1)
    w.take_job()
    row = stack.row(sid)
    assert row["rejudge_requested"] == 0 and row["state"] == "judging"


def test_rejudge_overwrites_verdict_hash_and_sets_rejudged_at(stack):
    w = stack.worker()
    sid = insert_submission(stack.db, stack.user_id, is_rejudge=1, verdict="WA", verdict_test_hash="old",
                            failed_test=2, failed_is_sample=0)
    stack.dispatcher.enqueue(sid, LEVEL_REJUDGE)
    job = w.take_job()
    assert "queued" not in stack.hooks.names(sid)  # rejudge không đẩy STATUS queued
    w.finish_ac(job)
    row = stack.wait_row(sid, lambda r: r["state"] == "done")
    assert row["verdict"] == "AC" and row["failed_test"] is None
    assert row["verdict_test_hash"] == row["delivered_test_hash"] != "old"
    assert row["rejudged_at"] is not None and row["is_rejudge"] == 0 and row["rejudge_failed"] == 0


def test_se_during_rejudge_keeps_the_old_verdict_and_old_hash(stack):
    w = stack.worker()
    sid = insert_submission(stack.db, stack.user_id, is_rejudge=1, verdict="AC", verdict_test_hash="old",
                            time_ms=7, memory_kib=8)
    stack.dispatcher.enqueue(sid, LEVEL_REJUDGE)
    job = w.take_job()
    w.result(job, "SE", se_detail="x")
    row = stack.wait_row(sid, lambda r: r["state"] == "done")
    assert (row["verdict"], row["verdict_test_hash"], row["time_ms"]) == ("AC", "old", 7)
    assert row["rejudge_failed"] == 1 and row["is_rejudge"] == 0 and row["rejudged_at"] is None


def test_rejudge_lost_three_times_keeps_the_old_verdict(stack):
    sid = insert_submission(stack.db, stack.user_id, is_rejudge=1, verdict="AC", verdict_test_hash="old")
    stack.dispatcher.enqueue(sid, LEVEL_REJUDGE)
    for number in (1, 2, 3):
        w = stack.worker(f"w{number}")
        job = w.take_job()
        assert "queued" not in stack.hooks.names(sid)
        w.progress(job, 0)  # xác nhận nhận việc, nên lần mất này tính vào trần 3
        w.close()
        assert wait_until(lambda: not stack.server.snapshot())
    row = stack.wait_row(sid, lambda r: r["state"] == "done")
    assert row["verdict"] == "AC" and row["verdict_test_hash"] == "old" and row["rejudge_failed"] == 1


def test_rejudge_requested_while_judging_is_queued_again_after_the_result(stack):
    w = stack.worker()
    sid = stack.submit()
    job = w.take_job()
    with stack.db.write() as c:  # admin bấm rejudge khi bài đang chấm (PR 6 sẽ làm việc này)
        c.execute("UPDATE submissions SET rejudge_requested = 1 WHERE id = ?", (sid,))
    w.finish_ac(job)
    job2 = w.take_job()  # kết quả đầu đã commit, bài tự vào hàng đợi rejudge
    assert job2["submission_id"] == sid and job2["attempt_id"] == 2
    mid = stack.row(sid)
    assert mid["verdict"] == "AC" and mid["is_rejudge"] == 1 and mid["rejudge_requested"] == 0
    w.finish_ac(job2)
    row = stack.wait_row(sid, lambda r: r["state"] == "done")
    assert row["rejudged_at"] is not None and row["is_rejudge"] == 0


def test_job_line_over_the_limit_becomes_se_and_is_never_sent(stack):
    huge = [problems.TestCase(False, b"a" * 8_600_000, b"b\n"), problems.TestCase(False, b"c" * 8_600_000, b"d\n")]
    add_problem(stack.db, "huge", tests=huge)  # đi vòng loader, như dữ liệu hỏng trong DB
    w = stack.worker()
    sid = stack.submit(problem_id="huge")
    row = stack.wait_row(sid, lambda r: r["state"] == "done")
    assert row["verdict"] == "SE" and row["attempt_id"] == 0 and row["delivery_count"] == 0 \
        and row["total_deliveries"] == 0
    with pytest.raises(TimeoutError):
        w.recv(0.5)
    stack.submit()  # worker vẫn rảnh và nhận bài kế tiếp
    assert w.take_job()["type"] == "JOB"


class ExplodingHooks(Hooks):
    def status(self, *args, **kwargs):
        raise RuntimeError("hook hỏng")

    def scoreboard_dirty(self):
        raise RuntimeError("hook hỏng")


def test_a_failing_hook_does_not_stop_dispatching(stack):
    stack.server.hooks = stack.dispatcher.hooks = ExplodingHooks()
    w = stack.worker()
    first, second = stack.submit(), stack.submit()
    job = w.take_job()
    w.finish_ac(job)
    assert stack.wait_row(first, lambda r: r["state"] == "done")["verdict"] == "AC"
    assert w.take_job()["submission_id"] == second


def test_queue_survives_worker_churn(stack):
    sids = [stack.submit() for _ in range(6)]
    done = []
    lock = threading.Lock()

    def run(name):
        w = stack.worker(name)
        while True:
            try:
                job = w.take_job(timeout=1.5)
            except (TimeoutError, EOFError):
                return
            w.finish_ac(job)
            with lock:
                done.append(job["submission_id"])

    threads = [threading.Thread(target=run, args=(f"w{i}",)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(15)
    assert sorted(done) == sorted(sids)
    assert all(stack.row(s)["verdict"] == "AC" and stack.row(s)["total_deliveries"] == 1 for s in sids)


def test_a_rejudge_that_starts_after_three_deliveries_gets_three_new_ones(stack):
    """README 2.6, D1, D6: mỗi lần rejudge đếm lại số lần giao từ đầu. Bài đã dùng đủ 3 lần giao khi AC mà được yêu cầu
    rejudge lúc đang chấm không được thành SE ngay."""
    sid = stack.submit()
    for number in (1, 2):
        w = stack.worker(f"lost{number}")
        assert w.take_job()["attempt_id"] == number
        w.close()
        assert wait_until(lambda: not stack.server.snapshot())
    w = stack.worker("third")
    job = w.take_job()
    assert job["attempt_id"] == 3 and stack.row(sid)["total_deliveries"] == 3
    with stack.db.write() as c:
        c.execute("UPDATE submissions SET rejudge_requested = 1 WHERE id = ?", (sid,))
    w.finish_ac(job)
    job2 = w.take_job()  # bị SE ngay (không gửi JOB) nếu bộ đếm không được đặt lại
    assert job2["submission_id"] == sid and job2["attempt_id"] == 4
    w.finish_ac(job2)
    row = stack.wait_row(sid, lambda r: r["state"] == "done")
    assert row["verdict"] == "AC" and row["rejudge_failed"] == 0 and row["rejudged_at"] is not None


def test_rejudge_resets_both_delivery_counters(stack):
    """D22: mỗi lần rejudge đếm lại cả hai giới hạn: lần giao đầu của rejudge ra total_deliveries = 1, delivery_count = 0."""
    sid = stack.submit()
    w = stack.worker("lost")
    job = w.take_job()
    w.progress(job, 0)
    w.close()  # lần mất đã xác nhận: delivery_count = 1
    assert wait_until(lambda: stack.row(sid)["delivery_count"] == 1)
    w = stack.worker("second")
    job = w.take_job()
    assert stack.row(sid)["total_deliveries"] == 2
    with stack.db.write() as c:
        c.execute("UPDATE submissions SET rejudge_requested = 1 WHERE id = ?", (sid,))
    w.finish_ac(job)
    job2 = w.take_job()
    row = stack.row(sid)
    assert job2["submission_id"] == sid and row["delivery_count"] == 0 and row["total_deliveries"] == 1
