"""Judge worker: kết nối tới cổng worker của server, REGISTER, PING/PONG, nhận JOB, gửi PROGRESS/RESULT.

Hành vi theo README mục 2 và PLAN D1, D15, D16. Thread:
- thread chính: kết nối, REGISTER, chờ phiên đóng, hủy bài, backoff rồi kết nối lại;
- thread đọc (mỗi phiên một thread): nhận message, trả PONG ngay, giao JOB cho thread chấm;
- thread chấm (một thread cho cả tiến trình): chạy executor.grade, gửi PROGRESS/RESULT.
"""

import logging
import os
import queue
import random
import select
import shutil
import signal
import socket
import sys
import threading
import time
from dataclasses import dataclass

from common import protocol

from . import executor, languages, sandbox

log = logging.getLogger("worker")

RECV_CHUNK = 256 * 1024
READ_POLL_S = 1.0
GRADER_POLL_S = 0.5


class ConfigError(Exception):
    pass


@dataclass
class Config:
    host: str = "server"
    port: int = protocol.DEFAULT_WORKER_PORT
    secret: str = ""
    name: str = "worker"
    work_root: str = "/work"
    uid: int = sandbox.JUDGE_UID
    peer_dead_after_s: float = protocol.PEER_DEAD_AFTER_S
    send_timeout_s: float = protocol.SEND_TIMEOUT_S
    connect_timeout_s: float = 5.0
    register_wait_s: float = protocol.REGISTER_TIMEOUT_S + 2
    backoff_base_s: float = protocol.RECONNECT_BACKOFF_BASE_S
    backoff_cap_s: float = protocol.RECONNECT_BACKOFF_CAP_S
    jitter: float = 0.25  # [ĐỀ XUẤT] cộng thêm ngẫu nhiên 0..25% khoảng chờ
    max_line_s2w: int = protocol.MAX_LINE_SERVER_TO_WORKER
    cancel_wait_s: float = 15.0


def load_config(env, *, consume_secret=False):
    """Đọc cấu hình từ biến môi trường. Secret ưu tiên `JH_WORKER_SECRET_FILE`.

    `consume_secret`: xóa `JH_WORKER_SECRET` khỏi `env` sau khi đọc. Bản gốc trong /proc/<pid>/environ không xóa
    được; bảo vệ thật là tiến trình thí sinh chạy uid khác nên không đọc được file đó (và ưu tiên dùng file)."""
    secret_file = env.get("JH_WORKER_SECRET_FILE")
    if secret_file:
        try:
            with open(secret_file, encoding="utf-8") as f:
                secret = f.read().strip()
        except OSError as e:
            raise ConfigError(f"không đọc được JH_WORKER_SECRET_FILE: {e.strerror}") from e
    else:
        secret = env.get("JH_WORKER_SECRET", "")
    if consume_secret:
        env.pop("JH_WORKER_SECRET", None)
        env.pop("JH_WORKER_SECRET_FILE", None)
    if not secret:
        raise ConfigError("thiếu secret: đặt JH_WORKER_SECRET_FILE (ưu tiên) hoặc JH_WORKER_SECRET")
    cfg = Config(secret=secret)
    try:
        cfg.host = env.get("JH_SERVER_HOST", cfg.host)
        cfg.port = int(env.get("JH_WORKER_PORT", cfg.port))
        cfg.name = (env.get("JH_WORKER_NAME") or socket.gethostname() or "worker")[:64]
        cfg.work_root = env.get("JH_WORK_ROOT", cfg.work_root)
        cfg.peer_dead_after_s = float(env.get("JH_PEER_DEAD_AFTER_S", cfg.peer_dead_after_s))
    except ValueError as e:
        raise ConfigError(f"cấu hình số không hợp lệ: {e}") from e
    check_work_root(cfg.work_root)
    return cfg


def check_work_root(path):
    """Thư mục làm việc không được nằm trong /tmp, /var/tmp, /dev/shm: sau mỗi test worker xóa mọi file của uid thí
    sinh ở đó, nên file thực thi do uid thí sinh biên dịch sẽ bị xóa theo."""
    real = os.path.realpath(path)
    for root in sandbox.TMP_ROOTS:
        if real == root or real.startswith(root + "/"):
            raise ConfigError(f"JH_WORK_ROOT ({path}) không được nằm trong {root}")


