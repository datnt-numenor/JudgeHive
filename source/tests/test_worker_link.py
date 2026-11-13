"""Cổng worker: đăng ký, không heartbeat, giao bài, attempt_id, hạn công việc, giao lại tối đa 3 lần."""
import asyncio
import inspect
import logging
import socket

from fastapi.testclient import TestClient
import pytest

import server.main
from common.protocol import TYPES, receive, send
from server.auth import hash_password
from server.db import Database, now
from conftest import SECRET, closed_by_server, connect, eventually, login, register, submit, system


def row(app, sid):
    """Gọi trong event loop của server (bên trong scenario)."""
    return app.state.db.one("SELECT state,verdict,attempt_id,tests_done,time_ms FROM submissions WHERE id=?", (sid,))


def state(client, app, sid):
    """Gọi từ thread test, sau khi scenario xong."""
    return client.portal.call(lambda: row(app, sid))


def ids(job):
    return {"submission_id": job["submission_id"], "attempt_id": job["attempt_id"]}


# ---------- Đăng ký ----------

def test_register_ok_and_bad_registrations_get_error(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    with system(tmp_path) as (client, app):
        async def scenario():
            reader, writer, ack = await register(app)
            assert ack["type"] == "REGISTER_OK" and type(ack["worker_id"]) is int
            writer.close()
            bad = [{"type": "REGISTER", "name": "w", "secret": s} for s in ("wrong-secret-0123456789", "", "é" * 30)]
            bad += [{"type": "REGISTER", "name": "w"}, {"type": "REGISTER", "name": "", "secret": SECRET},
                    {"type": "REGISTER", "name": "w" * 65, "secret": SECRET}, {"type": "PROGRESS", "secret": SECRET}]
            for message in bad:
                reader, writer = await connect(app)
                await send(writer, message)
                assert (await asyncio.wait_for(receive(reader), 2))["type"] == "ERROR", message
                assert await closed_by_server(reader)
                writer.close()
            reader, writer = await connect(app)  # JSON cho phép \ud800, UTF-8 thường không encode được
            writer.write(b'{"type":"REGISTER","name":"w","secret":"\\ud800"}\n')
            assert (await asyncio.wait_for(receive(reader), 2))["type"] == "ERROR"
            writer.close()
        client.portal.call(scenario)
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR], "Đăng ký sai không được gây lỗi không xử lý"


def test_silent_connection_closed_after_register_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr("server.dispatch.REGISTER_TIMEOUT", 0.3)
    with system(tmp_path) as (client, app):
        async def scenario():
            reader, writer = await connect(app)
            assert await closed_by_server(reader, 2)
        client.portal.call(scenario)


def test_at_most_16_unregistered_connections_and_slots_come_back(tmp_path):
    with system(tmp_path) as (client, app):
        async def scenario():
            idle = [await connect(app) for _ in range(16)]
            await asyncio.sleep(0.3)
            reader, writer = await connect(app)
            await send(writer, {"type": "REGISTER", "name": "late", "secret": SECRET})
            assert await closed_by_server(reader, 1), "Kết nối thứ 17 chưa đăng ký phải bị đóng"
            for _, idle_writer in idle:
                idle_writer.close()
            await asyncio.sleep(0.3)
            # Chỗ trống phải được trả lại, kể cả sau nhiều lần đăng ký đúng và sai.
            for index in range(20):
                reader, writer, reply = await register(app, SECRET if index % 2 else "wrong-secret-0123456789")
                assert reply["type"] == ("REGISTER_OK" if index % 2 else "ERROR"), index
                writer.close()
        client.portal.call(scenario)


def test_disconnected_worker_leaves_worker_list(tmp_path):
    with system(tmp_path) as (client, app):
        login(client, "admin")

        async def scenario():
            reader, writer, _ = await register(app)
            await eventually(lambda: len(app.state.engine.view()) == 1)
            writer.close()
            await eventually(lambda: not app.state.engine.view())
        client.portal.call(scenario)
        assert client.get("/api/admin/workers").json()["workers"] == []


# ---------- Không heartbeat ----------

def test_no_heartbeat():
    assert TYPES == {"REGISTER", "REGISTER_OK", "JOB", "PROGRESS", "RESULT", "ERROR"}
    source = inspect.getsource(server.main)
    assert "ws_ping_interval=None" in source and "ws_ping_timeout=None" in source


