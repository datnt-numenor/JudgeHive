"""Hạ tầng test server: worker giả bằng socket thật + `common.protocol`, và một "stack" (DB tạm, cổng worker,
dispatcher) chạy bằng thread thật như production."""

import socket
import threading
import time

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


# Dòng JOB gần 16 MB: lớn hơn mọi bộ đệm socket cộng lại, nên sendall chặn cho tới khi worker đọc.
BIG = problems.TestCase(False, b"a" * 15_500_000, b"b\n")


def wait_until(predicate, timeout=5.0, interval=0.01):
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

    def __init__(self, port, rcvbuf=None):
        self.sock = socket.socket()
        if rcvbuf is not None:  # trước connect: cửa sổ nhận nhỏ làm sendall của server chặn khi JOB lớn
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
        self.sock.settimeout(5)
        self.sock.connect(("127.0.0.1", port))
        self.decoder = protocol.LineDecoder(protocol.MAX_LINE_SERVER_TO_WORKER)
        self._pending = []
        self.worker_id = None

    def send(self, obj):
        self.sock.sendall(protocol.encode(obj))

    def send_raw(self, data):
        self.sock.sendall(data)

    def recv(self, timeout=5.0, skip_ping=True):
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

    def expect_error(self, code, timeout=5.0):
        msg = self.recv(timeout)
        assert msg["type"] == "ERROR" and msg["code"] == code, msg
        return msg

    def closed(self, timeout=5.0):
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

    def take_job(self, timeout=5.0):
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


def make_config(tmp_path, **overrides):
    cfg = ServerConfig(bind_host="127.0.0.1", worker_port=0, secret=SECRET, db_path=str(tmp_path / "test.db"),
                       problems_dir=str(tmp_path / "problems"), monitor_tick_s=0.05)
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def add_problem(db, problem_id="sum", tests=SUM_TESTS, time_limit_ms=1000, memory_limit_kib=262144):
    data = problems.ProblemData(problem_id, "Sum", "tong hai so", time_limit_ms, memory_limit_kib, tuple(tests))
    with db.write() as c:
        problems.store_problem(c, data, replace=True)
    return data


def add_big_problem(stack):
    add_problem(stack.db, "big", tests=[BIG])


def add_user(db, username="alice", role="user"):
    """Tạo user nếu chưa có (Stack dựng lại trên cùng DB để mô phỏng restart). Trả id."""
    with db.write() as c:
        row = c.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if row is not None:
            return row["id"]
        cur = c.execute("INSERT INTO users (username, password_hash, role, created_at) VALUES (?, 'x', ?, 0)",
                        (username, role))
        return cur.lastrowid


def insert_submission(db, user_id, problem_id="sum", language="python", code="print(1)", **columns):
    columns = {"state": "queued", **columns}
    names = ["user_id", "problem_id", "language", "code", "submitted_at", *columns]
    values = [user_id, problem_id, language, code, 1_000, *columns.values()]
    with db.write() as c:
        cur = c.execute(f"INSERT INTO submissions ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})", values)
        return cur.lastrowid


class Stack:
    """DB tạm + WorkerServer + Dispatcher thật, nghe cổng 0 của 127.0.0.1."""

    def __init__(self, tmp_path, **cfg_overrides):
        self.cfg = make_config(tmp_path, **cfg_overrides)
        self.db = Database(self.cfg.db_path)
        self.db.migrate()
        self.hooks = RecordingHooks()
        self.server = WorkerServer(self.cfg, self.db, self.hooks)
        self.dispatcher = Dispatcher(self.cfg, self.db, self.server, self.hooks)
        self.server.set_handler(self.dispatcher)
        self.user_id = add_user(self.db)
        self.problem = add_problem(self.db)
        self.workers = []

    def start(self):
        self.server.start()
        self.dispatcher.start()
        return self

    def stop(self):
        for w in self.workers:
            w.close()
        self.server.stop()
        self.dispatcher.stop()

    def connect(self, rcvbuf=None):
        w = FakeWorker(self.server.port, rcvbuf)
        self.workers.append(w)
        return w

    def worker(self, name="w1", rcvbuf=None):
        w = self.connect(rcvbuf)
        w.register(name)
        return w

    def submit(self, enqueue=True, **kwargs):
        sid = insert_submission(self.db, self.user_id, **kwargs)
        if enqueue:
            self.dispatcher.enqueue(sid, LEVEL_NEW)
        return sid

    def row(self, sid):
        return dict(self.db.conn().execute("SELECT * FROM submissions WHERE id = ?", (sid,)).fetchone())

    def wait_row(self, sid, predicate, timeout=5.0):
        wait_until(lambda: predicate(self.row(sid)), timeout)
        return self.row(sid)
