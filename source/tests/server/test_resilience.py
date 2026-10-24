"""Chịu lỗi tạm thời (kết quả review PR 4): accept lỗi, DB lỗi lúc ghi kết quả hay giao lại, ghi DB không chặn giám sát."""

import errno
import sqlite3
import time

from _support import Stack, wait_until
from server.worker_server import WorkerServer


class FlakyAcceptServer(WorkerServer):
    """accept() ném OSError tạm thời vài lần trước khi hoạt động lại (EMFILE, ECONNABORTED...)."""

    failures = 3

    def _accept(self):
        if self.failures > 0:
            self.failures -= 1
            raise OSError(errno.EMFILE, "Too many open files")
        return super()._accept()


def test_accept_loop_survives_transient_errors(tmp_path):
    stack = Stack(tmp_path)
    stack.server = FlakyAcceptServer(stack.cfg, stack.db, stack.hooks)
    stack.dispatcher.server = stack.server
    stack.server.set_handler(stack.dispatcher)
    stack.start()
    try:
        w = stack.connect()
        w.register()  # sau ba lỗi accept, worker vẫn được nhận
        assert stack.server.failures == 0
        stack.submit()
        w.take_job()
    finally:
        stack.stop()


def test_listener_closed_by_stop_still_ends_the_accept_thread(stack):
    accept_thread = next(t for t in stack.server._threads if t.name == "worker-accept")
    stack.server.stop()
    accept_thread.join(timeout=3)
    assert not accept_thread.is_alive()


def test_job_lost_is_retried_until_the_db_accepts_it(stack):
    original = stack.dispatcher._job_lost
    calls = []

    def flaky(job, reason):
        calls.append(job.attempt_id)
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked")
        return original(job, reason)

    stack.dispatcher._job_lost = flaky
    first = stack.worker("a")
    sid = stack.submit()
    job = first.take_job()
    second = stack.worker("b")
    first.close()
    again = second.take_job(timeout=10)  # lần ghi đầu lỗi, lần thử lại thành công
    assert again["submission_id"] == sid and again["attempt_id"] == job["attempt_id"] + 1
    assert len(calls) == 2


def test_result_commit_failure_sends_the_job_back_to_the_queue(stack):
    original = stack.dispatcher.handle_result
    calls = []

    def flaky(job, body):
        calls.append(job.attempt_id)
        if len(calls) == 1:
            raise sqlite3.OperationalError("disk I/O error")
        return original(job, body)

    stack.dispatcher.handle_result = flaky
    w = stack.worker()
    sid = stack.submit()
    job = w.take_job()
    w.finish_ac(job)
    again = w.take_job(timeout=10)  # không bị kẹt ở judging: giao lại với attempt_id mới
    assert again["submission_id"] == sid and again["attempt_id"] == 2
    w.finish_ac(again)
    row = stack.wait_row(sid, lambda r: r["state"] == "done")
    assert row["verdict"] == "AC" and row["delivery_count"] == 2


def test_failure_after_the_delivery_transaction_requeues_the_job(stack):
    original = stack.server.send_job
    calls = []

    def flaky(conn, data):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("lỗi bất ngờ sau khi ghi DB")
        return original(conn, data)

    stack.server.send_job = flaky
    first = stack.worker("a")
    sid = stack.submit()
    second = stack.worker("b")
    job = second.take_job(timeout=10)
    assert job["submission_id"] == sid and job["attempt_id"] == 2  # lần 1 đã tính, rồi giao lại
    assert first.closed()  # worker bị giữ chỗ nhưng chưa nhận được JOB bị đóng, không treo ở busy
    assert stack.row(sid)["delivery_count"] == 2


def test_slow_db_write_for_a_lost_job_does_not_stall_pings_to_other_workers(make_stack):
    stack = make_stack(ping_interval_s=0.1, peer_dead_after_s=0.5)
    original = stack.dispatcher._job_lost

    def slow(job, reason):
        time.sleep(1.5)  # DB bận
        return original(job, reason)

    stack.dispatcher._job_lost = slow
    silent = stack.worker("silent")
    stack.submit()
    silent.take_job()  # rồi im lặng: thread giám sát sẽ đánh dấu chết sau 0,5 giây
    healthy = stack.worker("healthy")
    pings = 0
    end = time.monotonic() + 1.6
    while time.monotonic() < end:
        healthy.send({"type": "PONG", "seq": 0})
        try:
            if healthy.recv(0.1, skip_ping=False)["type"] == "PING":
                pings += 1
        except TimeoutError:
            pass
    assert pings >= 11  # nếu thread giám sát phải chờ ghi DB 1,5 giây thì chỉ còn khoảng 5
    assert wait_until(lambda: len(stack.server.snapshot()) == 1)


def test_failure_before_the_job_is_attached_to_a_worker_still_requeues_it(stack):
    original = stack.server.assign_job
    calls = []

    def flaky(conn, job):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("lỗi bất ngờ trước khi gắn job vào worker")
        return original(conn, job)

    stack.server.assign_job = flaky
    first = stack.worker("a")
    sid = stack.submit()
    second = stack.worker("b")
    job = second.take_job(timeout=10)
    assert job["submission_id"] == sid and job["attempt_id"] == 2
    assert first.closed()
