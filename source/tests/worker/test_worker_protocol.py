"""Phía worker của cổng worker (README mục 2): REGISTER, PING/PONG, JOB/PROGRESS/RESULT, lỗi D16, backoff.

Server giả là socket thật; grade_fn giả (hoặc executor thật với Python) để kiểm luồng giao thức."""

import os
import signal
import subprocess
import sys
import threading
import time

import pytest

from _submissions import SUB
from _worker_helpers import case, make_job
from common import protocol

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

def test_no_answer_to_register_times_out_and_reconnects(server, make_worker):
    make_worker(register_wait_s=0.5)
    first = server.accept()
    assert first.recv()["type"] == "REGISTER"
    assert first.expect_closed(5)
    assert_reconnects(server)


# ------------------------------------------------------------------------------------------ PING / PONG

# ------------------------------------------------------------------------------------- JOB, PROGRESS, RESULT

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


def test_job_sent_right_after_result_is_accepted_while_grader_is_still_stalled_after_sending(server, make_worker):
    """Tất định: ép đúng thứ tự "server nhận RESULT rồi gửi ngay JOB kế tiếp" trong lúc thread chấm còn bị chặn ở điểm
    ngay sau khi gửi RESULT. Cờ bận phải đã được nhả trước khi gửi RESULT; nếu không worker coi JOB là "đến khi
    đang bận" và tự đóng kết nối."""
    worker = make_worker(ok_grade)
    reached, gate = threading.Event(), threading.Event()

    def stall(ctl):
        reached.set()
        gate.wait(15)

    worker.grader.after_result_sent = stall
    conn = server.accept()
    conn.register_ok()
    conn.send(make_job("python", "x", [case("", "")], attempt_id=1))
    assert conn.recv_all("RESULT")[-1]["attempt_id"] == 1
    assert reached.wait(5), "thread chấm phải đang dừng ngay sau khi gửi RESULT"
    conn.send(make_job("python", "x", [case("", "")], attempt_id=2))
    with pytest.raises(TimeoutError):  # kết nối vẫn mở (EOFError nghĩa là worker đã tự đóng vì cờ bận)
        conn.recv(1.0)
    gate.set()
    assert conn.recv_all("RESULT")[-1]["attempt_id"] == 2


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


def test_job_is_not_assigned_on_a_session_that_is_already_closed():
    """Phiên đóng ngay trước khi thread đọc giao JOB: không được chấm trên phiên đã chết (D16: mất kết nối thì hủy)."""
    from worker import executor
    from worker.main import Config, Grader

    class ClosedSession:
        closed = threading.Event()

    session = ClosedSession()
    session.closed.set()
    grader = Grader(Config(), ok_grade)
    job = executor.parse_job(make_job("python", "x", [case("", "")]))
    assert grader.try_assign(session, job) is False
    assert grader.idle.is_set()
