import asyncio
import logging
import socket
from contextlib import contextmanager

from fastapi.testclient import TestClient
import pytest
from common.protocol import MAX_LINE, MAX_LINE_TO_SERVER, encode, receive, send
from server.auth import hash_password
from server.db import Database, now
from server.main import create_app

SECRET = "test-secret-only-123456789"


@contextmanager
def system(tmp_path):
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


def submit(client):
    response = client.post("/api/submissions", json={"problem_id": 1, "language": "python", "code": "print(sum(map(int,input().split())))"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def worker(app, secret=SECRET):
    r, w = await asyncio.open_connection("127.0.0.1", app.state.worker_port, limit=MAX_LINE)
    assert w.get_extra_info("socket").getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) == 0
    await send(w, {"type": "REGISTER", "name": "test-worker", "secret": secret})
    message = await receive(r)
    return r, w, message


async def closed_by_server(reader, timeout=2):
    """Server đóng kết nối: EOF, hoặc RST khi server đóng lúc còn byte chưa đọc."""
    try:
        return await asyncio.wait_for(reader.read(), timeout) == b""
    except ConnectionError:
        return True


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.01)


def test_auth_access_admin_and_hidden_tests(tmp_path):
    with system(tmp_path) as (client, app):
        assert client.get("/api/problems").status_code == 401
        assert client.post("/api/auth/login", json={"username": "alice", "password": "wrong"}).status_code == 401
        login(client)
        assert client.get("/api/admin/users").status_code == 403
        problem = client.get("/api/problems/1").json()
        assert "tests" not in problem and len(problem["samples"]) == 1
        sid = submit(client)
        login(client, "bob")
        assert client.get(f"/api/submissions/{sid}").status_code == 404
        login(client, "admin")
        assert client.get(f"/api/submissions/{sid}").status_code == 200
        draft = {"title": "T", "statement": "Add", "tests": [{"input": "1 2", "output": "3"}]}
        assert client.put("/api/admin/problems/1", json=draft).status_code == 409
        pid = client.post("/api/admin/problems", json=draft).json()["id"]
        assert client.put(f"/api/admin/problems/{pid}", json=draft).status_code == 200
        assert client.patch(f"/api/admin/problems/{pid}", json={"hidden": True}).status_code == 200
        login(client)
        assert client.get(f"/api/problems/{pid}").status_code == 404
        assert client.post("/api/auth/logout", json={}).status_code == 200
        assert client.get("/api/auth/me").status_code == 401


def test_disable_reset_sessions_and_origin(tmp_path):
    with system(tmp_path) as (client, app):
        login(client)
        old_cookie = client.cookies.get("session")
        login(client, "admin")
        assert client.patch("/api/admin/users/2", json={"password": "newpassword123"}).status_code == 200
        assert client.get("/api/auth/me", headers={"Cookie": f"session={old_cookie}"}).status_code == 401
        assert client.patch("/api/admin/users/2", json={"disabled": True}).status_code == 200
        assert client.post("/api/auth/login", json={"username": "alice", "password": "newpassword123"}).status_code == 401
        assert client.post("/api/auth/logout", json={}, headers={"Origin": "http://evil.test"}).status_code == 403


def test_limits_and_problem_locks(tmp_path):
    with system(tmp_path) as (client, app):
        login(client)
        assert client.post("/api/submissions", json={"problem_id": 1, "language": "python", "code": "ấ" * 30000}).status_code == 400
        login(client, "admin")
        draft = {"title": "T", "statement": "S", "tests": [{"input": "a" * 1048577, "output": ""}]}
        assert client.post("/api/admin/problems", json=draft).status_code == 400
        draft["tests"] = [{"input": "", "output": ""}] * 21
        assert client.post("/api/admin/problems", json=draft).status_code == 422


