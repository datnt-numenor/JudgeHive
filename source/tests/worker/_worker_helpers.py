"""Hàm dùng chung cho test worker: chấm trực tiếp qua executor, mock server TCP, chương trình thí sinh mẫu."""

import queue
import socket
import threading
import time

from common import protocol
from worker import executor


class FakeCtl:
    """Thay cho điều khiển của thread chấm: ghi lại các PROGRESS, cờ hủy là Event thật."""

    def __init__(self, cancel_after_progress=None):
        self.cancel = threading.Event()
        self.progress_calls = []
        self._cancel_after = cancel_after_progress

    def progress(self, n):
        self.progress_calls.append(n)
        if self._cancel_after is not None and n >= self._cancel_after:
            self.cancel.set()
            return False
        return not self.cancel.is_set()


def case(inp, out, sample=False):
    return {"input": inp, "output": out, "is_sample": sample}


def make_job(language, code, tests, *, time_limit_ms=2000, memory_limit_kib=262144, submission_id=1, attempt_id=1):
    return {"type": "JOB", "submission_id": submission_id, "attempt_id": attempt_id, "language": language,
            "code": code, "time_limit_ms": time_limit_ms, "memory_limit_kib": memory_limit_kib, "tests": tests}


def grade(work_root, language, code, tests, **kw):
    """Chấm thật bằng executor. Trả (body của RESULT, danh sách tests_done đã báo)."""
    ctl = kw.pop("ctl", None) or FakeCtl()
    job = executor.parse_job(make_job(language, code, tests, **kw))
    return executor.grade(job, ctl, work_root=work_root), ctl.progress_calls


# --------------------------------------------------------------------------------------------- mock server

class Conn:
    def __init__(self, sock):
        self.sock = sock
        self.decoder = protocol.LineDecoder(64 * 1024 * 1024)
        self._pending = []

    def send(self, obj):
        self.sock.sendall(protocol.encode(obj))

    def send_raw(self, data):
        self.sock.sendall(data)

    def recv(self, timeout=10.0):
        """Message tiếp theo từ worker. Raise TimeoutError nếu chưa có, EOFError nếu worker đã đóng."""
        deadline = time.monotonic() + timeout
        while not self._pending:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError("không nhận được message từ worker")
            self.sock.settimeout(left)
            try:
                data = self.sock.recv(262144)
            except socket.timeout:
                raise TimeoutError("không nhận được message từ worker") from None
            except OSError as e:
                raise EOFError(str(e)) from e
            if not data:
                raise EOFError("worker đã đóng kết nối")
            self._pending.extend(e.obj for e in self.decoder.feed(data) if isinstance(e, protocol.Message))
        return self._pending.pop(0)

    def recv_all(self, until_type, timeout=30.0):
        """Đọc đến khi gặp message `until_type`; trả danh sách mọi message (gồm cả message đó)."""
        out = []
        deadline = time.monotonic() + timeout
        while True:
            msg = self.recv(max(0.01, deadline - time.monotonic()))
            out.append(msg)
            if msg["type"] == until_type:
                return out

    def expect_closed(self, timeout=10.0):
        try:
            while True:
                self.recv(timeout)
        except EOFError:
            return True
        except TimeoutError:
            return False

    def register_ok(self, worker_id=1):
        """Nhận REGISTER và trả REGISTER_OK. Trả REGISTER."""
        reg = self.recv()
        assert reg["type"] == "REGISTER"
        self.send({"type": "REGISTER_OK", "req_id": reg["req_id"], "worker_id": worker_id})
        return reg

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class MockServer:
    def __init__(self):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self._conns = queue.Queue()
        self._all = []
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self):
        while True:
            try:
                sock, _ = self.sock.accept()
            except OSError:
                return
            conn = Conn(sock)
            self._all.append(conn)
            self._conns.put(conn)

    def accept(self, timeout=10.0):
        try:
            return self._conns.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError("worker không kết nối") from None

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass
        for c in self._all:
            c.close()
