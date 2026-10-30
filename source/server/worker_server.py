"""Cổng worker (TCP thuần, README mục "Giao tiếp" 2): nhận kết nối, xác thực `REGISTER`, giữ kết nối (PING/PONG),
nhận `PROGRESS`/`RESULT`, canh hạn chót từng bước (D16) và đánh dấu worker chết.

Luồng thread (PLAN mục 2.1): một thread accept; mỗi kết nối một thread đọc; một thread giám sát (PING, `last_seen`,
watchdog); dispatcher gọi vào đây từ thread của nó. Mọi trạng thái chung nằm dưới `self._lock`; ghi vào socket của một
worker đi qua `conn.send_lock`. Worker chết (`mark_dead`) là hàm idempotent, gọi được từ thread bất kỳ."""

import hmac
import itertools
import logging
import select
import socket
import threading
import time
from dataclasses import dataclass

from common import protocol

log = logging.getLogger("judgehive.worker_server")

# Mã lỗi gửi cho worker. Tên là [ĐỀ XUẤT] (README 2.3, D18); danh sách này là đầu vào cho README mục 5.
LINE_TOO_LONG = "LINE_TOO_LONG"
BAD_JSON = "BAD_JSON"
REGISTER_REJECTED = "REGISTER_REJECTED"
UNSUPPORTED_VERSION = "UNSUPPORTED_VERSION"
REGISTER_TIMEOUT = "REGISTER_TIMEOUT"
UNKNOWN_TYPE = "UNKNOWN_TYPE"
BAD_MESSAGE = "BAD_MESSAGE"
ALREADY_REGISTERED = "ALREADY_REGISTERED"
UNEXPECTED_RESULT = "UNEXPECTED_RESULT"
DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"

RECV_BYTES = 256 * 1024
MAX_RESULT_INT = 2**31 - 1  # trần cho mọi số trong RESULT: số lớn hơn làm SQLite ném OverflowError và bài bị giao lại oan
CONTROL_SEND_TIMEOUT_S = 1.0  # ERROR giữ kết nối và PING không được chặn thread gọi quá lâu (JOB mới dùng send_timeout_s)
LOG_TEXT_MAX = 200
VERDICTS = frozenset({"AC", "WA", "TLE", "MLE", "RE", "CE", "SE"})
RE_KINDS = frozenset({"SIGSEGV", "SIGFPE", "SIGABRT", "SIGKILL", "OTHER_SIGNAL", "EXIT_NONZERO"})


class ResultError(Exception):
    """`RESULT` thiếu field hoặc sai kiểu."""


@dataclass
class JobState:
    """Lần giao đang nằm ở một worker. `last_done`, `deadline` và `confirmed` đổi dưới lock của WorkerServer.
    `confirmed` (D22): worker đã xác nhận nhận việc (PROGRESS 0 đúng thứ tự, hoặc RESULT khớp JOB); chỉ ở RAM."""

    submission_id: int
    attempt_id: int
    user_id: int
    tests_total: int
    sample_flags: tuple
    time_limit_ms: int
    is_rejudge: bool
    last_done: int = -1
    deadline: float | None = None
    confirmed: bool = False


class WorkerConn:
    def __init__(self, sock, addr):
        self.sock = sock
        self.addr = addr
        self.worker_id = None
        self.name = None
        self.decoder = protocol.LineDecoder(protocol.MAX_LINE_WORKER_TO_SERVER)
        self.send_lock = threading.Lock()
        self.state = "new"  # new -> idle <-> reserved -> busy -> idle; dead là trạng thái cuối
        self.job = None
        self.sending = False
        self.counted = True  # còn tính vào số kết nối chưa xác thực
        self.connected_at = time.monotonic()
        self.last_seen = self.last_ping_at = self.idle_since = self.connected_at
        self.ping_seq = 0
        self.last_pong_seq = None
        self.jobs_done = 0


def _text(value):
    return repr(str(value)[:LOG_TEXT_MAX])


def _is_int(value):
    return type(value) is int


