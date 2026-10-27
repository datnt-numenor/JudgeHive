"""Hạ tầng cho test hợp đồng cổng worker phía server: worker giả bằng socket thật + `common.protocol`, và một Stack (DB tạm,
cổng worker, dispatcher) chạy bằng thread thật. Chỉ dùng bề mặt API ghi ở server/README.md, mục "Dùng bởi test đã khóa".

Fixture `make_stack` và `stack` nằm ở đây (không ở conftest.py, vì conftest.py của thư mục này đã khóa); mỗi file test
import chúng."""

import socket
import threading
import time

import pytest

from common import protocol
from server import problems
from server.config import ServerConfig
from server.db import Database
from server.dispatcher import LEVEL_NEW, Dispatcher
from server.hooks import Hooks
from server.worker_server import WorkerServer

SECRET = "test-secret"
SUM_TESTS = (
    problems.TestCase(True, b"1 2\n", b"3\n"),
    problems.TestCase(False, b"5 7\n", b"12\n"),
    problems.TestCase(False, b"0 0\n", b"0\n"),
)


def wait_until(predicate, timeout=30.0, interval=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return predicate()


class RecordingHooks(Hooks):
    def __init__(self):
        self._lock = threading.Lock()
        self.events = []
        self.dirty = 0

    def status(self, submission_id, user_id, event, **info):
        with self._lock:
            self.events.append((submission_id, event, info))

    def scoreboard_dirty(self):
        with self._lock:
            self.dirty += 1

    def names(self, submission_id):
        with self._lock:
            return [e for s, e, _ in self.events if s == submission_id]


class FakeWorker:
    """Worker giả: kết nối TCP thật tới cổng worker và nói đúng framing JSON-lines."""

    def __init__(self, port):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=30)
        self.decoder = protocol.LineDecoder(protocol.MAX_LINE_SERVER_TO_WORKER)
        self._pending = []
        self.worker_id = None

    def send(self, obj):
        self.sock.sendall(protocol.encode(obj))

    def send_raw(self, data):
        self.sock.sendall(data)

    def recv(self, timeout=30.0, skip_ping=True):
        """Message tiếp theo. TimeoutError nếu chưa có; EOFError nếu server đã đóng."""
        deadline = time.monotonic() + timeout
        while True:
            while self._pending:
                msg = self._pending.pop(0)
                if skip_ping and msg.get("type") == "PING":
                    continue
                return msg
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError("không nhận được message từ server")
            self.sock.settimeout(left)
            try:
                data = self.sock.recv(262144)
            except socket.timeout:
                raise TimeoutError("không nhận được message từ server") from None
            except OSError as e:
                raise EOFError(str(e)) from e
            if not data:
                raise EOFError("server đã đóng kết nối")
            self._pending.extend(e.obj for e in self.decoder.feed(data) if isinstance(e, protocol.Message))

    def expect_error(self, code, timeout=30.0):
        msg = self.recv(timeout)
        assert msg["type"] == "ERROR" and msg["code"] == code, msg
        return msg

    def closed(self, timeout=30.0):
        """True nếu server đóng kết nối trong `timeout` (bỏ qua message còn lại trước khi đóng)."""
        try:
            while True:
                self.recv(timeout)
        except EOFError:
            return True
        except TimeoutError:
            return False

    def register(self, name="w1", secret=SECRET, version=1, req_id="r1"):
        self.send({"type": "REGISTER", "req_id": req_id, "name": name, "secret": secret, "protocol_version": version})
        reply = self.recv()
        assert reply["type"] == "REGISTER_OK" and reply["req_id"] == req_id, reply
        self.worker_id = reply["worker_id"]
        return self.worker_id

    def take_job(self, timeout=30.0):
        msg = self.recv(timeout)
        assert msg["type"] == "JOB", msg
        return msg

    def progress(self, job, done):
        self.send({"type": "PROGRESS", "submission_id": job["submission_id"], "attempt_id": job["attempt_id"],
                   "tests_done": done, "tests_total": len(job["tests"])})

    def result(self, job, verdict="AC", **fields):
        body = {"type": "RESULT", "submission_id": job["submission_id"], "attempt_id": job["attempt_id"],
                "verdict": verdict}
        if verdict == "AC":
            body.update(time_ms=5, memory_kib=1024)
        body.update(fields)
        self.send(body)

    def finish_ac(self, job):
        for done in range(len(job["tests"]) + 1):
            self.progress(job, done)
        self.result(job)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def register_eventually(stack, timeout=30.0):
    """Kết nối rồi REGISTER, thử lại nếu bị đóng ngay vì còn đủ kết nối chưa xác thực (chỗ được nhả bất đồng bộ)."""
    deadline = time.monotonic() + timeout
    while True:
        worker = stack.connect()
        try:
            worker.register()
            return worker
        except (EOFError, ConnectionError):
            worker.close()
            if time.monotonic() > deadline:
                raise
            time.sleep(0.05)


