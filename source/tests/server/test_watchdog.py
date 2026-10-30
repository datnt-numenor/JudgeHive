"""Hạn chót từng bước (README mục PROGRESS, D16, D18). Dùng hằng số nhỏ để chạy nhanh."""

import time

import pytest

from _support import add_big_problem, add_problem
from server import problems

# cửa sổ biên dịch = 0,3 + 0,2 = 0,5 s; cửa sổ test = 0,1 s x 1 + 0,6 s = 0,7 s
FAST = dict(compile_timeout_max_s=0.3, slack_s=0.2, max_time_factor=1, per_test_overhead_s=0.6,
            ping_interval_s=30, peer_dead_after_s=60)


@pytest.fixture
def fast(make_stack):
    stack = make_stack(**FAST)
    add_problem(stack.db, "quick", tests=[problems.TestCase(False, b"1\n", b"1\n")] * 3, time_limit_ms=100)
    return stack


def test_no_progress_zero_in_the_compile_window_means_deadline_exceeded_and_redelivery(fast):
    first = fast.worker("a")
    sid = fast.submit(problem_id="quick")
    job = first.take_job()
    second = fast.worker("b")
    started = time.monotonic()
    first.expect_error("DEADLINE_EXCEEDED", timeout=3)
    assert first.closed()
    assert 0.3 < time.monotonic() - started < 2.5
    again = second.take_job()
    assert again["submission_id"] == sid and again["attempt_id"] == job["attempt_id"] + 1
    row = fast.row(sid)
    assert row["total_deliveries"] == 2 and row["delivery_count"] == 0  # chưa PROGRESS(0): chỉ tính vào trần tổng


def test_deadline_after_progress_zero_counts_as_a_confirmed_loss(fast):
    first = fast.worker("a")
    sid = fast.submit(problem_id="quick")
    job = first.take_job()
    first.progress(job, 0)
    first.expect_error("DEADLINE_EXCEEDED", timeout=3)  # cửa sổ test 0,7 s sau PROGRESS(0)
    second = fast.worker("b")
    again = second.take_job()
    assert again["submission_id"] == sid and again["attempt_id"] == job["attempt_id"] + 1
    row = fast.row(sid)
    assert row["total_deliveries"] == 2 and row["delivery_count"] == 1


def test_window_starts_when_the_send_has_finished_not_when_it_began(make_stack):
    stack = make_stack(**FAST)
    add_big_problem(stack)
    w = stack.worker(rcvbuf=4096)
    sid = stack.submit(problem_id="big")
    time.sleep(1.2)  # gấp hơn hai lần cửa sổ biên dịch, nhưng server vẫn đang gửi nên chưa tính giờ
    data = bytearray()
    w.sock.settimeout(10)
    while not data.endswith(b"\n"):
        chunk = w.sock.recv(1 << 20)
        assert chunk, "server đã đóng kết nối khi đang gửi dở"
        data += chunk
    w.send({"type": "PROGRESS", "submission_id": sid, "attempt_id": 1, "tests_done": 0, "tests_total": 1})
    time.sleep(0.2)
    assert [s["state"] for s in stack.server.snapshot()] == ["busy"]  # chưa bị coi là quá hạn


def test_ce_result_in_the_compile_window_needs_no_progress(fast):
    w = fast.worker()
    sid = fast.submit(problem_id="quick", language="cpp")
    job = w.take_job()
    w.result(job, "CE", ce_message="Main.cpp:1: error")
    row = fast.wait_row(sid, lambda r: r["state"] == "done")
    assert row["verdict"] == "CE" and row["ce_message"] == "Main.cpp:1: error"
    time.sleep(0.8)
    assert len(fast.server.snapshot()) == 1  # worker vẫn sống và rảnh


