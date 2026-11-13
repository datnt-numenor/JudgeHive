"""Hàm dùng chung cho mọi test. Test chạy từ source/: python -m pytest tests"""
import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from common.protocol import MAX_LINE, receive, send  # noqa: E402
from server.auth import hash_password  # noqa: E402
from server.main import create_app  # noqa: E402
from worker.runner import Runner, grade  # noqa: E402

SECRET = "test-secret-0123456789"
CODE = "print(sum(map(int,input().split())))"


# ---------- Server thật trên loopback (REST qua TestClient, cổng worker TCP thật) ----------

@contextmanager
def system(tmp_path):
    """Server với 3 tài khoản admin/alice/bob (mật khẩu password123) và đề A+B mặc định."""
    app = create_app(str(tmp_path / "test.sqlite3"), SECRET, worker_port=0)
    with TestClient(app) as client:
        def seed():
            for name, role in [("admin", "admin"), ("alice", "user"), ("bob", "user")]:
                app.state.db.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
                                     (name, hash_password("password123"), role))
        client.portal.call(seed)
        yield client, app


def login(client, username="alice"):
    response = client.post("/api/auth/login", json={"username": username, "password": "password123"})
    assert response.status_code == 200, response.text


def submit(client, code=CODE):
    response = client.post("/api/submissions", json={"problem_id": 1, "language": "python", "code": code})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def db_one(client, app, sql, args=()):
    """SQLite chỉ dùng được trong thread của event loop server."""
    return client.portal.call(lambda: app.state.db.one(sql, args))


async def connect(app):
    return await asyncio.open_connection("127.0.0.1", app.state.worker_port, limit=MAX_LINE)


async def register(app, secret=SECRET, name="test-worker"):
    reader, writer = await connect(app)
    await send(writer, {"type": "REGISTER", "name": name, "secret": secret})
    return reader, writer, await asyncio.wait_for(receive(reader), 3)


async def closed_by_server(reader, timeout=2):
    """Server đóng kết nối: EOF, hoặc RST khi server đóng lúc còn byte chưa đọc."""
    try:
        return await asyncio.wait_for(reader.read(), timeout) == b""
    except ConnectionError:
        return True


async def eventually(predicate, timeout=3):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.02)


# ---------- Runner.grade / Runner.run thật, Docker CLI được thay bằng kết quả cho trước ----------

def outcome(**changes):
    base = {"reason": None, "oom": False, "exit": 0, "stdout": "5\n", "stderr": "", "time_ms": 10}
    result = {**base, **changes}
    result["stderr_tail"] = result["stderr"]
    return result


def fake_grade(monkeypatch, language, results, compile_outcome=None, tests=None):
    """`results`: một outcome cho mọi test, hoặc list outcome theo thứ tự test."""
    queue = iter(results) if isinstance(results, list) else None
    monkeypatch.setattr(Runner, "command", lambda self, *args, check=True: b"")
    monkeypatch.setattr(Runner, "create", lambda self, suffix, *args, **kwargs: self.prefix + "-" + suffix)
    monkeypatch.setattr(Runner, "remove", lambda self, name: None)
    monkeypatch.setattr(Runner, "run", lambda self, name, stdin, timeout:
                        (compile_outcome or outcome()) if name.endswith("compile")
                        else (next(queue) if queue else results))
    job = {"language": language, "code": "x", "time_limit_ms": 2000, "memory_limit_kib": 65536,
           "tests": tests or [{"input": "2 3\n", "output": "5\n"}]}
    progress = []
    result = grade(job, threading.Event(), progress.append)
    result["progress"] = progress
    return result


def docker_state(started, finished, **changes):
    return {"Running": False, "OOMKilled": False, "ExitCode": 0, "Error": "",
            "StartedAt": started, "FinishedAt": finished, **changes}


def fake_run(monkeypatch, state, timeout=2, still_running=False):
    """Runner.run thật; `docker inspect` trả `state`, đồng hồ phía CLI bị đẩy lên rất lớn.
    still_running=True: tiến trình `docker start` chưa xong nên watchdog phải kill."""
    class Process:
        def __init__(self, *args, **kwargs):
            self.killed = False

        def poll(self):
            return 0 if self.killed or not still_running else None

        def kill(self):
            self.killed = True

        def wait(self):
            return 0

    clock = iter(range(0, 10**6, 100))
    monkeypatch.setattr("worker.runner.time.monotonic", lambda: next(clock))
    monkeypatch.setattr("worker.runner.subprocess.Popen", Process)
    monkeypatch.setattr(Runner, "command", lambda self, *args, check=True:
                        json.dumps(state).encode() if args[0] == "inspect" else b"")
    return Runner(threading.Event()).run("container", "", timeout)
