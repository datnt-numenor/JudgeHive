"""Giao bài và ghi kết quả (README 2.5, 2.6, D1, D3, D15, D16, D18): nội dung JOB, hàng đợi ba mức, giao lại, verdict."""

import threading
import time

import pytest

from _server_contract_support import SUM_TESTS, Stack, make_stack, stack, wait_until  # noqa: F401 - fixture
from server import problems
from server.dispatcher import LEVEL_REJUDGE
from server.hooks import Hooks


def finish(stack, worker, job, verdict="AC", **fields):
    worker.result(job, verdict, **fields)
    return stack.wait_view(job["submission_id"], lambda v: v["state"] != "judging")


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
    stack.add_problem("mixed", [problems.TestCase(False, b"h1\n", b"1\n"), problems.TestCase(True, b"s1\n", b"2\n"),
                                problems.TestCase(False, b"h2\n", b"3\n"), problems.TestCase(True, b"s2\n", b"4\n")])
    w = stack.worker()
    stack.submit(problem_id="mixed")
    job = w.take_job()
    assert [(t["input"], t["is_sample"]) for t in job["tests"]] == [
        ("s1\n", True), ("s2\n", True), ("h1\n", False), ("h2\n", False)]


def test_attempt_and_test_hash_are_stored_before_the_job_reaches_the_worker(stack):
    w = stack.worker()
    sid = stack.submit()
    job = w.take_job()
    view = stack.view(sid)  # đọc ngay khi worker nhận được JOB
    assert view["attempt_id"] == job["attempt_id"] == 1 and view["state"] == "judging"
    assert view["delivered_test_hash"] == problems.compute_tests_hash(SUM_TESTS)


def test_an_ac_result_is_committed_with_the_verdict_and_the_hash_of_the_delivered_tests(stack):
    w = stack.worker()
    sid = stack.submit()
    job = w.take_job()
    w.finish_ac(job)
    view = stack.wait_view(sid, lambda v: v["state"] == "done")
    assert view["verdict"] == "AC" and (view["time_ms"], view["memory_kib"]) == (5, 1024)
    assert view["verdict_test_hash"] == view["delivered_test_hash"] is not None
    assert view["failed_test"] is None
    assert wait_until(lambda: stack.hooks.dirty == 1)
    names = stack.hooks.names(sid)
    assert names[0] == "judging" and names[-1] == "done" and "progress" in names
    assert names.count("judging") == 1 and names.count("done") == 1  # mỗi sự kiện đầu/cuối đúng một lần


@pytest.mark.parametrize("verdict,fields,expected", [
    ("WA", {"time_ms": 3, "memory_kib": 2048, "failed_test": 2, "failed_is_sample": False},
     {"time_ms": 3, "memory_kib": 2048, "failed_test": 2, "failed_is_sample": False, "sample_stdout": None}),
    ("WA", {"time_ms": 3, "memory_kib": 2048, "failed_test": 1, "failed_is_sample": True, "sample_stdout": "4\n"},
     {"failed_test": 1, "failed_is_sample": True, "sample_stdout": "4\n"}),
    ("TLE", {"time_ms": 5000, "memory_kib": 100, "failed_test": 3, "failed_is_sample": False},
     {"time_ms": 5000, "failed_test": 3}),
    ("MLE", {"time_ms": 9, "memory_kib": 5, "failed_test": 2, "failed_is_sample": False}, {"memory_kib": 5}),
    ("RE", {"failed_test": 2, "failed_is_sample": False, "re_kind": "SIGSEGV"},
     {"re_kind": "SIGSEGV", "time_ms": None, "memory_kib": None}),
    ("CE", {"ce_message": "Main.cpp:1: error: x"}, {"ce_message": "Main.cpp:1: error: x", "failed_test": None}),
])
def test_each_verdict_is_stored_with_its_fields(stack, verdict, fields, expected):
    w = stack.worker()
    stack.submit()
    job = w.take_job()
    view = finish(stack, w, job, verdict, **fields)
    assert view["state"] == "done" and view["verdict"] == verdict
    for key, value in expected.items():
        assert view[key] == value, key


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