class Backoff:
    """Khoảng chờ kết nối lại: 1, 2, 4, ... giây, trần 10 giây, cộng jitter; đặt lại sau mỗi REGISTER_OK."""

    def __init__(self, base, cap, jitter, rng):
        self.base, self.cap, self.jitter, self.rng = base, cap, jitter, rng
        self.attempt = 0

    def next_delay(self):
        delay = min(self.cap, self.base * 2 ** self.attempt)
        self.attempt += 1
        return delay + self.rng.uniform(0, self.jitter * delay)

    def reset(self):
        self.attempt = 0


class Session:
    """Một kết nối TCP tới server: gửi có lock, đóng idempotent."""

    def __init__(self, sock, cfg, on_close):
        self.sock, self.cfg, self._on_close = sock, cfg, on_close
        self.decoder = protocol.LineDecoder(cfg.max_line_s2w)
        self.send_lock = threading.Lock()
        self.closed = threading.Event()
        self.registered = threading.Event()
        self.close_reason = None
        self.req_id = None
        self.worker_id = None
        self._close_lock = threading.Lock()

    def send(self, obj):
        """Gửi một message; False nếu phiên đã đóng hoặc gửi lỗi (khi đó phiên bị đóng)."""
        try:
            data = protocol.encode(obj, max_line_bytes=protocol.MAX_LINE_WORKER_TO_SERVER)
        except protocol.EncodeError as e:
            log.error("không encode được %s: %s", obj.get("type"), e)
            self.close("không encode được message gửi đi")
            return False
        with self.send_lock:
            if self.closed.is_set():
                return False
            try:
                self.sock.sendall(data)
            except OSError as e:
                self.close(f"lỗi gửi: {e}")
                return False
        return True

    def close(self, reason):
        with self._close_lock:
            if self.closed.is_set():
                return
            self.closed.set()
            self.close_reason = reason
        log.info("đóng kết nối: %s", reason)
        try:
            self.sock.shutdown(socket.SHUT_RDWR)  # đánh thức thread đang recv/sendall
        except OSError:
            pass
        self.sock.close()
        self._on_close(self)

    def read_loop(self, dispatch):
        """Đọc đến khi phiên đóng. Mọi byte nhận được tính là server còn sống (README: giữ kết nối)."""
        last_rx = time.monotonic()
        try:
            while not self.closed.is_set():
                ready, _, _ = select.select([self.sock], [], [], READ_POLL_S)
                if self.closed.is_set():
                    return
                if not ready:
                    if time.monotonic() - last_rx > self.cfg.peer_dead_after_s:
                        self.close(f"quá {self.cfg.peer_dead_after_s:g} giây không nhận được byte nào")
                        return
                    continue
                data = self.sock.recv(RECV_CHUNK)
                if not data:
                    self.close("server đóng kết nối")
                    return
                last_rx = time.monotonic()
                for event in self.decoder.feed(data):
                    if self.closed.is_set():
                        return
                    if isinstance(event, protocol.Message):
                        dispatch(self, event.obj)
                    elif isinstance(event, protocol.LineTooLong):
                        self.close("dòng từ server quá dài")
                        return
                    else:
                        self.close(f"message hỏng từ server ({event.reason})")
                        return
        except Exception as e:  # kể cả lỗi bất ngờ trong dispatch: phiên phải đóng, không để thread đọc chết lặng
            log.exception("lỗi trong thread đọc")
            self.close(f"lỗi đọc: {type(e).__name__}")


class _Ctl:
    """Điều khiển một lần chấm: cờ hủy và kênh PROGRESS."""

    def __init__(self, session, job):
        self.session, self.job = session, job
        self.cancel = threading.Event()

    def progress(self, tests_done):
        if self.cancel.is_set():
            return False
        return self.session.send({"type": "PROGRESS", "submission_id": self.job.submission_id,
                                  "attempt_id": self.job.attempt_id, "tests_done": tests_done,
                                  "tests_total": len(self.job.tests)})


