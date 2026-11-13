"""REST và WebSocket: phân quyền, không lộ test ẩn, phiên, Origin, giới hạn, đề, bảng xếp hạng."""
import asyncio
import json
from pathlib import Path

import pytest
from starlette.websockets import WebSocketDisconnect

from common.protocol import receive, send
from server.db import Database
from server.problems import insert_problem, parse_problem
from conftest import db_one, login, register, submit, system


def test_login_logout_and_roles(tmp_path):
    with system(tmp_path) as (client, app):
        assert client.get("/api/problems").status_code == 401
        assert client.post("/api/auth/login", json={"username": "alice", "password": "wrong"}).status_code == 401
        login(client)
        assert client.get("/api/auth/me").json()["role"] == "user"
        for path in ("/api/admin/users", "/api/admin/workers", "/api/admin/submissions"):
            assert client.get(path).status_code == 403, path
        assert client.patch("/api/admin/problems/1", json={"hidden": True}).status_code == 403
        assert client.post("/api/auth/logout", json={}).status_code == 200
        assert client.get("/api/auth/me").status_code == 401


def test_hidden_tests_and_other_users_submissions_are_private(tmp_path):
    with system(tmp_path) as (client, app):
        login(client, "bob")
        sid = submit(client)
        login(client, "alice")
        problem = client.get("/api/problems/1")
        assert "tests" not in problem.json() and all(set(s) == {"input", "output"} for s in problem.json()["samples"])
        hidden = db_one(client, app, "SELECT input FROM tests WHERE problem_id=1 AND is_sample=0")["input"]
        assert hidden not in problem.text
        assert client.get(f"/api/submissions/{sid}").status_code == 404
        assert all(row["id"] != sid for row in client.get("/api/submissions").json())
        login(client, "admin")
        assert client.get(f"/api/submissions/{sid}").json()["code"]
        assert any(row["id"] == sid for row in client.get("/api/admin/submissions").json())


def test_hidden_problem_invisible_to_user(tmp_path):
    with system(tmp_path) as (client, app):
        login(client, "admin")
        assert client.patch("/api/admin/problems/1", json={"hidden": True}).status_code == 200
        login(client)
        assert client.get("/api/problems/1").status_code == 404
        assert client.get("/api/problems").json() == []
        assert client.post("/api/submissions", json={"problem_id": 1, "language": "python", "code": "pass"}).status_code == 404


def test_disable_or_password_reset_kills_sessions_and_origin_is_checked(tmp_path):
    with system(tmp_path) as (client, app):
        login(client)
        old_cookie = client.cookies.get("session")
        login(client, "admin")
        assert client.patch("/api/admin/users/2", json={"password": "newpassword123"}).status_code == 200
        assert client.get("/api/auth/me", headers={"Cookie": f"session={old_cookie}"}).status_code == 401
        assert client.patch("/api/admin/users/2", json={"disabled": True}).status_code == 200
        assert client.post("/api/auth/login", json={"username": "alice", "password": "newpassword123"}).status_code == 401
        assert client.post("/api/auth/logout", json={}, headers={"Origin": "http://evil.test"}).status_code == 403


def test_admin_creates_user_and_duplicate_is_409(tmp_path):
    with system(tmp_path) as (client, app):
        login(client, "admin")
        body = {"username": "carol", "password": "password123", "role": "user"}
        assert client.post("/api/admin/users", json=body).status_code == 201
        assert client.post("/api/admin/users", json=body).status_code == 409
        assert client.post("/api/admin/users", json={**body, "username": "bad name"}).status_code == 400


def test_submission_limits(tmp_path):
    with system(tmp_path) as (client, app):
        login(client)
        assert client.post("/api/submissions", json={"problem_id": 1, "language": "python", "code": "ấ" * 30000}).status_code == 400
        assert client.post("/api/submissions", json={"problem_id": 1, "language": "ruby", "code": "x"}).status_code == 400
        for _ in range(3):
            submit(client)
        assert client.post("/api/submissions", json={"problem_id": 1, "language": "python", "code": "pass"}).status_code == 429
        login(client, "bob")
        submit(client)


def test_no_problem_editing_on_web(tmp_path):
    with system(tmp_path) as (client, app):
        login(client, "admin")
        draft = {"title": "T", "statement": "S", "tests": [{"input": "1", "output": "1"}]}
        assert client.post("/api/admin/problems", json=draft).status_code == 405
        assert client.put("/api/admin/problems/1", json=draft).status_code == 405
        assert client.get("/api/admin/problems/1").status_code == 404


def test_problem_files(tmp_path):
    def problem(**changes):
        return json.dumps({"title": "T", "statement": "S", "tests": [{"input": "1", "output": "1"}], **changes})
    assert parse_problem(problem()).time_limit_ms == 2000
    for bad in (problem(tests=[{"input": "a" * 1048577, "output": ""}]), problem(tests=[{"input": "", "output": ""}] * 21),
                problem(tests=[]), problem(memory_limit_kib=1024), problem(time_limit_ms="2000"), problem(extra=1), "{"):
        with pytest.raises(ValueError):
            parse_problem(bad)
    db = Database(str(tmp_path / "p.sqlite3"))
    for path in sorted((Path(__file__).parents[1] / "problems").glob("*.json")):
        insert_problem(db, parse_problem(path.read_text(encoding="utf-8")))
    assert any(row["memory_limit_kib"] == 65536 for row in db.all("SELECT memory_limit_kib FROM problems"))
    db.conn.close()


def test_websocket_events_scoreboard_and_privacy(tmp_path):
    with system(tmp_path) as (client, app):
        login(client, "bob")
        with client.websocket_connect("/ws") as bob_ws:
            login(client, "alice")
            with client.websocket_connect("/ws") as alice_ws:
                sid = submit(client)

                async def judge():
                    reader, writer, _ = await register(app)
                    job = await asyncio.wait_for(receive(reader), 3)
                    await send(writer, {"type": "RESULT", "submission_id": sid, "attempt_id": job["attempt_id"],
                                        "verdict": "AC", "time_ms": 5, "message": ""})
                    await asyncio.sleep(0.3)
                    writer.close()
                client.portal.call(judge)
                # WebSocket chỉ báo có thay đổi; client tải lại bằng REST.
                while client.get(f"/api/submissions/{sid}").json()["state"] != "done":
                    message = alice_ws.receive_json()
                    assert message in ({"type": "submission", "id": sid}, {"type": "scoreboard"}), message
                # Bob chỉ nhận bảng xếp hạng, không nhận sự kiện bài của Alice.
                assert [bob_ws.receive_json() for _ in range(3)] == [{"type": "scoreboard"}] * 3
        board = client.get("/api/scoreboard").json()
        assert board[0] == {"username": "alice", "solved": 1, "last_ac": board[0]["last_ac"]}


def test_websocket_of_disabled_user_is_closed(tmp_path):
    with system(tmp_path) as (client, app):
        login(client)
        with client.websocket_connect("/ws") as alice_ws:
            login(client, "admin")
            assert client.patch("/api/admin/users/2", json={"disabled": True}).status_code == 200
            with pytest.raises(WebSocketDisconnect):
                alice_ws.receive_json()


def test_websocket_rejects_foreign_origin_and_anonymous(tmp_path):
    with system(tmp_path) as (client, app):
        with pytest.raises(Exception):
            with client.websocket_connect("/ws") as ws:
                ws.receive_json()
        login(client)
        with pytest.raises(Exception):
            with client.websocket_connect("/ws", headers={"Origin": "http://evil.test"}) as ws:
                ws.receive_json()