def test_parallel_results_and_websocket_privacy(tmp_path):
    with system(tmp_path) as (client, app):
        login(client)
        with client.websocket_connect("/ws") as ws:
            first, second = submit(client), submit(client)

            async def scenario():
                r1, w1, _ = await worker(app)
                r2, w2, _ = await worker(app)
                j1, j2 = await asyncio.gather(receive(r1), receive(r2))
                assert {j1["submission_id"], j2["submission_id"]} == {first, second}
                assert all(w["state"] == "judging" for w in app.state.engine.workers.values())
                for w, j in [(w1, j1), (w2, j2)]:
                    await send(w, {"type": "RESULT", "submission_id": j["submission_id"], "attempt_id": j["attempt_id"],
                                   "verdict": "AC", "time_ms": 5, "message": ""})
                await until(lambda: app.state.db.one("SELECT COUNT(*) n FROM submissions WHERE state='done'")["n"] == 2)
                w1.close(); w2.close()
            client.portal.call(scenario)
            seen = set()
            while len(seen) < 2:
                message = ws.receive_json()
                if message["type"] == "submission" and message["data"]["state"] == "done":
                    assert "code" not in message["data"]
                    seen.add(message["data"]["id"])
            board = client.get("/api/scoreboard").json()
            assert board[0]["username"] == "alice" and board[0]["solved"] == 1


def test_disconnect_redelivery_stale_result_and_retry_cap(tmp_path):
    with system(tmp_path) as (client, app):
        login(client)
        sid = submit(client)

        async def scenario():
            for attempt in range(1, 4):
                r, w, ack = await worker(app)
                assert ack["type"] == "REGISTER_OK"
                job = await asyncio.wait_for(receive(r), 2)
                assert job["attempt_id"] == attempt
                if attempt == 2:
                    await send(w, {"type": "RESULT", "submission_id": sid, "attempt_id": 1,
                                   "verdict": "AC", "time_ms": 1})
                    await asyncio.wait_for(r.read(), 2)
                else:
                    w.close(); await w.wait_closed()
                await until(lambda: app.state.db.one("SELECT state FROM submissions WHERE id=?", (sid,))["state"] != "judging")
            row = app.state.db.one("SELECT * FROM submissions WHERE id=?", (sid,))
            assert row["state"] == "done" and row["verdict"] == "SE" and row["attempt_id"] == 3
        client.portal.call(scenario)


def test_job_deadline_not_extended_by_progress(tmp_path, monkeypatch):
    monkeypatch.setattr("server.dispatch.job_timeout", lambda job: 0.15)
    with system(tmp_path) as (client, app):
        login(client)
        sid = submit(client)

        async def scenario():
            r, w, _ = await worker(app)
            job = await receive(r)
            await asyncio.sleep(0.1)
            await send(w, {"type": "PROGRESS", "submission_id": sid, "attempt_id": job["attempt_id"], "tests_done": 1})
            started = asyncio.get_running_loop().time()
            assert await asyncio.wait_for(r.read(), 0.12) == b""
            assert asyncio.get_running_loop().time() - started < 0.12
            await until(lambda: app.state.db.one("SELECT state FROM submissions WHERE id=?", (sid,))["state"] == "queued")
            w.close()
        client.portal.call(scenario)


def test_bad_worker_registration(tmp_path):
    with system(tmp_path) as (client, app):
        async def scenario():
            r, w, ack = await worker(app, "incorrect")
            assert ack["type"] == "ERROR"
            assert await r.read() == b""
            w.close()
        client.portal.call(scenario)


def test_websocket_does_not_send_other_users_submission(tmp_path):
    with system(tmp_path) as (client, app):
        login(client, "alice")
        with client.websocket_connect("/ws") as alice_ws:
            login(client, "bob")
            submit(client)
            assert alice_ws.receive_json()["type"] == "scoreboard"


def test_no_periodic_worker_messages_while_idle(tmp_path):
    with system(tmp_path) as (client, app):
        async def scenario():
            r, w, ack = await worker(app)
            assert ack["type"] == "REGISTER_OK"
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(receive(r), 0.1)
            w.close()
        client.portal.call(scenario)


def test_restart_recovers_pending_and_final_attempt(tmp_path):
    path = tmp_path / "recover.sqlite3"
    db = Database(str(path))
    uid = db.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)", ("a", hash_password("password123"), "user"))
    for attempt in (1, 3):
        db.execute("INSERT INTO submissions(user_id,problem_id,language,code,submitted_at,state,attempt_id) VALUES(?,1,'python','pass',?,'judging',?)", (uid, now(), attempt))
    db.conn.close()
    app = create_app(str(path), SECRET, worker_port=0)
    with TestClient(app) as client:
        rows = client.portal.call(lambda: app.state.db.all("SELECT state,verdict FROM submissions ORDER BY id"))
        assert rows == [{"state": "queued", "verdict": None}, {"state": "done", "verdict": "SE"}]