class Stack:
    """DB tạm + WorkerServer + Dispatcher thật, nghe cổng 0 của 127.0.0.1. `user_id`: dùng lại user của stack trước."""

    def __init__(self, tmp_path, hooks=None, user_id=None, **cfg):
        # PING/peer_dead truyền tường minh và lớn hơn nhiều thời gian chạy của mọi test: worker giả không trả PONG,
        # nên không để test phụ thuộc mặc định của server (có thể đổi) hay bị cắt kết nối giữa chừng
        cfg.setdefault("ping_interval_s", 30.0)
        cfg.setdefault("peer_dead_after_s", 120.0)
        self.cfg = ServerConfig(bind_host="127.0.0.1", worker_port=0, secret=SECRET,
                                db_path=str(tmp_path / "contract.db"), **cfg)
        self.db = Database(self.cfg.db_path)
        self.db.migrate()
        self.hooks = hooks if hooks is not None else RecordingHooks()
        self.server = WorkerServer(self.cfg, self.db, self.hooks)
        self.dispatcher = Dispatcher(self.cfg, self.db, self.server, self.hooks)
        self.server.set_handler(self.dispatcher)
        # `user_id` truyền vào khi dựng lại stack trên cùng file DB (mô phỏng restart): user đã có sẵn
        self.user_id = user_id if user_id is not None else self.db.create_user("alice", "x")
        self.add_problem("sum", SUM_TESTS)
        self.workers = []

    def add_problem(self, problem_id, tests, time_limit_ms=1000, memory_limit_kib=262144):
        data = problems.ProblemData(problem_id, "Title", "statement", time_limit_ms, memory_limit_kib, tuple(tests))
        with self.db.write() as c:
            problems.store_problem(c, data, replace=True)
        return data

    def start(self):
        self.server.start()
        self.dispatcher.start()
        return self

    def stop(self):
        for w in self.workers:
            w.close()
        self.server.stop()
        self.dispatcher.stop()

    def connect(self):
        w = FakeWorker(self.server.port)
        self.workers.append(w)
        return w

    def worker(self, name="w1"):
        w = self.connect()
        w.register(name)
        return w

    def submit(self, problem_id="sum", language="python", code="print(1)", level=LEVEL_NEW):
        sid = self.db.create_submission(self.user_id, problem_id, language, code)
        if level is not None:
            self.dispatcher.enqueue(sid, level)
        return sid

    def view(self, sid):
        return self.db.get_submission(sid)

    def wait_view(self, sid, predicate, timeout=30.0):
        wait_until(lambda: predicate(self.view(sid)), timeout)
        return self.view(sid)

    def registered(self):
        return len(self.server.snapshot())


@pytest.fixture
def make_stack(tmp_path):
    """Tạo (và tự dừng) một Stack đã khởi động; `cfg` ghi đè field của ServerConfig, `hooks` thay hook."""
    stacks = []

    def make(hooks=None, **cfg):
        stack = Stack(tmp_path, hooks=hooks, **cfg).start()
        stacks.append(stack)
        return stack

    yield make
    for stack in stacks:
        stack.stop()


@pytest.fixture
def stack(make_stack):
    return make_stack()
