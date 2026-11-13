"""Hàm dùng chung cho test khóa. Chỉ dùng API công khai: REST, TCP, WebSocket, worker.runner."""
import asyncio
from contextlib import contextmanager
import json
import threading

from fastapi.testclient import TestClient

from common.protocol import MAX_LINE, receive, send
from server.auth import hash_password
from server.main import create_app
from worker.runner import Runner, grade

SECRET = "invariant-secret-0123456789"
CODE = "print(sum(map(int,input().split())))"


@contextmanager
def system(tmp_path):
    app = create_app(str(tmp_path / "inv.sqlite3"), SECRET, worker_port=0)
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


def submit(client, problem_id=1):
    response = client.post("/api/submissions", json={"problem_id": problem_id, "language": "python", "code": CODE})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def state_of(client, sid):
    login(client, "admin")
    return client.get(f"/api/submissions/{sid}").json()


async def connect(app):
    return await asyncio.open_connection("127.0.0.1", app.state.worker_port, limit=MAX_LINE)


async def register(app, secret=SECRET, name="inv-worker"):
    reader, writer = await connect(app)
    await send(writer, {"type": "REGISTER", "name": name, "secret": secret})
    return reader, writer, await asyncio.wait_for(receive(reader), 3)


async def closed_by_server(reader, timeout=2):
    """EOF, hoặc RST khi server đóng lúc còn byte chưa đọc."""
    try:
        return await asyncio.wait_for(reader.read(), timeout) == b""
    except ConnectionError:
        return True


async def silent_for(reader, seconds):
    try:
        data = await asyncio.wait_for(reader.read(1), seconds)
    except TimeoutError:
        return True
    return False if data else "closed"


async def eventually(predicate, timeout=3):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.02)


def outcome(**changes):
    base = {"reason": None, "oom": False, "exit": 0, "stdout": "5\n", "stderr": "", "stderr_tail": "", "time_ms": 10}
    return {**base, **changes}


def fake_grade(monkeypatch, language, test_outcome, compile_outcome=None, tests=None):
    """Runner.grade thật; mọi lời gọi Docker được thay bằng kết quả cho trước."""
    monkeypatch.setattr(Runner, "command", lambda self, *args, check=True: b"")
    monkeypatch.setattr(Runner, "create", lambda self, suffix, *args, **kwargs: self.prefix + "-" + suffix)
    monkeypatch.setattr(Runner, "remove", lambda self, name: None)
    monkeypatch.setattr(Runner, "run", lambda self, name, stdin, timeout:
                        (compile_outcome or outcome()) if name.endswith("compile") else test_outcome)
    job = {"language": language, "code": "x", "time_limit_ms": 2000, "memory_limit_kib": 65536,
           "tests": tests or [{"input": "2 3\n", "output": "5\n"}]}
    return grade(job, threading.Event(), lambda count: None)


def docker_state(started, finished, **changes):
    return {"Running": False, "OOMKilled": False, "ExitCode": 0, "Error": "",
            "StartedAt": started, "FinishedAt": finished, **changes}


def fake_run(monkeypatch, state, timeout=2):
    """Runner.run thật với Docker CLI giả trả `state`; đồng hồ phía CLI bị đẩy lên rất lớn."""
    class Finished:
        def __init__(self, *args, **kwargs):
            pass

        def poll(self):
            return 0

    clock = iter(range(0, 10**6, 100))
    monkeypatch.setattr("worker.runner.time.monotonic", lambda: next(clock))
    monkeypatch.setattr("worker.runner.subprocess.Popen", Finished)
    monkeypatch.setattr(Runner, "command", lambda self, *args, check=True:
                        json.dumps(state).encode() if args[0] == "inspect" else b"")
    return Runner(threading.Event()).run("container", "", timeout)
