"""Khởi động lại server (D1, D6, D18, D22): nạp lại hàng đợi từ DB, hoàn lại lần giao tổng của bài đang chấm dở."""

from _support import Stack, add_problem, insert_submission
from server.dispatcher import LEVEL_NEW, LEVEL_REJUDGE
from server.problems import TestCase


def make_idle_stack(tmp_path):
    """Stack chưa start: DB có sẵn, hàng đợi trống, như server vừa khởi động."""
    return Stack(tmp_path)


def test_load_pending_queues_unfinished_submissions_at_their_original_level(tmp_path):
    stack = make_idle_stack(tmp_path)
    new = insert_submission(stack.db, stack.user_id)
    rejudge = insert_submission(stack.db, stack.user_id, is_rejudge=1, verdict="AC")
    judging = insert_submission(stack.db, stack.user_id, state="judging", attempt_id=2, total_deliveries=2)
    done = insert_submission(stack.db, stack.user_id, state="done", verdict="AC")
    assert stack.dispatcher.load_pending() == 3
    assert sorted(stack.dispatcher.queue.queue) == [(LEVEL_NEW, new), (LEVEL_NEW, judging), (LEVEL_REJUDGE, rejudge)]
    assert stack.row(done)["state"] == "done"


def test_a_job_that_was_judging_gets_one_total_delivery_back_and_keeps_its_attempt_id(tmp_path):
    stack = make_idle_stack(tmp_path)
    judging = insert_submission(stack.db, stack.user_id, state="judging", attempt_id=2, delivery_count=1,
                                total_deliveries=2)
    never_delivered = insert_submission(stack.db, stack.user_id, state="judging", attempt_id=0)
    stack.dispatcher.load_pending()
    row = stack.row(judging)
    assert (row["state"], row["total_deliveries"], row["attempt_id"]) == ("queued", 1, 2)
    assert row["delivery_count"] == 1  # lần mất đã xác nhận trước đó không được hoàn
    assert stack.row(never_delivered)["total_deliveries"] == 0  # không xuống âm


def test_queued_submissions_are_delivered_in_order_after_a_restart(tmp_path):
    stack = make_idle_stack(tmp_path)
    first = insert_submission(stack.db, stack.user_id)
    rejudge = insert_submission(stack.db, stack.user_id, is_rejudge=1, verdict="WA", verdict_test_hash="old")
    resumed = insert_submission(stack.db, stack.user_id, state="judging", attempt_id=1, total_deliveries=1)
    stack.dispatcher.load_pending()
    stack.start()
    try:
        w = stack.worker()
        order = []
        for _ in range(3):
            job = w.take_job()
            order.append((job["submission_id"], job["attempt_id"]))
            w.finish_ac(job)
        assert order == [(first, 1), (resumed, 2), (rejudge, 1)]  # attempt_id tiếp tục tăng, không về 0
        assert stack.row(resumed)["total_deliveries"] == 1  # lần giao dở trước restart không bị tính
    finally:
        stack.stop()


def test_a_restart_in_the_middle_of_a_job_costs_no_delivery(tmp_path):
    first = Stack(tmp_path).start()
    sid = first.submit()
    w = first.worker()
    job = w.take_job()
    assert first.row(sid)["total_deliveries"] == 1
    first.stop()  # server tắt khi worker đang chấm

    second = Stack(tmp_path)  # cùng file DB, hàng đợi trong RAM trống như server mới
    second.dispatcher.load_pending()
    second.start()
    try:
        w2 = second.worker()
        job2 = w2.take_job()
        assert job2["submission_id"] == sid and job2["attempt_id"] == job["attempt_id"] + 1
        assert second.row(sid)["total_deliveries"] == 1 and second.row(sid)["delivery_count"] == 0
    finally:
        second.stop()


def test_pending_problem_edits_between_restarts_are_used_for_the_next_delivery(tmp_path):
    stack = make_idle_stack(tmp_path)
    sid = insert_submission(stack.db, stack.user_id)
    add_problem(stack.db, tests=[TestCase(False, b"2 2\n", b"4\n")])
    stack.dispatcher.load_pending()
    stack.start()
    try:
        job = stack.worker().take_job()
        assert job["submission_id"] == sid and job["tests"] == [{"input": "2 2\n", "output": "4\n", "is_sample": False}]
    finally:
        stack.stop()