def parse_result(msg):
    """Kiểm tra field của `RESULT` theo README mục RESULT. Trả dict đã chuẩn hóa, hoặc raise ResultError."""

    def integer(key, minimum=0):
        value = msg.get(key)
        if not _is_int(value) or not minimum <= value <= MAX_RESULT_INT:
            raise ResultError(f"{key} thiếu, sai kiểu hoặc ngoài khoảng")
        return value

    def string(key):
        value = msg.get(key)
        if not isinstance(value, str):
            raise ResultError(f"{key} thiếu hoặc sai kiểu")
        return value

    verdict = msg.get("verdict")
    if not isinstance(verdict, str) or verdict not in VERDICTS:
        raise ResultError("verdict thiếu hoặc lạ")
    body = {"submission_id": integer("submission_id"), "attempt_id": integer("attempt_id"), "verdict": verdict,
            "time_ms": None, "memory_kib": None, "failed_test": None, "failed_is_sample": None, "re_kind": None,
            "ce_message": None, "sample_stdout": None, "se_detail": None}
    if verdict in ("AC", "WA", "TLE", "MLE"):
        body["time_ms"], body["memory_kib"] = integer("time_ms"), integer("memory_kib")
    if verdict in ("WA", "TLE", "MLE", "RE"):
        body["failed_test"] = integer("failed_test", 1)
        sample = msg.get("failed_is_sample")
        if type(sample) is not bool:
            raise ResultError("failed_is_sample thiếu hoặc sai kiểu")
        body["failed_is_sample"] = sample
        if sample:
            body["sample_stdout"] = string("sample_stdout")
    if verdict == "RE":
        kind = msg.get("re_kind")
        if not isinstance(kind, str) or kind not in RE_KINDS:
            raise ResultError("re_kind thiếu hoặc lạ")
        body["re_kind"] = kind
    if verdict == "CE":
        body["ce_message"] = string("ce_message")
    if verdict == "SE" and "se_detail" in msg:
        body["se_detail"] = string("se_detail")
    return body


def check_semantics(job, body):
    """D18: `failed_test` trong 1..tests_total và `failed_is_sample` khớp test thật của lần giao. Trả lý do lỗi hoặc None."""
    failed = body["failed_test"]
    if failed is None:
        return None
    if failed > job.tests_total:
        return "failed_test vượt số test"
    if body["failed_is_sample"] != job.sample_flags[failed - 1]:
        return "failed_is_sample không khớp test"
    return None