def test_idle_worker_stays_silent_and_no_keepalive(tmp_path):
    with system(tmp_path) as (client, app):
        async def scenario():
            reader, writer, _ = await register(app)
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(reader.read(1), 1.5)
            assert app.state.engine.view()[0]["state"] == "idle"
            sockets = [w["writer"].get_extra_info("socket") for w in app.state.engine.workers.values()]
            assert all(s.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE) == 0 for s in sockets)
            writer.close()
        client.portal.call(scenario)


# ---------- Giao bài ----------

def test_job_progress_result_and_one_job_per_worker(tmp_path):
    with system(tmp_path) as (client, app):
        login(client)
        first, second = submit(client), submit(client)

        async def scenario():
            reader, writer, _ = await register(app)
            job = await asyncio.wait_for(receive(reader), 3)
            assert job["type"] == "JOB" and job["submission_id"] == first and job["attempt_id"] == 1
            assert job["language"] == "python" and {"input", "output"} <= set(job["tests"][0])
            with pytest.raises(TimeoutError):  # đang bận thì không nhận JOB thứ hai
                await asyncio.wait_for(reader.read(1), 0.5)
            await send(writer, {"type": "PROGRESS", **ids(job), "tests_done": 1})
            await send(writer, {"type": "RESULT", **ids(job), "verdict": "AC", "time_ms": 12, "message": ""})
            assert (await asyncio.wait_for(receive(reader), 3))["submission_id"] == second
            writer.close()
        client.portal.call(scenario)
        assert state(client, app, first) == {"state": "done", "verdict": "AC", "attempt_id": 1, "tests_done": 2, "time_ms": 12}


def test_two_workers_judge_in_parallel(tmp_path):
    with system(tmp_path) as (client, app):
        login(client)
        submitted = {submit(client), submit(client)}

        async def scenario():
            (r1, w1, _), (r2, w2, _) = await register(app, name="a"), await register(app, name="b")
            jobs = await asyncio.gather(asyncio.wait_for(receive(r1), 3), asyncio.wait_for(receive(r2), 3))
            assert {job["submission_id"] for job in jobs} == submitted
            assert all(w["state"] == "judging" for w in app.state.engine.view())
            w1.close(); w2.close()
        client.portal.call(scenario)


# ---------- attempt_id và message sai ----------

@pytest.mark.parametrize("kind", ["stale_attempt", "future_attempt", "string_attempt", "other_submission"])
@pytest.mark.parametrize("message_type", ["RESULT", "PROGRESS"])
def test_wrong_attempt_or_submission_closes_and_is_ignored(tmp_path, kind, message_type):
    with system(tmp_path) as (client, app):
        login(client)
        sid = submit(client)

        async def scenario():
            reader, writer, _ = await register(app)
            await asyncio.wait_for(receive(reader), 3)
            writer.close()
            await eventually(lambda: row(app, sid)["state"] == "queued")
            reader, writer, _ = await register(app)
            job = await asyncio.wait_for(receive(reader), 3)
            assert job["attempt_id"] == 2
            wrong = {"stale_attempt": {"submission_id": sid, "attempt_id": 1},
                     "future_attempt": {"submission_id": sid, "attempt_id": 3},
                     "string_attempt": {"submission_id": sid, "attempt_id": "2"},
                     "other_submission": {"submission_id": sid + 99, "attempt_id": 2}}[kind]
            extra = {"verdict": "AC", "time_ms": 1, "message": ""} if message_type == "RESULT" else {"tests_done": 1}
            await send(writer, {"type": message_type, **wrong, **extra})
            assert await closed_by_server(reader), "Message sai lần giao phải làm đóng kết nối"
            await eventually(lambda: row(app, sid)["state"] == "queued")
        client.portal.call(scenario)
        final = state(client, app, sid)
        assert final["verdict"] is None and final["tests_done"] == 0, "Message của lần giao khác không được ghi"


@pytest.mark.parametrize("bad", [{"tests_done": 0}, {"tests_done": 99}, {"tests_done": True}, {"tests_done": "1"},
                                 {"tests_done": 1, "repeat": True}, {"verdict": "PERFECT"}, {"time_ms": 1e300},
                                 {"time_ms": -1}, {"message": "x" * 9000}, {"message": None}])
