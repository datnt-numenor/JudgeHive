"""Phía worker của cổng worker (README mục 2): REGISTER, PING/PONG, JOB/PROGRESS/RESULT, lỗi D16, backoff.

Server giả là socket thật; grade_fn giả (hoặc executor thật với Python) để kiểm luồng giao thức."""

import socket
import threading
import time

import pytest

from _worker_contract_helpers import case, make_job
from _worker_contract_submissions import SUB
from common import protocol
from worker import sandbox

AC_BODY = {"verdict": "AC", "time_ms": 1, "memory_kib": 1}


def ok_grade(job, ctl, **_):
    for i in range(len(job.tests) + 1):
        if not ctl.progress(i):
            return None
    return dict(AC_BODY)


class Blocker:
    """grade_fn chặn đến khi được thả hoặc bị hủy; ghi lại việc bị hủy."""

    def __init__(self):
        self.started, self.release = threading.Event(), threading.Event()
        self.cancelled, self.finished = threading.Event(), threading.Event()

    def __call__(self, job, ctl, **_):
        ctl.progress(0)
        self.started.set()
        while not ctl.cancel.is_set() and not self.release.wait(0.02):
            pass
        self.finished.set()
        if ctl.cancel.is_set():
            self.cancelled.set()
            return None
        return dict(AC_BODY)


def start(server, make_worker, grade_fn=ok_grade, **cfg):
    """Chạy worker, nhận kết nối đầu, đăng ký thành công. Trả (worker, conn)."""
    worker = make_worker(grade_fn, **cfg)
    conn = server.accept()
    conn.register_ok()
    return worker, conn


def assert_reconnects(server, timeout=10.0):
    """Worker đã đóng kết nối cũ và REGISTER lại trên kết nối mới."""
    conn = server.accept(timeout)
    assert conn.recv()["type"] == "REGISTER"
    return conn


def test_register_message_content(server, make_worker):
    make_worker(name="w-é", secret="s3cr3t")
    reg = server.accept().recv()
    assert reg["type"] == "REGISTER" and reg["name"] == "w-é" and reg["secret"] == "s3cr3t"
    assert reg["protocol_version"] == protocol.PROTOCOL_VERSION == 1
    assert isinstance(reg["req_id"], str) and reg["req_id"]
    assert set(reg) == {"type", "req_id", "name", "secret", "protocol_version"}


def test_register_rejected_then_reconnect_with_backoff(server, make_worker):
    make_worker()
    first = server.accept()
    req = first.recv()
    first.send({"type": "ERROR", "code": "REGISTER_REJECTED", "message": "no", "req_id": req["req_id"]})
    assert first.expect_closed(5)
    second = assert_reconnects(server)
    assert second is not first


@pytest.mark.parametrize("reply", [
    {"type": "REGISTER_OK", "req_id": "WRONG", "worker_id": 1},
    {"type": "REGISTER_OK", "worker_id": 1},
    {"type": "REGISTER_OK", "req_id": None, "worker_id": 1},
])
def test_unexpected_reply_to_register_closes_connection(server, make_worker, reply):
    make_worker()
    conn = server.accept()
    assert conn.recv()["type"] == "REGISTER"
    conn.send(reply)
    assert conn.expect_closed(5)
    assert_reconnects(server)


def test_register_ok_without_integer_worker_id_closes(server, make_worker):
    make_worker()
    conn = server.accept()
    req = conn.recv()
    conn.send({"type": "REGISTER_OK", "req_id": req["req_id"], "worker_id": "3"})
    assert conn.expect_closed(5)


def test_ping_gets_pong_with_same_seq(server, make_worker):
    _, conn = start(server, make_worker)
    for seq in (1, 2, 41):
        conn.send({"type": "PING", "seq": seq})
        assert conn.recv(2) == {"type": "PONG", "seq": seq}


