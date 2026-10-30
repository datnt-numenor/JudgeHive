"""Cách tính lần giao (README 2.6, D22): chỉ lần mất đã xác nhận mới tính vào trần 3; trần tổng là 6 lần giao.

Mỗi lần mất, test chờ hook `queued` (phát sau khi bài đã vào lại hàng đợi) rồi mới đăng ký worker kế, để worker kế
không nhận JOB trước khi server kịp ghi lần mất."""

import pytest

from _server_contract_support import Stack, make_stack, stack, wait_until  # noqa: F401 - fixture


def queued_count(stack, sid):
    return stack.hooks.names(sid).count("queued")


def lose(stack, sid, attempt, confirm=True, send=None):
    """Một worker mới nhận JOB `attempt`, rồi mất. `confirm`: gửi PROGRESS 0 hợp lệ trước khi mất; `send(worker, job)`
    thay cho việc đó khi cần gửi message khác. Trả khi bài đã vào lại hàng đợi (hoặc đã kết thúc)."""
    before = queued_count(stack, sid)
    w = stack.worker(f"w{attempt}")
    job = w.take_job()
    assert (job["submission_id"], job["attempt_id"]) == (sid, attempt)
    try:
        if send is not None:
            send(w, job)
        elif confirm:
            w.progress(job, 0)
    except OSError:
        pass  # server đã đóng kết nối vì message sai: đúng như kỳ vọng của vài ca
    w.close()
    assert wait_until(lambda: queued_count(stack, sid) > before or stack.view(sid)["state"] == "done")


def assert_no_more_job(stack):
    spare = stack.worker("spare")
    with pytest.raises(TimeoutError):
        spare.recv(0.5)


def test_losses_before_progress_0_do_not_count(stack):
    sid = stack.submit()
    for attempt in range(1, 6):
        lose(stack, sid, attempt, confirm=False)
    w = stack.worker("last")
    job = w.take_job()
    assert job["attempt_id"] == 6
    w.finish_ac(job)
    assert stack.wait_view(sid, lambda v: v["state"] == "done")["verdict"] == "AC"


def test_unconfirmed_losses_stop_at_cap_6(stack):
    sid = stack.submit()
    for attempt in range(1, 7):
        lose(stack, sid, attempt, confirm=False)
    view = stack.wait_view(sid, lambda v: v["state"] == "done")
    assert view["verdict"] == "SE" and view["attempt_id"] == 6
    assert_no_more_job(stack)


def test_confirmed_cap_3_with_mixed_losses(stack):
    sid = stack.submit()
    for attempt, confirm in enumerate((False, True, False, True, True), start=1):
        lose(stack, sid, attempt, confirm=confirm)
    view = stack.wait_view(sid, lambda v: v["state"] == "done")
    assert view["verdict"] == "SE" and view["attempt_id"] == 5
    assert_no_more_job(stack)  # không có attempt 6


def test_restart_does_not_use_up_the_total_cap(tmp_path):
    before = Stack(tmp_path).start()
    sid = before.submit()
    w = before.worker()
    assert w.take_job()["attempt_id"] == 1  # attempt 1 dở khi server tắt
    before.stop()

    after = Stack(tmp_path, user_id=before.user_id)
    after.dispatcher.load_pending()
    after.start()
    try:
        for attempt in range(2, 7):  # 5 lần mất chưa xác nhận đầu, bài vẫn được giao tiếp
            lose(after, sid, attempt, confirm=False)
        assert after.view(sid)["state"] != "done"
        w = after.worker("w7")
        assert w.take_job()["attempt_id"] == 7  # attempt 7 được giao rồi mới SE
        w.close()
        view = after.wait_view(sid, lambda v: v["state"] == "done")
        assert view["verdict"] == "SE"
        assert_no_more_job(after)
    finally:
        after.stop()


def send_first_progress_1(w, job):
    w.progress(job, 1)


def send_progress_0_wrong_total(w, job):
    w.send({"type": "PROGRESS", "submission_id": job["submission_id"], "attempt_id": job["attempt_id"],
            "tests_done": 0, "tests_total": len(job["tests"]) + 1})


def send_progress_0_other_attempt(w, job):
    w.send({"type": "PROGRESS", "submission_id": job["submission_id"], "attempt_id": job["attempt_id"] + 1,
            "tests_done": 0, "tests_total": len(job["tests"])})


@pytest.mark.parametrize("send", [send_first_progress_1, send_progress_0_wrong_total, send_progress_0_other_attempt],
                         ids=["first_progress_1", "progress_0_with_wrong_total", "progress_0_of_another_attempt"])
def test_message_that_is_not_a_valid_progress_0_does_not_confirm(stack, send):
    sid = stack.submit()
    for attempt in range(1, 6):
        lose(stack, sid, attempt, send=send)
    assert stack.view(sid)["state"] != "done"  # nếu có xác nhận thì đã SE ở lần mất thứ 3
    w = stack.worker("sixth")
    assert w.take_job()["attempt_id"] == 6


def test_matched_result_with_bad_semantics_confirms(stack):
    def bad_semantics(w, job):  # khớp JOB, đúng kiểu và khoảng, nhưng failed_test vượt số test
        w.result(job, "WA", time_ms=3, memory_kib=2048, failed_test=len(job["tests"]) + 1, failed_is_sample=False)

    sid = stack.submit()
    for attempt in (1, 2, 3):
        lose(stack, sid, attempt, send=bad_semantics)
    view = stack.wait_view(sid, lambda v: v["state"] == "done")
    assert view["verdict"] == "SE" and view["attempt_id"] == 3
    assert_no_more_job(stack)


def test_result_that_fails_the_type_or_range_check_does_not_confirm(stack):
    def bad_range(w, job):  # đối chứng: time_ms âm bị loại trước khi khớp JOB nên không xác nhận
        w.result(job, "AC", time_ms=-1)

    sid = stack.submit()
    for attempt in (1, 2, 3):
        lose(stack, sid, attempt, send=bad_range)
    assert stack.view(sid)["state"] != "done"
    w = stack.worker("fourth")
    assert w.take_job()["attempt_id"] == 4