def test_jobs_wait_until_a_worker_registers_and_are_taken_in_submission_order(stack):
    sids = [stack.submit() for _ in range(3)]
    assert wait_until(lambda: False, timeout=0.4) is False  # chưa có worker nào
    w = stack.worker()
    taken = []
    for _ in range(3):
        job = w.take_job()
        taken.append(job["submission_id"])
        w.finish_ac(job)
    assert taken == sids


def test_priority_is_redelivery_then_new_then_rejudge(stack):
    first = stack.worker("a")
    s1 = stack.submit()
    job1 = first.take_job()
    s_rejudge = stack.submit(level=LEVEL_REJUDGE)
    s_new = stack.submit()
    first.close()  # s1 giao lại, mức cao nhất
    assert wait_until(lambda: stack.registered() == 0)
    # worker rời không có nghĩa là s1 đã nằm trong hàng đợi (xếp hàng giao lại là bất đồng bộ); hook `queued` được phát
    # sau khi s1 đã vào hàng đợi, nên chờ nó trước khi có worker mới, không thì s_new có thể được giao trước
    assert wait_until(lambda: "queued" in stack.hooks.names(s1))
    second = stack.worker("b")
    order = []
    for _ in range(3):
        job = second.take_job()
        order.append((job["submission_id"], job["attempt_id"]))
        second.finish_ac(job)
    assert order == [(s1, job1["attempt_id"] + 1), (s_new, 1), (s_rejudge, 1)]


def test_a_reserved_worker_that_dies_while_the_queue_is_empty_costs_nothing(stack):
    gone = stack.worker("gone")
    gone.close()
    assert wait_until(lambda: stack.registered() == 0)
    alive = stack.worker("alive")
    stack.submit()
    assert alive.take_job()["attempt_id"] == 1


def test_a_job_is_redelivered_with_a_new_attempt_when_the_worker_dies(stack):
    first = stack.worker("a")
    sid = stack.submit()
    job1 = first.take_job()
    second = stack.worker("b")
    first.close()
    job2 = second.take_job()
    assert job2["submission_id"] == sid and job2["attempt_id"] == job1["attempt_id"] + 1
    assert stack.view(sid)["attempt_id"] == 2
    assert "queued" in stack.hooks.names(sid)  # D1: STATUS queued khi giao lại
    second.finish_ac(job2)
    assert stack.wait_view(sid, lambda v: v["state"] == "done")["verdict"] == "AC"


def test_a_late_result_of_a_lost_attempt_is_not_applied(stack):
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
    assert stack.view(sid)["verdict"] is None
    second.finish_ac(job2)
    assert stack.wait_view(sid, lambda v: v["state"] == "done")["verdict"] == "AC"


def test_the_third_lost_delivery_ends_in_se_and_there_is_no_fourth_job(stack):
    sid = stack.submit()
    for number in (1, 2, 3):
        w = stack.worker(f"w{number}")
        job = w.take_job()
        assert job["attempt_id"] == number
        w.progress(job, 0)
        w.close()
        assert wait_until(lambda: stack.registered() == 0)
    view = stack.wait_view(sid, lambda v: v["state"] == "done")
    assert view["verdict"] == "SE" and view["verdict_test_hash"] == view["delivered_test_hash"]
    spare = stack.worker("spare")
    with pytest.raises(TimeoutError):
        spare.recv(0.5)
    assert "done" in stack.hooks.names(sid)


def test_se_reported_by_the_worker_is_final_and_not_redelivered(stack):
    w = stack.worker("a")
    other = stack.worker("b")
    stack.submit()
    job = w.take_job()
    assert finish(stack, w, job, "SE", se_detail="boom")["verdict"] == "SE"
    with pytest.raises(TimeoutError):
        other.recv(0.5)