def test_pong_is_immediate_while_grading(server, make_worker):
    blocker = Blocker()
    _, conn = start(server, make_worker, blocker)
    conn.send(make_job("python", "x", [case("", "")]))
    assert blocker.started.wait(5) and conn.recv()["type"] == "PROGRESS"
    conn.send({"type": "PING", "seq": 9})
    assert conn.recv(5) == {"type": "PONG", "seq": 9}
    blocker.release.set()


def test_job_flow_progress_then_result(server, make_worker):
    _, conn = start(server, make_worker)
    conn.send(make_job("python", "print(1)", [case("", "1\n", True), case("", "1\n"), case("", "1\n")],
                       submission_id=42, attempt_id=7))
    msgs = conn.recv_all("RESULT")
    progress = [m for m in msgs if m["type"] == "PROGRESS"]
    assert [m["tests_done"] for m in progress] == [0, 1, 2, 3]
    assert all(m["tests_total"] == 3 and m["submission_id"] == 42 and m["attempt_id"] == 7 for m in progress)
    assert msgs[-1] == {"type": "RESULT", "submission_id": 42, "attempt_id": 7, **AC_BODY}


def test_second_job_is_accepted_after_first_result(server, make_worker):
    _, conn = start(server, make_worker)
    for attempt in (1, 2):
        conn.send(make_job("python", "x", [case("", "")], attempt_id=attempt))
        assert conn.recv_all("RESULT")[-1]["attempt_id"] == attempt


def test_job_split_into_single_bytes_and_glued_with_ping(server, make_worker):
    _, conn = start(server, make_worker)
    raw = protocol.encode(make_job("python", "print(1)", [case("", "1\n")], submission_id=5, attempt_id=6))
    ping = protocol.encode({"type": "PING", "seq": 3})
    for i in range(len(raw)):
        conn.send_raw(raw[i:i + 1])
    conn.send_raw(ping + ping)  # hai PING dính nhau trong một lần gửi
    msgs = conn.recv_all("RESULT")
    assert msgs[-1]["submission_id"] == 5
    pongs = [m for m in msgs if m["type"] == "PONG"]
    while len(pongs) < 2:
        pongs.append(conn.recv(2))
    assert pongs == [{"type": "PONG", "seq": 3}] * 2


def test_grade_fn_exception_becomes_se_result(server, make_worker):
    def boom(job, ctl, **_):
        raise RuntimeError("secret-input-leak-check")

    _, conn = start(server, make_worker, boom)
    conn.send(make_job("python", "x", [case("INPUT-DATA-1", "")]))
    result = conn.recv_all("RESULT")[-1]
    assert result["verdict"] == "SE"
    assert "INPUT-DATA-1" not in str(result) and "secret-input-leak-check" not in str(result)


def test_real_executor_wa_stops_at_first_failing_test(server, make_worker):
    _, conn = start(server, make_worker, None)  # executor thật
    conn.send(make_job("python", SUB["ac"]["python"],
                       [case("1 2\n", "3\n", True), case("5 5\n", "11\n"), case("1 1\n", "2\n")], attempt_id=3))
    msgs = conn.recv_all("RESULT", 30)
    assert [m["tests_done"] for m in msgs if m["type"] == "PROGRESS"] == [0, 1]
    result = msgs[-1]
    assert result["verdict"] == "WA" and result["failed_test"] == 2 and result["failed_is_sample"] is False
    assert result["attempt_id"] == 3 and "sample_stdout" not in result


@pytest.mark.parametrize("bad", [
    pytest.param(b"not json\n", id="not-json"),
    pytest.param(b"[1,2]\n", id="not-object"),
    pytest.param(b'{"no_type":1}\n', id="missing-type"),
    pytest.param(b'{"type":5}\n', id="type-not-string"),
    pytest.param(b"\xff\xfe\n", id="bad-utf8"),
    pytest.param(b'{"type":"PING"}\n', id="ping-missing-seq"),
    pytest.param(b'{"type":"PING","seq":"1"}\n', id="ping-seq-string"),
    pytest.param(b'{"type":"JOB","submission_id":1}\n', id="job-missing-fields"),
    pytest.param(protocol.encode(make_job("ruby", "x", [case("", "")])), id="job-bad-language"),
])
def test_bad_message_from_server_closes_and_reconnects(server, make_worker, bad):
    _, conn = start(server, make_worker)
    conn.send_raw(bad)
    assert conn.expect_closed(5)
    assert_reconnects(server)