def test_invalid_progress_or_result_closes_and_requeues(tmp_path, bad, caplog):
    caplog.set_level(logging.INFO)
    with system(tmp_path) as (client, app):
        login(client)
        sid = submit(client)

        async def scenario():
            reader, writer, _ = await register(app)
            job = await asyncio.wait_for(receive(reader), 3)
            if "tests_done" in bad:
                if bad.get("repeat"):  # 1 hợp lệ lần đầu, gửi lại 1 thì không tăng
                    await send(writer, {"type": "PROGRESS", **ids(job), "tests_done": 1})
                await send(writer, {"type": "PROGRESS", **ids(job), "tests_done": bad["tests_done"]})
            else:
                result = {"type": "RESULT", **ids(job), "verdict": "AC", "time_ms": 1, "message": "", **bad}
                await send(writer, {k: v for k, v in result.items() if v is not None})  # None = thiếu trường
            assert await closed_by_server(reader)
            await eventually(lambda: row(app, sid)["state"] == "queued")
        client.portal.call(scenario)
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


@pytest.mark.parametrize("message_type", ["RESULT", "PROGRESS"])
def test_idle_worker_sending_result_or_progress_is_closed(tmp_path, message_type):
    with system(tmp_path) as (client, app):
        async def scenario():
            reader, writer, _ = await register(app)
            await send(writer, {"type": message_type, "submission_id": 1, "attempt_id": 1,
                                "verdict": "AC", "time_ms": 1, "message": "", "tests_done": 1})
            assert await closed_by_server(reader)
        client.portal.call(scenario)


# ---------- Giao lại và hạn công việc ----------

def test_disconnect_redelivers_and_third_failure_is_se(tmp_path):
    with system(tmp_path) as (client, app):
        login(client)
        sid = submit(client)

        async def scenario():
            for attempt in (1, 2, 3):
                reader, writer, _ = await register(app)
                job = await asyncio.wait_for(receive(reader), 3)
                assert job["submission_id"] == sid and job["attempt_id"] == attempt
                writer.close()
                await eventually(lambda: row(app, sid)["state"] != "judging")
        client.portal.call(scenario)
        final = state(client, app, sid)
        assert final["state"] == "done" and final["verdict"] == "SE" and final["attempt_id"] == 3


def test_job_deadline_not_extended_by_progress(tmp_path, monkeypatch):
    monkeypatch.setattr("server.dispatch.job_timeout", lambda job: 0.4)
    with system(tmp_path) as (client, app):
        login(client)
        sid = submit(client)

        async def scenario():
            reader, writer, _ = await register(app)
            job = await asyncio.wait_for(receive(reader), 3)
            started = asyncio.get_running_loop().time()
            await asyncio.sleep(0.25)
            await send(writer, {"type": "PROGRESS", **ids(job), "tests_done": 1})
            assert await closed_by_server(reader, 2)
            assert asyncio.get_running_loop().time() - started < 0.6, "PROGRESS không được gia hạn"
            await eventually(lambda: row(app, sid)["state"] == "queued")
        client.portal.call(scenario)


def test_restart_requeues_judging_and_final_attempt_is_se(tmp_path):
    path = tmp_path / "restart.sqlite3"
    db = Database(str(path))
    uid = db.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)", ("u", hash_password("password123"), "user"))
    for attempt in (1, 2, 3):
        db.execute("INSERT INTO submissions(user_id,problem_id,language,code,submitted_at,state,attempt_id) "
                   "VALUES(?,1,'python','pass',?,'judging',?)", (uid, now(), attempt))
    db.conn.close()
    app = server.main.create_app(str(path), SECRET, worker_port=0)
    with TestClient(app) as client:
        rows = client.portal.call(lambda: app.state.db.all("SELECT state,verdict FROM submissions ORDER BY id"))
    assert rows == [{"state": "queued", "verdict": None}] * 2 + [{"state": "done", "verdict": "SE"}]


@pytest.mark.parametrize("message", [{"type": "REGISTER", "name": "again", "secret": SECRET},
                                     {"type": "ERROR", "message": "x"}, {"type": "JOB"}])
def test_registered_worker_sending_other_message_types_is_closed(tmp_path, message):
    with system(tmp_path) as (client, app):
        async def scenario():
            reader, writer, _ = await register(app)
            await send(writer, message)
            assert await closed_by_server(reader)
        client.portal.call(scenario)
