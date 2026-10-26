"""Giữ kết nối (README 2.6, D1): PING/PONG, phát hiện worker chết, gửi dở không bị tính là im lặng."""

import time

import pytest

from _support import add_big_problem, wait_until


def test_ping_has_increasing_seq_at_the_ping_interval(make_stack):
    stack = make_stack(ping_interval_s=0.1, peer_dead_after_s=30)
    w = stack.worker()
    seqs = []
    started = time.monotonic()
    while len(seqs) < 4:
        msg = w.recv(2, skip_ping=False)
        assert msg["type"] == "PING"
        seqs.append(msg["seq"])
    assert seqs == [1, 2, 3, 4]
    assert time.monotonic() - started < 2


def test_silent_worker_is_declared_dead_and_its_job_goes_to_another(make_stack):
    stack = make_stack(ping_interval_s=0.1, peer_dead_after_s=0.6)
    silent = stack.worker("silent")
    sid = stack.submit()
    job = silent.take_job()
    other = stack.worker("other")
    other_pong = {"type": "PONG", "seq": 0}
    deadline = time.monotonic() + 5
    again = None
    while again is None and time.monotonic() < deadline:
        other.send(other_pong)  # "other" còn sống vì liên tục có message
        try:
            msg = other.recv(0.1)
        except TimeoutError:
            continue
        again = msg
    assert again is not None and again["type"] == "JOB"
    assert again["submission_id"] == sid and again["attempt_id"] == job["attempt_id"] + 1
    assert silent.closed()


def test_any_message_counts_as_alive(make_stack):
    stack = make_stack(ping_interval_s=0.1, peer_dead_after_s=0.6)
    w = stack.worker()
    end = time.monotonic() + 1.8  # gấp ba lần peer_dead_after
    while time.monotonic() < end:
        w.send({"type": "PONG", "seq": 1})
        time.sleep(0.2)
    assert len(stack.server.snapshot()) == 1
    stack.submit()
    w.take_job()


def test_idle_silent_worker_is_removed(make_stack):
    stack = make_stack(ping_interval_s=0.1, peer_dead_after_s=0.4)
    w = stack.worker()
    assert wait_until(lambda: not stack.server.snapshot(), timeout=3)
    assert w.closed()


def test_slow_receiver_of_a_big_job_is_not_declared_silent(make_stack):
    stack = make_stack(ping_interval_s=0.1, peer_dead_after_s=0.4, send_timeout_s=30)
    add_big_problem(stack)
    w = stack.worker(rcvbuf=4096)
    sid = stack.submit(problem_id="big")
    time.sleep(1.5)  # gấp gần bốn lần peer_dead_after mà worker chưa đọc byte nào
    assert [s["state"] for s in stack.server.snapshot()] == ["busy"]
    data = bytearray()
    w.sock.settimeout(10)
    while not data.endswith(b"\n"):
        chunk = w.sock.recv(1 << 20)
        assert chunk, "server đã đóng kết nối khi đang gửi dở"
        data += chunk
    assert len(data) > 15_000_000
    assert stack.row(sid)["state"] == "judging"


def test_monitor_keeps_pinging_other_workers_while_a_big_job_is_being_sent(make_stack):
    stack = make_stack(ping_interval_s=0.1, peer_dead_after_s=30, send_timeout_s=30)
    add_big_problem(stack)
    slow = stack.worker("slow", rcvbuf=4096)
    stack.submit(problem_id="big")
    time.sleep(0.3)
    other = stack.worker("other")
    assert other.recv(2, skip_ping=False)["type"] == "PING"
    assert other.recv(2, skip_ping=False)["type"] == "PING"
    assert slow.sock  # worker chậm vẫn đang được gửi, không bị bỏ


def test_send_that_does_not_complete_in_send_timeout_marks_worker_dead_and_redelivers(make_stack):
    stack = make_stack(ping_interval_s=0.1, peer_dead_after_s=60, send_timeout_s=1.0)
    add_big_problem(stack)
    stuck = stack.worker("stuck", rcvbuf=4096)  # đăng ký trước nên nhận bài trước
    sid = stack.submit(problem_id="big")
    other = stack.worker("other")
    started = time.monotonic()
    job = other.take_job(timeout=10)
    assert job["submission_id"] == sid and job["attempt_id"] == 2
    assert 0.8 < time.monotonic() - started < 6
    assert stack.row(sid)["delivery_count"] == 2
    assert stuck.sock


def test_worker_that_closes_the_socket_is_noticed_immediately(make_stack):
    stack = make_stack(ping_interval_s=30, peer_dead_after_s=60)
    gone = stack.worker("gone")
    sid = stack.submit()
    gone.take_job()
    other = stack.worker("other")
    gone.close()
    started = time.monotonic()
    job = other.take_job(timeout=3)
    assert job["submission_id"] == sid and time.monotonic() - started < 2


@pytest.mark.parametrize("seq", [0, 7])
def test_pong_with_integer_seq_is_accepted_silently(stack, seq):
    w = stack.worker()
    w.send({"type": "PONG", "seq": seq})
    with pytest.raises(TimeoutError):
        w.recv(0.3)