def test_line_too_long_closes_and_reconnects_without_waiting_for_newline(server, make_worker):
    _, conn = start(server, make_worker)
    try:
        conn.send_raw(b"x" * (protocol.MAX_LINE_SERVER_TO_WORKER + 1))  # chưa có \n mà buffer đã vượt giới hạn
    except OSError:
        pass  # worker đóng kết nối giữa chừng khi đang gửi
    assert conn.expect_closed(5)
    assert_reconnects(server)


def test_job_while_busy_closes_connection_and_cancels_running_job(server, make_worker):
    blocker = Blocker()
    _, conn = start(server, make_worker, blocker)
    conn.send(make_job("python", "x", [case("", "")], attempt_id=1))
    assert blocker.started.wait(5)
    conn.send(make_job("python", "x", [case("", "")], attempt_id=2))
    assert conn.expect_closed(5)
    assert blocker.cancelled.wait(5)
    assert_reconnects(server)


def test_connection_lost_mid_job_cancels_it_and_never_sends_stale_result(server, make_worker):
    blocker = Blocker()
    _, conn = start(server, make_worker, blocker)
    conn.send(make_job("python", "x", [case("", "")], attempt_id=11))
    assert blocker.started.wait(5)
    conn.close()
    assert blocker.cancelled.wait(5), "bài đang chạy phải bị hủy khi mất kết nối"
    new_conn = assert_reconnects(server)
    blocker.release.set()
    with pytest.raises(TimeoutError):  # không có PROGRESS/RESULT cũ nào qua kết nối mới
        new_conn.recv(1.0)


def test_real_executor_kills_running_program_when_connection_is_lost(server, make_worker):
    _, conn = start(server, make_worker, None)
    conn.send(make_job("python", SUB["loop"]["python"], [case("", "x\n")], time_limit_ms=60000))
    assert conn.recv(10)["type"] == "PROGRESS"
    deadline = time.monotonic() + 5
    while not sandbox.uid_pids(sandbox.JUDGE_UID) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert sandbox.uid_pids(sandbox.JUDGE_UID), "chương trình thí sinh phải đang chạy"
    conn.close()
    new_conn = assert_reconnects(server, 15)
    assert sandbox.uid_pids(sandbox.JUDGE_UID) == []
    with pytest.raises(TimeoutError):
        new_conn.recv(0.5)


def test_backoff_doubles_up_to_cap_while_server_is_down(make_worker):
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()  # không ai lắng nghe: connect bị từ chối
    delays, holder = [], {}

    def fake_sleep(d):
        delays.append(d)
        if len(delays) >= 6:
            holder["w"].stop()

    holder["w"] = make_worker(sleep=fake_sleep, port=port, backoff_base_s=1, backoff_cap_s=10)
    deadline = time.monotonic() + 10
    while len(delays) < 6 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert delays == [1, 2, 4, 8, 10, 10]


def test_backoff_resets_to_base_after_register_ok(server, make_worker):
    delays = []
    make_worker(sleep=lambda d: (delays.append(d), time.sleep(0.05)), backoff_base_s=1, backoff_cap_s=10)
    for _ in range(2):  # hai lần đăng ký thất bại: 1 s, 2 s
        conn = server.accept()
        conn.recv()
        conn.close()
    conn = server.accept()
    conn.register_ok()
    conn.close()  # đăng ký xong rồi mất kết nối: khoảng chờ về 1 s
    assert_reconnects(server)
    assert delays[:3] == [1, 2, 1]