class Grader:
    """Thread chấm: tối đa một bài tại một thời điểm."""

    def __init__(self, cfg, grade_fn):
        self.cfg, self.grade_fn = cfg, grade_fn
        self.inbox = queue.Queue(maxsize=1)
        self.idle = threading.Event()
        self.idle.set()
        self._lock = threading.Lock()
        self._busy = False
        self._current = None
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="grader", daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self._stop.set()

    def try_assign(self, session, job):
        with self._lock:
            if self._busy:
                return False
            self._busy = True
            self.idle.clear()
            ctl = self._current = _Ctl(session, job)
        self.inbox.put_nowait(ctl)
        return True

    def cancel_session(self, session):
        with self._lock:
            if self._current is not None and self._current.session is session:
                self._current.cancel.set()

    def _loop(self):
        while not self._stop.is_set():
            try:
                ctl = self.inbox.get(timeout=GRADER_POLL_S)
            except queue.Empty:
                continue
            try:
                self._run(ctl)
            except Exception:
                log.exception("lỗi trong thread chấm")
            finally:
                self._release(ctl)

    def _release(self, ctl):
        """Nhả cờ bận. Phải xảy ra trước khi gửi RESULT: server gửi JOB kế tiếp ngay khi nhận RESULT, và JOB đến
        lúc worker còn bận thì worker tự đóng kết nối."""
        with self._lock:
            if self._current is ctl:
                self._busy, self._current = False, None
                self.idle.set()

    def _run(self, ctl):
        job, session = ctl.job, ctl.session
        log.info("bắt đầu chấm bài %d lần giao %d (%s, %d test)", job.submission_id, job.attempt_id,
                 job.language.name, len(job.tests))
        try:
            body = self.grade_fn(job, ctl, work_root=self.cfg.work_root, uid=self.cfg.uid)
        except Exception as e:
            log.exception("grade_fn lỗi")
            body = {"verdict": "SE", "se_detail": f"{type(e).__name__}"}
        if body is None or ctl.cancel.is_set() or session.closed.is_set():
            log.info("bỏ kết quả bài %d lần giao %d (đã hủy hoặc mất kết nối)", job.submission_id, job.attempt_id)
            return
        self._release(ctl)
        session.send({"type": "RESULT", "submission_id": job.submission_id, "attempt_id": job.attempt_id, **body})
        log.info("xong bài %d lần giao %d: %s", job.submission_id, job.attempt_id, body["verdict"])