def test_each_in_order_progress_renews_the_window(fast):
    w = fast.worker()
    sid = fast.submit(problem_id="quick")
    job = w.take_job()
    for done in range(4):  # tổng thời gian dài hơn cả cửa sổ biên dịch lẫn cửa sổ một test
        w.progress(job, done)
        time.sleep(0.3)
    w.result(job)
    assert fast.wait_row(sid, lambda r: r["state"] == "done")["verdict"] == "AC"


def test_repeated_progress_does_not_extend_the_window(fast):
    w = fast.worker()
    fast.submit(problem_id="quick")
    job = w.take_job()
    w.progress(job, 0)
    started = time.monotonic()
    for _ in range(3):  # lặp lại tại 0,15 / 0,3 / 0,45 s: BAD_MESSAGE và không làm mới hạn (cửa sổ 0,7 s)
        time.sleep(0.15)
        w.progress(job, 0)
    # rồi im lặng (gửi tiếp lúc server đóng socket có thể gây RST làm mất ERROR best-effort)
    for _ in range(3):
        w.expect_error("BAD_MESSAGE")
    w.expect_error("DEADLINE_EXCEEDED", timeout=3)
    # hạn tính từ PROGRESS(0) đầu: khoảng 0,7 s. Nếu lần lặp cuối (0,45 s) làm mới hạn thì phải tới khoảng 1,15 s.
    assert time.monotonic() - started < 1.0


def test_window_after_the_last_progress_waits_for_result_and_then_expires(fast):
    w = fast.worker()
    fast.submit(problem_id="quick")
    job = w.take_job()
    for done in range(4):
        w.progress(job, done)
    started = time.monotonic()
    w.expect_error("DEADLINE_EXCEEDED", timeout=3)  # RESULT không về: D18, dùng công thức cửa sổ test
    assert 0.4 < time.monotonic() - started < 2.5


def test_progress_for_another_attempt_does_not_renew_the_window(fast):
    w = fast.worker()
    fast.submit(problem_id="quick")
    job = w.take_job()
    started = time.monotonic()
    while time.monotonic() - started < 3:
        w.send({"type": "PROGRESS", "submission_id": job["submission_id"], "attempt_id": job["attempt_id"] + 1,
                "tests_done": 0, "tests_total": 3})
        try:
            msg = w.recv(0.1)
        except TimeoutError:
            continue
        assert msg["code"] == "DEADLINE_EXCEEDED"
        break
    else:
        pytest.fail("PROGRESS không khớp đã làm mới hạn")


def test_progress_zero_arriving_before_send_returns_keeps_its_own_window(make_stack):
    """PROGRESS(0) về đúng giữa lúc sendall(JOB) xong và lúc đặt mốc watchdog: mốc của send_job không được đè cửa sổ
    của PROGRESS(0). Cửa sổ biên dịch dài (3 s), cửa sổ test ngắn (0,7 s): nếu bị đè thì DEADLINE_EXCEEDED tới trễ."""
    import threading

    from _support import wait_until

    stack = make_stack(compile_timeout_max_s=2.5, slack_s=0.5, max_time_factor=1, per_test_overhead_s=0.6,
                       ping_interval_s=30, peer_dead_after_s=60)
    add_problem(stack.db, "quick", tests=[problems.TestCase(False, b"1\n", b"1\n")] * 3, time_limit_ms=100)
    sent, release = threading.Event(), threading.Event()

    def hook(conn):
        sent.set()
        release.wait(10)

    stack.server.after_send_hook = hook
    w = stack.worker()
    sid = stack.submit(problem_id="quick")
    try:
        job = w.take_job()
        assert sent.wait(5)  # dispatcher đang dừng giữa sendall xong và đặt mốc
        started = time.monotonic()
        w.progress(job, 0)
        assert wait_until(lambda: "progress" in stack.hooks.names(sid))  # server đã xử lý PROGRESS(0)
    finally:
        release.set()
    w.expect_error("DEADLINE_EXCEEDED", timeout=4)
    assert time.monotonic() - started < 2.0
