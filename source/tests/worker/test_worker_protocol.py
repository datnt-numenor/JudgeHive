"""Phía worker của cổng worker (README mục 2): REGISTER, PING/PONG, JOB/PROGRESS/RESULT, lỗi D16, backoff.

Server giả là socket thật; grade_fn giả (hoặc executor thật với Python) để kiểm luồng giao thức."""

import os
import signal
import socket
import subprocess
import sys
import threading
import time

import pytest

from _submissions import SUB
from _worker_helpers import case, make_job
from common import protocol
from worker import sandbox

AC_BODY = {"verdict": "AC", "time_ms": 1, "memory_kib": 1}


def ok_grade(job, ctl, *, work_root, uid):
    for i in range(len(job.tests) + 1):
        if not ctl.progress(i):
            return None
    return dict(AC_BODY)


class Blocker:
    """grade_fn chặn đến khi được thả hoặc bị hủy; ghi lại việc bị hủy."""

    def __init__(self):
        self.started, self.release = threading.Event(), threading.Event()
        self.cancelled, self.finished = threading.Event(), threading.Event()

    def __call__(self, job, ctl, *, work_root, uid):
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


# ----------------------------------------------------------------------------------------------- REGISTER

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
    {"type": "PING", "seq": 1},
    {"type": "JOB"},
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


def test_no_answer_to_register_times_out_and_reconnects(server, make_worker):
    make_worker(register_wait_s=0.5)
    first = server.accept()
    assert first.recv()["type"] == "REGISTER"
    assert first.expect_closed(5)
    assert_reconnects(server)


# ------------------------------------------------------------------------------------------ PING / PONG

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
    started = time.monotonic()
    conn.send({"type": "PING", "seq": 9})
    assert conn.recv(2) == {"type": "PONG", "seq": 9}
    assert time.monotonic() - started < 1.0
    blocker.release.set()


# ------------------------------------------------------------------------------------- JOB, PROGRESS, RESULT

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
    for i in range(0, len(raw), 1):
        conn.send_raw(raw[i:i + 1])
    conn.send_raw(ping + ping)  # hai PING dính nhau trong một lần gửi
    msgs = conn.recv_all("RESULT")
    assert msgs[-1]["submission_id"] == 5
    pongs = [m for m in msgs if m["type"] == "PONG"]
    while len(pongs) < 2:
        pongs.append(conn.recv(2))
    assert pongs == [{"type": "PONG", "seq": 3}] * 2


def test_grade_fn_exception_becomes_se_result(server, make_worker):
    def boom(job, ctl, *, work_root, uid):
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


# --------------------------------------------------------------------------------------- lỗi phía server

@pytest.mark.parametrize("bad", [
    pytest.param(b"not json\n", id="not-json"),
    pytest.param(b"[1,2]\n", id="not-object"),
    pytest.param(b'{"no_type":1}\n', id="missing-type"),
    pytest.param(b'{"type":5}\n', id="type-not-string"),
    pytest.param(b"\xff\xfe\n", id="bad-utf8"),
    pytest.param(b'{"type":"PING"}\n', id="ping-missing-seq"),
    pytest.param(b'{"type":"PING","seq":"1"}\n', id="ping-seq-string"),
    pytest.param(b'{"type":"WHAT"}\n', id="unknown-type"),
    pytest.param(b'{"type":"JOB","submission_id":1}\n', id="job-missing-fields"),
    pytest.param(protocol.encode(make_job("ruby", "x", [case("", "")])), id="job-bad-language"),
    pytest.param(protocol.encode(make_job("python", "x", [])), id="job-no-tests"),
])
def test_bad_message_from_server_closes_and_reconnects(server, make_worker, bad):
    _, conn = start(server, make_worker)
    conn.send_raw(bad)
    assert conn.expect_closed(5)
    assert_reconnects(server)


def test_line_too_long_closes_and_reconnects_without_waiting_for_newline(server, make_worker):
    _, conn = start(server, make_worker, max_line_s2w=2000)
    conn.send_raw(b"x" * 3000)  # chưa có \n mà buffer đã vượt giới hạn
    assert conn.expect_closed(5)
    assert_reconnects(server)


def test_server_error_message_after_register_is_not_fatal(server, make_worker):
    _, conn = start(server, make_worker)
    conn.send({"type": "ERROR", "code": "BAD_MESSAGE", "message": "x"})
    conn.send({"type": "PING", "seq": 1})
    assert conn.recv(2) == {"type": "PONG", "seq": 1}


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


def test_silent_server_is_declared_dead_and_worker_reconnects(server, make_worker):
    _, conn = start(server, make_worker, peer_dead_after_s=0.6)
    assert conn.expect_closed(5)
    assert_reconnects(server)


def test_any_received_byte_counts_as_alive(server, make_worker):
    _, conn = start(server, make_worker, peer_dead_after_s=0.8)
    for seq in range(8):  # 8 × 0.25 s = 2 s > peer_dead_after_s, nhưng luôn có byte đến
        conn.send({"type": "PING", "seq": seq})
        assert conn.recv(2)["type"] == "PONG"
        time.sleep(0.25)