def test_the_verdict_hash_is_the_hash_of_the_tests_that_were_delivered(stack):
    w = stack.worker()
    sid = stack.submit()
    job = w.take_job()
    delivered = stack.view(sid)["delivered_test_hash"]
    edited = [problems.TestCase(False, b"9 9\n", b"18\n")]
    stack.add_problem("sum", edited)  # admin sửa test giữa chừng
    assert problems.compute_tests_hash(edited) != delivered
    w.finish_ac(job)
    assert stack.wait_view(sid, lambda v: v["state"] == "done")["verdict_test_hash"] == delivered


class ExplodingHooks(Hooks):
    def status(self, *args, **kwargs):
        raise RuntimeError("hook hỏng")

    def scoreboard_dirty(self):
        raise RuntimeError("hook hỏng")


def test_a_failing_hook_does_not_stop_dispatching(make_stack):
    stack = make_stack(hooks=ExplodingHooks())
    w = stack.worker()
    first, second = stack.submit(), stack.submit()
    job = w.take_job()
    w.finish_ac(job)
    assert stack.wait_view(first, lambda v: v["state"] == "done")["verdict"] == "AC"
    assert w.take_job()["submission_id"] == second


def test_the_queue_is_drained_exactly_once_by_concurrent_workers(stack):
    sids = [stack.submit() for _ in range(6)]
    assert len(set(sids)) == 6
    done = []
    lock = threading.Lock()
    started = time.monotonic()
    deadline = started + 15

    def finished():
        with lock:
            return len(done) >= len(sids)

    def run(name):
        w = stack.worker(name)
        while not finished() and time.monotonic() < deadline:  # chỉ thoát khi đã đủ bài (hoặc quá hạn 15 s)
            try:
                job = w.take_job(timeout=0.5)
            except TimeoutError:
                continue
            except EOFError:
                return
            w.finish_ac(job)
            with lock:
                done.append(job["submission_id"])

    threads = [threading.Thread(target=run, args=(f"w{i}",)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert time.monotonic() - started <= 15, "quá 15 s"
    # đúng 6 bài phân biệt, mỗi bài được một worker nhận và hoàn tất đúng một lần (không thiếu, không trùng)
    assert len(done) == 6 and len(set(done)) == 6 and set(done) == set(sids), done
    views = {s: stack.wait_view(s, lambda v: v["state"] == "done") for s in sids}
    assert all(v["verdict"] == "AC" and v["attempt_id"] == 1 for v in views.values())
    # phía server cũng kết thúc mỗi bài đúng một lần: đủ 6 sự kiện done, không thêm sau khi chờ một lúc
    assert wait_until(lambda: stack.hooks.dirty == 6)
    time.sleep(0.3)
    assert stack.hooks.dirty == 6
    for s in sids:
        names = stack.hooks.names(s)
        assert names[0] == "judging" and names[-1] == "done", names
        assert names.count("judging") == 1 and names.count("done") == 1, names


def test_a_server_restart_in_the_middle_of_a_job_does_not_use_up_one_of_the_three_deliveries(tmp_path):
    """D1, D18: lần giao dở lúc server tắt được hoàn lại. Có hoàn lại thì sau restart bài còn nhận đủ attempt 2, 3 và 4
    (3 lần giao, mỗi lần worker mất) trước khi thành SE; không hoàn lại thì SE ngay sau attempt 3."""
    before = Stack(tmp_path).start()
    sid = before.submit()
    w = before.worker()
    assert w.take_job()["attempt_id"] == 1
    before.stop()  # server tắt khi worker đang chấm

    after = Stack(tmp_path, user_id=before.user_id)  # cùng file DB, hàng đợi trong RAM trống như server mới
    after.dispatcher.load_pending()
    after.start()
    try:
        for attempt in (2, 3, 4):
            worker = after.worker(f"w{attempt}")
            job = worker.take_job()
            assert job["attempt_id"] == attempt
            worker.progress(job, 0)
            worker.close()
            assert wait_until(lambda: after.registered() == 0)
        view = after.wait_view(sid, lambda v: v["state"] == "done")
        assert view["verdict"] == "SE"
    finally:
        after.stop()