class Worker:
    def __init__(self, cfg, *, grade_fn=executor.grade, sleep=None, rng=None):
        self.cfg = cfg
        self.grader = Grader(cfg, grade_fn)
        self.backoff = Backoff(cfg.backoff_base_s, cfg.backoff_cap_s, cfg.jitter, rng or random.Random())
        self._stop = threading.Event()
        self._sleep = sleep or (lambda s: self._stop.wait(s))
        self._session = None
        self._req = 0

    def stop(self):
        self._stop.set()
        session = self._session
        if session is not None:
            session.close("worker dừng")

    def run(self):
        self.grader.start()
        try:
            while not self._stop.is_set():
                session = self._connect()
                if session is not None:
                    self.backoff.reset()  # README: đặt lại về 1 giây sau mỗi REGISTER_OK
                    self._wait_closed(session)
                    self._cancel_and_wait(session)
                if not self._stop.is_set():
                    self._sleep(self.backoff.next_delay())
        finally:
            self.grader.stop()
            if self._session is not None:
                self._session.close("worker dừng")

    def _connect(self):
        try:
            sock = socket.create_connection((self.cfg.host, self.cfg.port), timeout=self.cfg.connect_timeout_s)
        except OSError as e:
            log.warning("không kết nối được %s:%d: %s", self.cfg.host, self.cfg.port, e)
            return None
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(self.cfg.send_timeout_s)
        session = self._session = Session(sock, self.cfg, self.grader.cancel_session)
        try:
            threading.Thread(target=session.read_loop, args=(self._dispatch,), name="reader", daemon=True).start()
        except RuntimeError as e:  # hết thread (ví dụ pids_limit): thử lại sau backoff
            session.close(f"không tạo được thread đọc: {e}")
            return None
        self._req += 1
        session.req_id = f"r{self._req}"
        if not session.send({"type": "REGISTER", "req_id": session.req_id, "name": self.cfg.name,
                             "secret": self.cfg.secret, "protocol_version": protocol.PROTOCOL_VERSION}):
            return None
        deadline = time.monotonic() + self.cfg.register_wait_s
        while time.monotonic() < deadline and not self._stop.is_set():
            if session.registered.is_set():
                log.info("đã đăng ký, worker_id=%s", session.worker_id)
                return session
            if session.closed.wait(0.02):
                # REGISTER_OK rồi đóng ngay vẫn là một lần đăng ký thành công (backoff phải được đặt lại)
                return session if session.registered.is_set() else None
        session.close("hết hạn chờ REGISTER_OK")
        return None

    def _wait_closed(self, session):
        while not self._stop.is_set() and not session.closed.wait(0.5):
            pass

    def _cancel_and_wait(self, session):
        """Mất kết nối thì hủy bài ngay và chờ grader dọn xong trước khi REGISTER lại (README mục 2.6)."""
        self.grader.cancel_session(session)
        if not self.grader.idle.wait(self.cfg.cancel_wait_s):
            log.warning("grader chưa rảnh sau %g giây", self.cfg.cancel_wait_s)

    def _dispatch(self, session, msg):
        kind = msg["type"]
        if not session.registered.is_set():
            self._dispatch_register(session, msg, kind)
            return
        if kind == "PING":
            seq = msg.get("seq")
            if type(seq) is not int:
                session.close("PING thiếu seq hoặc seq sai kiểu")
                return
            session.send({"type": "PONG", "seq": seq})
        elif kind == "JOB":
            try:
                job = executor.parse_job(msg)
            except executor.BadJob as e:
                session.close(f"JOB sai định dạng: {e}")
                return
            if not self.grader.try_assign(session, job):
                session.close("nhận JOB khi đang bận")
        elif kind == "ERROR":
            log.warning("server báo lỗi: %s", _short(msg.get("code")))
        else:
            session.close(f"type lạ từ server: {_short(kind)}")  # [ĐỀ XUẤT] coi là message sai định dạng

    def _dispatch_register(self, session, msg, kind):
        if kind == "REGISTER_OK":
            worker_id = msg.get("worker_id")
            if msg.get("req_id") != session.req_id or type(worker_id) is not int:
                session.close("REGISTER_OK sai req_id hoặc worker_id")
                return
            session.worker_id = worker_id
            session.registered.set()
        elif kind == "ERROR":
            session.close(f"server từ chối đăng ký: {_short(msg.get('code'))}")
        else:
            session.close(f"message {_short(kind)} trước REGISTER_OK")


def _short(value, limit=64):
    return str(value)[:limit]


def startup_cleanup(cfg):
    """Dọn dư lượng của lần chạy trước (nếu worker bị kill giữa chừng)."""
    os.makedirs(cfg.work_root, mode=0o755, exist_ok=True)
    sandbox.kill_uid(cfg.uid)
    sandbox.cleanup_uid_files(cfg.uid, (cfg.work_root, *sandbox.TMP_ROOTS))
    for name in os.listdir(cfg.work_root):
        if name.startswith(("job-", ".input-")):
            path = os.path.join(cfg.work_root, name)
            shutil.rmtree(path, ignore_errors=True) if os.path.isdir(path) else os.unlink(path)


def main():
    logging.basicConfig(level=os.environ.get("JH_LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        languages.validate()
        cfg = load_config(os.environ, consume_secret=True)
    except (languages.ConfigError, ConfigError) as e:
        log.critical("cấu hình sai: %s", e)
        return 2
    startup_cleanup(cfg)
    worker = Worker(cfg)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: worker.stop())
    log.info("worker %r kết nối tới %s:%d", cfg.name, cfg.host, cfg.port)
    worker.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
