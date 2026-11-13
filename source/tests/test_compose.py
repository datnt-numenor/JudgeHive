"""Smoke test toàn hệ thống đang chạy Compose, tạo/xóa tài khoản thử riêng.

Bật JH_COMPOSE_TESTS=1 và chạy từ source/. Không cần mật khẩu admin có sẵn.
"""
import json
import os
from pathlib import Path
import secrets
import subprocess
import time

import httpx
import pytest
from websockets.sync.client import connect

pytestmark = pytest.mark.skipif(os.environ.get("JH_COMPOSE_TESTS") != "1", reason="Cần Compose đang chạy")
ROOT = Path(__file__).parents[1]
SEED = """
import json,os,sys
from server.db import Database
from server.auth import hash_password
u=json.load(sys.stdin)
d=Database(os.environ['JH_DB'])
d.execute('INSERT INTO users(username,password_hash,role) VALUES(?,?,?)',(u['username'],hash_password(u['password']),'admin'))
"""
CLEAN = """
import json,os,sys
from server.db import Database
d=Database(os.environ['JH_DB'])
name=json.load(sys.stdin)['username']
with d.conn:
 ids=[r['id'] for r in d.all('SELECT id FROM users WHERE username=? OR username=?',(name,name+'-user'))]
 for uid in ids:
  d.conn.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
  d.conn.execute('DELETE FROM submissions WHERE user_id=?',(uid,))
  d.conn.execute('DELETE FROM users WHERE id=?',(uid,))
"""


def exec_python(code, data):
    subprocess.run(["docker", "compose", "exec", "-T", "server", "python", "-c", code],
                   input=json.dumps(data), text=True, cwd=ROOT, check=True, capture_output=True, timeout=20)


def test_real_rest_tcp_docker_and_websocket():
    credentials = {"username": "smoke-" + secrets.token_hex(5), "password": secrets.token_urlsafe(18)}
    exec_python(SEED, credentials)
    base = os.environ.get("JH_E2E_URL", "http://localhost:8000")
    try:
        with httpx.Client(base_url=base, timeout=10) as admin, httpx.Client(base_url=base, timeout=10) as user:
            assert admin.post("/api/auth/login", json=credentials).status_code == 200
            worker_info = admin.get("/api/admin/workers").json()
            assert sum(w["state"] == "idle" for w in worker_info["workers"]) >= 2, worker_info
            person = {**credentials, "username": credentials["username"] + "-user", "role": "user"}
            assert admin.post("/api/admin/users", json=person).status_code == 201
            assert user.post("/api/auth/login", json={k: person[k] for k in ("username", "password")}).status_code == 200
            assert user.get("/api/admin/workers").status_code == 403
            assert "tests" not in user.get("/api/problems/1").json()
            ws_url = base.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
            cookie = "session=" + user.cookies.get("session")
            with connect(ws_url, origin=base, additional_headers={"Cookie": cookie}, ping_interval=None) as ws:
                jobs = []
                for language, file in [("python", "ac.py"), ("cpp", "ac.cpp"), ("java", "Main.java")]:
                    response = user.post("/api/submissions", json={"problem_id": 1, "language": language,
                        "code": (ROOT / "samples" / file).read_text(encoding="utf-8")})
                    assert response.status_code == 201, response.text
                    jobs.append(response.json()["id"])
                done = set()
                deadline = time.monotonic() + 120
                while len(done) < len(jobs):
                    message = json.loads(ws.recv(timeout=max(0.1, deadline - time.monotonic())))
                    if message["type"] == "submission" and message["data"]["state"] == "done":
                        result = message["data"]
                        assert result["verdict"] == "AC", result["message"]
                        done.add(result["id"])
                for sid in jobs:
                    assert user.get(f"/api/submissions/{sid}").json()["verdict"] == "AC"
                board = user.get("/api/scoreboard").json()
                assert next(r for r in board if r["username"] == person["username"])["solved"] == 1
    finally:
        exec_python(CLEAN, credentials)