def test_protocol_fragmentation_combining_and_errors():
    async def scenario():
        reader = asyncio.StreamReader(limit=MAX_LINE)
        packet = encode({"type": "REGISTER_OK"})
        reader.feed_data(packet[:5])
        pending = asyncio.create_task(receive(reader))
        await asyncio.sleep(0)
        assert not pending.done()
        reader.feed_data(packet[5:] + packet)
        assert (await pending)["type"] == "REGISTER_OK"
        assert (await receive(reader))["type"] == "REGISTER_OK"
        for bad in (b"not json\n", b"[]\n", b'{"type":"PING"}\n', b"\xff\n", b"x" * (MAX_LINE + 1)):
            r = asyncio.StreamReader(limit=MAX_LINE); r.feed_data(bad)
            with pytest.raises((ValueError, UnicodeDecodeError)):
                await receive(r)
    asyncio.run(scenario())


def test_register_line_over_server_limit_closes(tmp_path):
    with system(tmp_path) as (client, app):
        async def scenario():
            # Không có newline: StreamReader dừng ở giới hạn, không đệm tới 8 MiB.
            r, w = await asyncio.open_connection("127.0.0.1", app.state.worker_port)
            w.write(b"x" * (MAX_LINE_TO_SERVER + 1024))
            assert await closed_by_server(r)
            w.close()
            # JSON hợp lệ có newline nhưng dài hơn giới hạn chiều vào server.
            r, w = await asyncio.open_connection("127.0.0.1", app.state.worker_port)
            line = encode({"type": "REGISTER", "name": "x" * MAX_LINE_TO_SERVER, "secret": SECRET})
            w.write(line)
            assert await closed_by_server(r)
            w.close()
            assert app.state.engine.workers == {}
        client.portal.call(scenario)


def test_unregistered_connection_cap(tmp_path):
    with system(tmp_path) as (client, app):
        async def scenario():
            port = app.state.worker_port
            idle = [await asyncio.open_connection("127.0.0.1", port) for _ in range(16)]
            await until(lambda: app.state.engine.unregistered == 16)
            r, w = await asyncio.open_connection("127.0.0.1", port)
            await send(w, {"type": "REGISTER", "name": "late", "secret": SECRET})
            assert await closed_by_server(r, 1)
            w.close()
            for _, idle_writer in idle:
                idle_writer.close()
            # Đếm phải được giảm cả khi kết nối đóng giữa lúc chờ REGISTER.
            await until(lambda: app.state.engine.unregistered == 0)
            r, w, ack = await worker(app)
            assert ack["type"] == "REGISTER_OK"
            w.close()
        client.portal.call(scenario)


def test_register_non_ascii_or_surrogate_secret_gets_error(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    with system(tmp_path) as (client, app):
        async def scenario():
            r, w, ack = await worker(app, "é" * 20)
            assert ack["type"] == "ERROR"
            assert await closed_by_server(r)
            w.close()
            r, w = await asyncio.open_connection("127.0.0.1", app.state.worker_port, limit=MAX_LINE)
            w.write(b'{"type":"REGISTER","name":"x","secret":"\\ud800"}\n')
            assert (await asyncio.wait_for(receive(r), 2))["type"] == "ERROR"
            w.close()
        client.portal.call(scenario)
    assert not [rec for rec in caplog.records if rec.levelno >= logging.ERROR]


def test_receive_respects_max_line():
    async def scenario():
        small = encode({"type": "PROGRESS", "pad": "x" * 1000})
        reader = asyncio.StreamReader(limit=MAX_LINE_TO_SERVER)
        reader.feed_data(small)
        assert (await receive(reader, MAX_LINE_TO_SERVER))["type"] == "PROGRESS"
        big = encode({"type": "PROGRESS", "pad": "x" * MAX_LINE_TO_SERVER})
        reader = asyncio.StreamReader(limit=MAX_LINE)
        reader.feed_data(big)
        with pytest.raises(ValueError):
            await receive(reader, MAX_LINE_TO_SERVER)
    asyncio.run(scenario())
