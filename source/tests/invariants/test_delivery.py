"""Khóa giao bài: một JOB mỗi worker, attempt_id, hạn công việc, giao lại tối đa 3 lần."""
import asyncio

import pytest

from fastapi.testclient import TestClient

from common.protocol import receive, send
from server.auth import hash_password
from server.db import Database, now
from server.main import create_app
from _support import SECRET, closed_by_server, eventually, login, register, silent_for, submit, system


def db_row(app, sid):
    return app.state.db.one("SELECT state,verdict,attempt_id,time_ms FROM submissions WHERE id=?", (sid,))


def test_job_fields_and_result_recorded(tmp_path):
    with system(tmp_path) as (client, app):
        login(client)
        sid = submit(client)

        async def scenario():
            reader, writer, _ = await register(app)
            job = await asyncio.wait_for(receive(reader), 3)
            assert job["type"] == "JOB" and job["submission_id"] == sid and job["attempt_id"] == 1
            assert job["language"] == "python" and job["tests"] and {"input", "output"} <= set(job["tests"][0])
            assert type(job["time_limit_ms"]) is int and type(job["memory_limit_kib"]) is int
            ids = {"submission_id": sid, "attempt_id": 1}
            await send(writer, {"type": "PROGRESS", **ids, "tests_done": 1})
            await send(writer, {"type": "RESULT", **ids, "verdict": "AC", "time_ms": 12, "message": ""})
            await eventually(lambda: db_row(app, sid)["state"] == "done")
            assert db_row(app, sid)["verdict"] == "AC" and db_row(app, sid)["time_ms"] == 12
            writer.close()
        client.portal.call(scenario)


def test_busy_worker_gets_one_job_at_a_time(tmp_path):
    with system(tmp_path) as (client, app):
        login(client)
        first, second = submit(client), submit(client)

        async def scenario():
            reader, writer, _ = await register(app)
            job = await asyncio.wait_for(receive(reader), 3)
            assert job["submission_id"] == first
            assert await silent_for(reader, 0.5) is True, "Worker đang bận không được nhận JOB thứ hai"
            await send(writer, {"type": "RESULT", "submission_id": first, "attempt_id": 1,
                                "verdict": "WA", "time_ms": 1, "message": ""})
            assert (await asyncio.wait_for(receive(reader), 3))["submission_id"] == second
            writer.close()
        client.portal.call(scenario)


def test_two_workers_judge_two_submissions_in_parallel(tmp_path):
    with system(tmp_path) as (client, app):
        login(client)
        ids = {submit(client), submit(client)}

        async def scenario():
            (r1, w1, _), (r2, w2, _) = await register(app, name="a"), await register(app, name="b")
            jobs = await asyncio.gather(asyncio.wait_for(receive(r1), 3), asyncio.wait_for(receive(r2), 3))
            assert {job["submission_id"] for job in jobs} == ids
            w1.close(); w2.close()
        client.portal.call(scenario)


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
            await eventually(lambda: db_row(app, sid)["state"] == "queued")
            reader, writer, _ = await register(app)
            job = await asyncio.wait_for(receive(reader), 3)
            assert job["attempt_id"] == 2
            ids = {"stale_attempt": {"submission_id": sid, "attempt_id": 1},
                   "future_attempt": {"submission_id": sid, "attempt_id": 3},
                   "string_attempt": {"submission_id": sid, "attempt_id": "2"},
                   "other_submission": {"submission_id": sid + 99, "attempt_id": 2}}[kind]
            extra = ({"verdict": "AC", "time_ms": 1, "message": ""} if message_type == "RESULT" else {"tests_done": 1})
            await send(writer, {"type": message_type, **ids, **extra})
            assert await closed_by_server(reader), "Message sai lần giao phải làm đóng kết nối"
            await eventually(lambda: db_row(app, sid)["state"] == "queued")
            assert db_row(app, sid)["verdict"] is None, "Kết quả của lần giao khác không được ghi"
            assert app.state.db.one("SELECT tests_done FROM submissions WHERE id=?", (sid,))["tests_done"] == 0
        client.portal.call(scenario)


def test_invalid_result_fields_close_connection(tmp_path):
    with system(tmp_path) as (client, app):
        login(client)
        sid = submit(client)

        async def scenario():
            reader, writer, _ = await register(app)
            job = await asyncio.wait_for(receive(reader), 3)
            await send(writer, {"type": "RESULT", "submission_id": sid, "attempt_id": job["attempt_id"],
                                "verdict": "PERFECT", "time_ms": 1, "message": ""})
            assert await closed_by_server(reader)
            await eventually(lambda: db_row(app, sid)["state"] == "queued")
        client.portal.call(scenario)


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
                await eventually(lambda: db_row(app, sid)["state"] != "judging")
            row = db_row(app, sid)
            assert row["state"] == "done" and row["verdict"] == "SE" and row["attempt_id"] == 3
        client.portal.call(scenario)


def test_job_deadline_not_extended_by_progress(tmp_path, monkeypatch):
    monkeypatch.setattr("server.dispatch.job_timeout", lambda job: 0.4)
    with system(tmp_path) as (client, app):
        login(client)
        sid = submit(client)

        async def scenario():
            reader, writer, _ = await register(app)
            job = await asyncio.wait_for(receive(reader), 3)
            started = asyncio.get_running_loop().time()
            ids = {"submission_id": sid, "attempt_id": job["attempt_id"]}
            await asyncio.sleep(0.25)
            await send(writer, {"type": "PROGRESS", **ids, "tests_done": 1})
            assert await closed_by_server(reader, 2)
            assert asyncio.get_running_loop().time() - started < 0.6, "PROGRESS không được gia hạn"
            await eventually(lambda: db_row(app, sid)["state"] == "queued")
            writer.close()
        client.portal.call(scenario)


def test_restart_requeues_judging_and_final_attempt_is_se(tmp_path):
    path = tmp_path / "restart.sqlite3"
    db = Database(str(path))
    uid = db.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)", ("u", hash_password("password123"), "user"))
    for attempt in (1, 2, 3):
        db.execute("INSERT INTO submissions(user_id,problem_id,language,code,submitted_at,state,attempt_id) "
                   "VALUES(?,1,'python','pass',?,'judging',?)", (uid, now(), attempt))
    db.conn.close()
    app = create_app(str(path), SECRET, worker_port=0)
    with TestClient(app) as client:
        rows = client.portal.call(lambda: app.state.db.all("SELECT state,verdict FROM submissions ORDER BY id"))
        assert rows == [{"state": "queued", "verdict": None}, {"state": "queued", "verdict": None},
                        {"state": "done", "verdict": "SE"}]


def test_pending_submissions_capped_at_three_per_user(tmp_path):
    with system(tmp_path) as (client, app):
        login(client)
        for _ in range(3):
            submit(client)
        assert client.post("/api/submissions", json={"problem_id": 1, "language": "python", "code": "pass"}).status_code == 429
        login(client, "bob")
        submit(client)