def test_stop_closes_connection_and_thread_exits(server, make_worker):
    worker, conn = start(server, make_worker)
    worker.stop()
    assert conn.expect_closed(5)


# ---------------------------------------------------------------------------------------------- backoff

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


# --------------------------------------------------------------------------- tiến trình worker thật (main)

def run_main(env_extra, timeout=15):
    env = {"PATH": os.environ["PATH"], "PYTHONPATH": "/src", "PYTHONDONTWRITEBYTECODE": "1", **env_extra}
    return subprocess.run([sys.executable, "-m", "worker.main"], env=env, capture_output=True, text=True,
                          timeout=timeout, cwd="/src")


def test_main_exits_nonzero_without_secret():
    p = run_main({})
    assert p.returncode == 2 and "secret" in p.stderr


def test_main_exits_nonzero_when_work_root_is_inside_tmp():
    p = run_main({"JH_WORKER_SECRET": "x", "JH_WORK_ROOT": "/tmp/work"})
    assert p.returncode == 2 and "JH_WORK_ROOT" in p.stderr and "x" not in p.stderr.replace("JH_WORK_ROOT", "")


def spawn_main(server, work_root, **extra):
    env = {"PATH": os.environ["PATH"], "PYTHONPATH": "/src", "PYTHONDONTWRITEBYTECODE": "1",
           "JH_SERVER_HOST": "127.0.0.1", "JH_WORKER_PORT": str(server.port), "JH_WORK_ROOT": work_root,
           "JH_LOG_LEVEL": "INFO", **extra}
    return subprocess.Popen([sys.executable, "-m", "worker.main"], env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, cwd="/src")


def test_main_process_registers_grades_and_exits_cleanly_on_sigterm(server, work_root):
    proc = spawn_main(server, work_root, JH_WORKER_SECRET="canary-secret-1", JH_WORKER_NAME="proc-test")
    try:
        conn = server.accept(15)
        reg = conn.register_ok()
        assert reg["secret"] == "canary-secret-1" and reg["name"] == "proc-test"
        conn.send(make_job("python", SUB["ac"]["python"], [case("1 2\n", "3\n")]))
        assert conn.recv_all("RESULT", 30)[-1]["verdict"] == "AC"
    finally:
        proc.send_signal(signal.SIGTERM)
    assert proc.wait(timeout=15) == 0
    assert "canary-secret-1" not in proc.stdout.read() + proc.stderr.read()  # secret không bị ghi vào log


@pytest.mark.parametrize("mode", ["env", "file"])
def test_secret_is_not_readable_by_candidate_code(server, work_root, tmp_path, mode):
    """Thử đọc thật /proc/1/environ, /proc/<pid worker>/environ và file secret dưới uid thí sinh. `os.environ.pop`
    không xóa bản gốc trong /proc nên bảo vệ thật là khác uid (và ưu tiên dùng file)."""
    canary = f"CANARY-{mode}-5f1e9a"
    extra = {}
    secret_file = None
    if mode == "env":
        extra["JH_WORKER_SECRET"] = canary
    else:
        secret_file = os.path.join(work_root, "secret-file")  # 0400 root, ngoài mọi thư mục thí sinh ghi được
        with open(secret_file, "w") as f:
            f.write(canary + "\n")
        os.chmod(secret_file, 0o400)
        extra["JH_WORKER_SECRET_FILE"] = secret_file
    proc = spawn_main(server, work_root, **extra)
    try:
        conn = server.accept(15)
        assert conn.register_ok()["secret"] == canary
        code = f'''
import os
canary = {canary!r}
for path in ("/proc/1/environ", "/proc/{proc.pid}/environ", "/proc/{proc.pid}/cmdline", {secret_file!r}):
    if not path:
        continue
    try:
        data = open(path, "rb").read()
        print(path, "READ", "LEAK" if canary.encode() in data else "noleak")
    except Exception as e:
        print(path, type(e).__name__)
print("env", "LEAK" if any(canary in v for v in os.environ.values()) else "clean")
'''
        conn.send(make_job("python", code, [case("", "x\n", True)]))
        result = conn.recv_all("RESULT", 30)[-1]
        out = result["sample_stdout"]
        assert "LEAK" not in out and canary not in out, out
        assert f"/proc/1/environ PermissionError" in out
        assert f"/proc/{proc.pid}/environ PermissionError" in out
        assert "env clean" in out
        if secret_file:
            assert f"{secret_file} PermissionError" in out
    finally:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=15)


def test_next_job_sent_right_after_result_is_never_rejected_as_busy(server, make_worker):
    """Server gửi JOB kế tiếp ngay khi nhận RESULT: worker phải nhả cờ bận trước khi gửi RESULT."""
    _, conn = start(server, make_worker, ok_grade)
    for attempt in range(1, 61):
        conn.send(make_job("python", "x", [case("", "")], attempt_id=attempt))
        while True:
            msg = conn.recv(5)
            if msg["type"] == "RESULT":
                assert msg["attempt_id"] == attempt
                break
