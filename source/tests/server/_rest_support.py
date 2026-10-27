"""Hạ tầng test REST: server thật (uvicorn trong một thread, lifespan thật, cổng 0) và client HTTP bằng thư viện chuẩn.

Dùng `http.client` thay `TestClient` để chạy qua socket thật: header, body chunked, đóng kết nối và thread pool đều
thật, và không cần thêm dependency (TestClient cần httpx)."""

import http.client
import json
import socket
import threading

import uvicorn

from _support import FakeWorker, add_problem, make_config, wait_until
from server import auth, problems
from server.db import Database
from server.main import create_app

PASSWORD = "correct horse battery"
_HASH_CACHE = {}


def password_hash(password=PASSWORD):
    if password not in _HASH_CACHE:
        _HASH_CACHE[password] = auth.hash_password(password)
    return _HASH_CACHE[password]


def add_account(db, username, role="user", password=PASSWORD):
    """Tạo user có mật khẩu thật (trả id)."""
    return db.create_user(username, password_hash(password), role)


class FakeClock:
    """Đồng hồ ms do test điều khiển, cho phiên hết hạn và cửa sổ rate limit."""

    def __init__(self, start_ms=1_800_000_000_000):
        self.now = start_ms

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += int(seconds * 1000)


class Resp:
    def __init__(self, status, headers, body):
        self.status, self.headers, self.body = status, headers, body

    def json(self):
        return json.loads(self.body.decode("utf-8"))

    @property
    def error_code(self):
        return self.json()["error"]["code"]

    def cookie_value(self):
        """Giá trị token trong Set-Cookie, hoặc None."""
        header = self.headers.get("set-cookie")
        if not header:
            return None
        pair = header.split(";", 1)[0]
        return pair.split("=", 1)[1]


class Web:
    def __init__(self, tmp_path, clock=None, prepare=None, **cfg_overrides):
        self.tmp_path, self.clock, self.prepare = tmp_path, clock, prepare
        self.cfg = make_config(tmp_path, **cfg_overrides)
        self.db = Database(self.cfg.db_path)
        self.server = self.thread = self.app = None
        self.port = None

    def start(self):
        self.db.migrate()
        self.app = create_app(self.cfg, clock=self.clock)
        if self.prepare:
            self.prepare(self.app)
        # log_config=None: logger của uvicorn truyền lên root, nên caplog thấy cả "Exception in ASGI application"
        config = uvicorn.Config(self.app, host="127.0.0.1", port=0, log_level="warning", lifespan="on",
                                access_log=False, log_config=None)
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        wait_until(lambda: self.server.started or not self.thread.is_alive(), timeout=15)
        assert self.server.started, "uvicorn không khởi động được"
        self.port = self.server.servers[0].sockets[0].getsockname()[1]
        return self

    def stop(self):
        if self.server is not None:
            self.server.should_exit = True
            self.thread.join(timeout=15)
            self.server = None

    def restart(self):
        self.stop()
        return self.start()

    @property
    def worker_port(self):
        return self.app.state.worker_server.port

    def worker(self, name="w1"):
        w = FakeWorker(self.worker_port)
        w.register(name)
        return w

    def request(self, method, path, *, obj=None, body=None, headers=None, cookie=None, content_type="json"):
        """`obj`: gửi JSON; `body`: bytes thô. `content_type="json"` là application/json, hoặc chuỗi tùy ý, hoặc None."""
        hdrs = dict(headers or {})
        if obj is not None:
            body = json.dumps(obj).encode("utf-8")
        if body is not None and content_type is not None:
            hdrs.setdefault("Content-Type", "application/json" if content_type == "json" else content_type)
        if cookie is not None:
            hdrs["Cookie"] = f"jh_session={cookie}"
        hdrs.setdefault("Connection", "close")
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        try:
            try:
                conn.request(method, path, body=body, headers=hdrs)
            except (BrokenPipeError, ConnectionResetError):
                pass  # server đã trả lời (413) và đóng khi ta còn đang gửi; vẫn đọc response
            r = conn.getresponse()
            data = r.read()
            return Resp(r.status, {k.lower(): v for k, v in r.getheaders()}, data)
        finally:
            conn.close()

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, obj=None, **kw):
        return self.request("POST", path, obj=obj, **kw)

    def raw(self, data, read_timeout=5.0):
        """Gửi byte thô (request nên có `Connection: close`), đọc tới khi server đóng hoặc hết hạn; trả bytes."""
        with socket.create_connection(("127.0.0.1", self.port), timeout=read_timeout) as s:
            try:
                s.sendall(data)
            except (BrokenPipeError, ConnectionResetError):
                pass
            chunks = []
            try:
                while True:
                    part = s.recv(65536)
                    if not part:
                        break
                    chunks.append(part)
            except (socket.timeout, ConnectionResetError):
                pass
            return b"".join(chunks)

    def login(self, username="alice", password=PASSWORD):
        r = self.post("/api/login", {"username": username, "password": password})
        assert r.status == 200, (r.status, r.body)
        return r.cookie_value()

    def submit(self, cookie, token, problem_id="sum", language="python", code="print(1)\n"):
        return self.post("/api/submissions", {"problem_id": problem_id, "language": language, "code": code,
                                               "client_token": token}, cookie=cookie)

    def wait_state(self, cookie, sid, state, timeout=10.0):
        wait_until(lambda: self.get(f"/api/submissions/{sid}", cookie=cookie).json()["state"] == state, timeout)
        return self.get(f"/api/submissions/{sid}", cookie=cookie).json()


def uuid_token(n):
    return f"00000000-0000-4000-8000-{n:012x}"


def ensure_problem(db, problem_id="sum", **kw):
    return add_problem(db, problem_id, **kw)


def hide_problem(db, problem_id):
    with db.write() as c:
        c.execute("UPDATE problems SET hidden = 1 WHERE id = ?", (problem_id,))


SAMPLE_TESTS = (problems.TestCase(True, b"1 2\n", b"3\n"), problems.TestCase(True, b"10 20\n", b"30\n"))
HIDDEN_TESTS = (problems.TestCase(False, b"HIDDEN-IN-7\n", b"HIDDEN-OUT-7\n"),
                problems.TestCase(False, b"HIDDEN-IN-8\n", b"HIDDEN-OUT-8\n"))


def populate(db):
    """alice, bob (user) và root (admin); đề `sum` hiện (2 test mẫu, 2 test ẩn) và `secret` ẩn."""
    for name, role in (("alice", "user"), ("bob", "user"), ("root", "admin")):
        add_account(db, name, role)
    ensure_problem(db, "sum", tests=SAMPLE_TESTS + HIDDEN_TESTS)
    ensure_problem(db, "secret", tests=SAMPLE_TESTS + HIDDEN_TESTS)
    hide_problem(db, "secret")