class WorkerServer:
    def __init__(self, cfg, db, hooks, handler=None):
        self.cfg, self.db, self.hooks = cfg, db, hooks
        self.handler = handler  # có handle_result(job, body) -> bool và job_lost(job, reason)
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._workers = {}  # worker_id -> WorkerConn (đã REGISTER_OK, chưa chết)
        self._conns = set()  # mọi kết nối đang mở, kể cả chưa xác thực (để stop() đóng hết)
        self._unauth = 0
        self._ids = itertools.count(1)
        self._stop = threading.Event()
        self._stopping = False
        self._listener = None
        self._threads = []
        self.port = None
        self.after_send_hook = None  # điểm chèn cho test: gọi ngay sau khi sendall(JOB) xong, trước khi đặt mốc watchdog

    def set_handler(self, handler):
        self.handler = handler

    # ------------------------------------------------------------------ vòng đời

    def start(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.cfg.bind_host, self.cfg.worker_port))
        sock.listen(64)
        self._listener = sock
        self.port = sock.getsockname()[1]
        for target, name in ((self._accept_loop, "worker-accept"), (self._monitor_loop, "worker-monitor")):
            t = threading.Thread(target=target, name=name, daemon=True)
            t.start()
            self._threads.append(t)
        log.info("cổng worker lắng nghe %s:%s", self.cfg.bind_host, self.port)

    def stop(self):
        self._stopping = True
        self._stop.set()
        listener = self._listener
        if listener is not None:
            try:
                listener.shutdown(socket.SHUT_RDWR)  # đánh thức accept() đang chờ
            except OSError:
                pass
            listener.close()
        with self._lock:
            conns = list(self._conns)
            self._cond.notify_all()
        for conn in conns:
            self._close_socket(conn)
        for t in self._threads:
            t.join(timeout=2)

    # ------------------------------------------------------------------ API cho dispatcher

    def reserve_idle(self):
        """Chờ có worker rảnh và giữ chỗ nó. Trả None khi server đang dừng."""
        with self._cond:
            while not self._stop.is_set():
                idle = [c for c in self._workers.values() if c.state == "idle"]
                if idle:
                    conn = min(idle, key=lambda c: c.idle_since)
                    conn.state = "reserved"
                    return conn
                self._cond.wait(timeout=0.5)
        return None

    def release(self, conn):
        with self._cond:
            if conn.state == "reserved":
                conn.state = "idle"
                conn.idle_since = time.monotonic()
                self._cond.notify_all()

    def is_alive(self, conn):
        return conn.state != "dead"

    def assign_job(self, conn, job):
        """Gắn `job` vào worker đã giữ chỗ. False nếu worker đã chết."""
        with self._lock:
            if conn.state != "reserved":
                return False
            conn.state = "busy"
            conn.job = job
            conn.sending = True
            return True

    def send_job(self, conn, data):
        """Gửi dòng `JOB`. Lỗi gửi (kể cả quá `send_timeout_s`) thì worker bị đánh dấu chết và job giao lại theo D1."""
        try:
            with conn.send_lock:
                conn.sock.sendall(data)
        except OSError as e:
            self.mark_dead(conn, f"gửi JOB lỗi: {type(e).__name__}")
            return False
        hook = self.after_send_hook
        if hook is not None:
            hook(conn)
        now = time.monotonic()
        with self._lock:
            if conn.state == "dead":
                return False
            conn.sending = False
            conn.last_seen = now  # một lần gửi dài không bị tính là im lặng
            job = conn.job
            # PROGRESS(0) có thể về trước khi dòng này chạy: khi đó không đè cửa sổ của nó
            if job is not None and job.last_done == -1:
                job.deadline = now + self.cfg.compile_timeout_max_s + self.cfg.slack_s
        return True

    def snapshot(self):
        with self._lock:
            return [{"worker_id": c.worker_id, "name": c.name, "addr": c.addr, "state": c.state,
                     "submission_id": c.job.submission_id if c.job else None,
                     "attempt_id": c.job.attempt_id if c.job else None}
                    for c in self._workers.values()]

    # ------------------------------------------------------------------ accept và kết nối

    def _accept(self):
        return self._listener.accept()

    def _accept_loop(self):
        while not self._stop.is_set():
            try:
                sock, addr = self._accept()
            except OSError:
                if self._stop.is_set() or self._listener.fileno() == -1:
                    return  # listener đã đóng (stop())
                # lỗi tạm thời (hết file descriptor, kết nối bị hủy giữa chừng): vẫn phải nhận worker sau đó
                log.exception("accept lỗi tạm thời, thử lại")
                self._stop.wait(0.2)
                continue
            with self._lock:
                over = self._unauth >= self.cfg.max_unauthenticated
                if not over:
                    self._unauth += 1
            if over:
                log.warning("từ chối %s: quá %d kết nối chưa xác thực", addr, self.cfg.max_unauthenticated)
                self._close_raw(sock)
                continue
            conn = WorkerConn(sock, addr)
            with self._lock:
                self._conns.add(conn)
            try:
                threading.Thread(target=self._serve, args=(conn,), name=f"worker-conn-{addr[1]}", daemon=True).start()
            except RuntimeError:
                log.exception("không tạo được thread cho kết nối %s", addr)
                self._finish(conn)

    def _serve(self, conn):
        registered = False
        try:
            pending = self._handshake(conn)
            if pending is not None:
                registered = True
                self._read_loop(conn, pending)
        except Exception:
            log.exception("lỗi không mong đợi ở kết nối %s", conn.addr)
        finally:
            if registered:
                self.mark_dead(conn, "kết nối đóng")
            self._finish(conn)
            self.db.close_thread()

    def _finish(self, conn):
        self._release_unauth(conn)
        self._close_socket(conn)
        with self._lock:
            self._conns.discard(conn)

    def _release_unauth(self, conn):
        with self._lock:
            if conn.counted:
                conn.counted = False
                self._unauth -= 1

    def _handshake(self, conn):
        """Đọc và kiểm `REGISTER` (README 2.3, 2.5). Trả danh sách event còn dư sau `REGISTER` nếu thành công, hoặc None
        (đã gửi ERROR và đóng) nếu bị từ chối."""
        deadline = time.monotonic() + self.cfg.register_timeout_s
        events = []
        while not events:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._reject(conn, REGISTER_TIMEOUT, "no valid REGISTER within the time limit")
                return None
            conn.sock.settimeout(remaining)
            try:
                data = conn.sock.recv(RECV_BYTES)
            except socket.timeout:
                continue
            except OSError:
                return None
            if not data:
                return None
            events = conn.decoder.feed(data)
        first, rest = events[0], events[1:]
        if isinstance(first, protocol.LineTooLong):
            self._reject(conn, LINE_TOO_LONG, "line too long")
            return None
        if isinstance(first, protocol.Malformed):
            code = REGISTER_REJECTED if first.reason == protocol.REASON_MISSING_TYPE else BAD_JSON
            self._reject(conn, code, "registration rejected" if code == REGISTER_REJECTED else "malformed JSON")
            return None
        msg = first.obj
        req_id = msg.get("req_id") if isinstance(msg.get("req_id"), str) else None
        name, secret, version = msg.get("name"), msg.get("secret"), msg.get("protocol_version")
        well_formed = (msg["type"] == "REGISTER" and req_id is not None and isinstance(name, str)
                       and 1 <= len(name) <= 64 and isinstance(secret, str) and _is_int(version))
        # định dạng và secret trước, protocol_version sau; secret sai thì luôn dùng mã chung
        if not well_formed or not hmac.compare_digest(secret.encode("utf-8"), self.cfg.secret.encode("utf-8")):
            self._reject(conn, REGISTER_REJECTED, "registration rejected", req_id)
            return None
        if version != protocol.PROTOCOL_VERSION:
            self._reject(conn, UNSUPPORTED_VERSION, f"server expects protocol_version {protocol.PROTOCOL_VERSION}",
                         req_id)
            return None
        with self._lock:
            conn.worker_id = next(self._ids)
        conn.name = name
        try:
            with conn.send_lock:
                conn.sock.settimeout(self.cfg.send_timeout_s)
                conn.sock.sendall(protocol.encode({"type": "REGISTER_OK", "req_id": req_id,
                                                   "worker_id": conn.worker_id}))
        except OSError:
            return None
        self._release_unauth(conn)
        now = time.monotonic()
        with self._cond:
            conn.last_seen = conn.last_ping_at = conn.idle_since = now
            conn.state = "idle"
            self._workers[conn.worker_id] = conn
            self._cond.notify_all()
        log.info("worker #%d %s đăng ký từ %s", conn.worker_id, _text(name), conn.addr)
        return rest

    def _reject(self, conn, code, message, req_id=None):
        self._try_send(conn, _error(code, message, req_id), lock_timeout=0.5)
        self._close_socket(conn)

    # ------------------------------------------------------------------ vòng đọc

    def _read_loop(self, conn, events):
        while True:
            for event in events:
                if not self._handle_event(conn, event):
                    return
            if conn.state == "dead":
                return
            try:
                data = conn.sock.recv(RECV_BYTES)
            except socket.timeout:
                events = []  # settimeout(30) áp cho cả recv: đọc tiếp (PLAN mục 2.1)
                continue
            except OSError:
                self.mark_dead(conn, "lỗi đọc socket")
                return
            if not data:
                self.mark_dead(conn, "worker đóng socket")
                return
            events = conn.decoder.feed(data)

    def _handle_event(self, conn, event):
        """Xử lý một dòng nhận từ worker đã đăng ký (README 2.3, D16). False: dừng đọc (kết nối đã đóng)."""
        with self._lock:
            conn.last_seen = time.monotonic()
        if isinstance(event, protocol.LineTooLong):
            self.mark_dead(conn, "dòng quá dài", error=_error(LINE_TOO_LONG, "line too long"))
            return False
        if isinstance(event, protocol.Malformed):
            self._warn(conn, BAD_JSON, "malformed JSON")
            return True
        msg = event.obj
        kind = msg["type"]
        if kind == "PONG":
            return self._on_pong(conn, msg)
        if kind == "PROGRESS":
            return self._on_progress(conn, msg)
        if kind == "RESULT":
            return self._on_result(conn, msg)
        if kind == "REGISTER":
            self.mark_dead(conn, "REGISTER lần hai", error=_error(ALREADY_REGISTERED, "already registered"))
            return False
        if kind == "ERROR":
            log.warning("worker #%s báo lỗi: code=%s message=%s", conn.worker_id, _text(msg.get("code")),
                        _text(msg.get("message")))
            return True
        self._warn(conn, UNKNOWN_TYPE, f"unknown type {_text(kind)}")
        return True

    def _warn(self, conn, code, message):
        """Gửi ERROR nhưng giữ kết nối. Lock gửi bận (đang gửi JOB lớn) quá 1 giây thì bỏ qua lượt ERROR."""
        if not self._try_send(conn, _error(code, message), lock_timeout=1.0, socket_timeout=CONTROL_SEND_TIMEOUT_S):
            log.info("worker #%s: bỏ ERROR %s vì lock gửi bận hoặc lỗi gửi", conn.worker_id, code)

    def _on_pong(self, conn, msg):
        if not _is_int(msg.get("seq")):
            self._warn(conn, BAD_MESSAGE, "PONG needs integer seq")
            return True
        conn.last_pong_seq = msg["seq"]
        return True

    def _on_progress(self, conn, msg):
        keys = ("submission_id", "attempt_id", "tests_done", "tests_total")
        if not all(_is_int(msg.get(k)) for k in keys):
            self._warn(conn, BAD_MESSAGE, "PROGRESS needs integer submission_id, attempt_id, tests_done, tests_total")
            return True
        sid, aid, done, total = (msg[k] for k in keys)
        with self._lock:
            job = conn.job
            matched = job is not None and job.submission_id == sid and job.attempt_id == aid
            in_order = matched and done == job.last_done + 1 and 0 <= done <= total == job.tests_total
            if in_order:
                if done == 0:
                    job.confirmed = True
                job.last_done = done
                job.deadline = time.monotonic() + job.time_limit_ms / 1000 * self.cfg.max_time_factor \
                    + self.cfg.per_test_overhead_s  # D18: cả cửa sổ chờ RESULT sau PROGRESS cuối
        if not matched:
            log.info("worker #%s: bỏ PROGRESS không khớp JOB (submission %s, attempt %s)", conn.worker_id, sid, aid)
        elif not in_order:
            self._warn(conn, BAD_MESSAGE, "PROGRESS tests_done out of order")
        else:
            self.hooks_status(job, "progress", tests_done=done, tests_total=total)
        return True

    def _on_result(self, conn, msg):
        try:
            body = parse_result(msg)
        except ResultError as e:
            self.mark_dead(conn, f"RESULT sai định dạng: {e}", error=_error(BAD_MESSAGE, str(e)))
            return False
        with self._lock:
            job = conn.job
            matched = (job is not None and job.submission_id == body["submission_id"]
                       and job.attempt_id == body["attempt_id"])
            if matched:
                job.confirmed = True  # bật trước check_semantics: RESULT khớp mà sai ngữ nghĩa vẫn xác nhận (D22)
        if not matched:
            self.mark_dead(conn, "RESULT không khớp JOB", error=_error(UNEXPECTED_RESULT, "unexpected RESULT"))
            return False
        problem = check_semantics(job, body)
        if problem:
            self.mark_dead(conn, f"RESULT sai ngữ nghĩa: {problem}", error=_error(BAD_MESSAGE, problem))
            return False
        with self._lock:
            if conn.job is not job:  # mark_dead đã lấy job đi (giao lại)
                return False
            conn.job = None
        try:
            self.handler.handle_result(job, body)
        except Exception:
            log.exception("ghi kết quả bài %s lỗi", job.submission_id)
            try:
                self.handler.job_lost(job, "ghi kết quả lỗi")
            except Exception:
                log.exception("giao lại bài %s lỗi", job.submission_id)
        with self._cond:
            if conn.state == "busy":
                conn.state = "idle"
                conn.idle_since = time.monotonic()
                conn.jobs_done += 1
                self._cond.notify_all()
        return True

    def hooks_status(self, job, event, **info):
        try:
            self.hooks.status(job.submission_id, job.user_id, event, **info)
        except Exception:
            log.exception("hook status lỗi")

    # ------------------------------------------------------------------ giám sát

    def _monitor_loop(self):
        while not self._stop.wait(self.cfg.monitor_tick_s):
            try:
                self._monitor_once()
            except Exception:
                log.exception("thread giám sát lỗi")

    def _monitor_once(self):
        with self._lock:
            conns = list(self._workers.values())
        for conn in conns:
            now = time.monotonic()
            with self._lock:
                if conn.state == "dead" or conn.sending:  # đang gửi dở: không tính là im lặng (D1)
                    continue
                silent = now - conn.last_seen > self.cfg.peer_dead_after_s
                job = conn.job
                overdue = job is not None and job.deadline is not None and now > job.deadline
                ping_due = now - conn.last_ping_at >= self.cfg.ping_interval_s
            if silent:
                self.mark_dead(conn, f"không nhận message nào trong {self.cfg.peer_dead_after_s} giây")
            elif overdue:
                self.mark_dead(conn, "quá hạn chót của bước chấm",
                               error=_error(DEADLINE_EXCEEDED, "step deadline exceeded"))
            elif ping_due:
                self._send_ping(conn)

    def _send_ping(self, conn):
        """PING lấy lock gửi kiểu không chờ: bận (đang gửi JOB lớn) thì bỏ qua lượt này."""
        if not conn.send_lock.acquire(blocking=False):
            return
        failed = False
        try:
            try:
                writable = select.select([], [conn.sock], [], 0)[1]
                if not writable:
                    return
                conn.ping_seq += 1
                conn.sock.settimeout(CONTROL_SEND_TIMEOUT_S)
                conn.sock.sendall(protocol.encode({"type": "PING", "seq": conn.ping_seq}))
                conn.last_ping_at = time.monotonic()
            except (OSError, ValueError):
                failed = True
        finally:
            self._restore_send_timeout(conn, CONTROL_SEND_TIMEOUT_S)
            conn.send_lock.release()
        if failed:
            self.mark_dead(conn, "gửi PING lỗi")

    # ------------------------------------------------------------------ worker chết

    def mark_dead(self, conn, reason, error=None):
        """Idempotent. Lấy job của worker (nếu có) một lần duy nhất, đóng socket, rồi báo dispatcher giao lại."""
        with self._cond:
            if conn.state == "dead":
                return
            conn.state = "dead"
            job, conn.job = conn.job, None
            if conn.worker_id is not None:
                self._workers.pop(conn.worker_id, None)
            self._cond.notify_all()
        if error is not None:
            self._try_send(conn, error, lock_timeout=0.2, socket_timeout=1.0)
        self._close_socket(conn)
        log.info("worker #%s %s mất: %s", conn.worker_id, _text(conn.name), reason)
        if job is not None and not self._stopping:
            try:
                self.handler.job_lost(job, reason)
            except Exception:
                log.exception("giao lại bài %s lỗi", job.submission_id)

    # ------------------------------------------------------------------ socket

    def _try_send(self, conn, data, lock_timeout, socket_timeout=None):
        if not conn.send_lock.acquire(timeout=lock_timeout):
            return False
        try:
            if socket_timeout is not None:
                conn.sock.settimeout(socket_timeout)
            conn.sock.sendall(data)
            return True
        except OSError:
            return False
        finally:
            self._restore_send_timeout(conn, socket_timeout)
            conn.send_lock.release()

    def _restore_send_timeout(self, conn, changed):
        if changed is not None:
            try:
                conn.sock.settimeout(self.cfg.send_timeout_s)
            except OSError:
                pass

    def _close_socket(self, conn):
        self._close_raw(conn.sock)

    @staticmethod
    def _close_raw(sock):
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass


def _error(code, message, req_id=None):
    msg = {"type": "ERROR", "code": code, "message": message}
    if req_id is not None:
        msg["req_id"] = req_id
    return protocol.encode(msg)
