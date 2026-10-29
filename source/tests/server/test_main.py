"""main.py: lifespan (PLAN mục 2.1), teardown, /api/health, cấu hình từ môi trường."""

import asyncio
import json
import os
import shutil
import socket
import time

import pytest

from _support import FakeWorker, add_user, insert_submission, make_config
from server import config as config_module
from server import problems
from server.db import Database
from server.main import create_app
from server.worker_server import WorkerServer

SEED_DIR = os.path.join(os.path.dirname(problems.__file__), "problems")


def run(coro):
    return asyncio.run(coro)


async def asgi_get(app, path):
    scope = {"type": "http", "method": "GET", "path": path, "raw_path": path.encode(), "query_string": b"",
             "headers": [], "http_version": "1.1", "scheme": "http", "server": ("test", 80), "client": ("c", 1),
             "root_path": ""}
    messages = []
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.sleep(3600)

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return status, body


@pytest.fixture
def cfg(tmp_path):
    seed = tmp_path / "problems"
    seed.mkdir()
    shutil.copytree(os.path.join(SEED_DIR, "aplusb"), seed / "aplusb")
    return make_config(tmp_path, problems_dir=str(seed))


def test_startup_steps_run_in_the_documented_order(cfg):
    app = create_app(cfg)

    async def scenario():
        async with app.router.lifespan_context(app):
            return list(app.state.startup_steps)

    assert run(scenario()) == ["loop", "migrate", "seed", "reload_queue", "scoreboard", "worker_port",
                             "dispatcher", "rest", "ws"]
    assert app.state.startup_steps[-1] == "stopped"


def test_startup_stores_the_running_event_loop_and_seeds_the_db(cfg):
    app = create_app(cfg)

    async def scenario():
        async with app.router.lifespan_context(app):
            assert app.state.loop is asyncio.get_running_loop()
            return await asyncio.to_thread(
                lambda: Database(cfg.db_path).conn().execute("SELECT id, hidden FROM problems").fetchall())

    rows = run(scenario())
    assert [(r["id"], r["hidden"]) for r in rows] == [("aplusb", 0)]


def test_health_endpoint(cfg):
    app = create_app(cfg)

    async def scenario():
        async with app.router.lifespan_context(app):
            return await asgi_get(app, "/api/health")

    status, body = run(scenario())
    assert status == 200 and json.loads(body) == {"status": "ok"}


def test_worker_port_is_open_during_the_lifespan_and_closed_after_it(cfg):
    app = create_app(cfg)
    ports = []

    async def scenario():
        async with app.router.lifespan_context(app):
            port = app.state.worker_server.port
            ports.append(port)

            def talk():
                w = FakeWorker(port)
                try:
                    return w.register()
                finally:
                    w.close()

            return await asyncio.to_thread(talk)

    assert run(scenario()) == 1
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", ports[0]), timeout=1)


def test_server_can_rebind_the_same_worker_port_right_after_teardown(cfg):
    app = create_app(cfg)
    ports = []

    async def scenario():
        async with app.router.lifespan_context(app):
            ports.append(app.state.worker_server.port)
            await asyncio.to_thread(lambda: FakeWorker(ports[0]).register())  # có kết nối để lại TIME_WAIT

    run(scenario())
    cfg.worker_port = ports[0]
    again = WorkerServer(cfg, Database(cfg.db_path), None)
    again.start()  # SO_REUSEADDR: không "address already in use"
    try:
        assert again.port == ports[0]
    finally:
        again.stop()


def test_pending_submissions_in_the_db_are_delivered_after_startup(cfg):
    db = Database(cfg.db_path)
    db.migrate()
    problems.seed_problems(db, cfg.problems_dir)
    user = add_user(db)
    sid = insert_submission(db, user, problem_id="aplusb", code="print(3)")
    app = create_app(cfg)

    async def scenario():
        async with app.router.lifespan_context(app):
            def talk():
                w = FakeWorker(app.state.worker_server.port)
                try:
                    w.register()
                    return w.take_job()
                finally:
                    w.close()

            return await asyncio.to_thread(talk)

    job = run(scenario())
    assert job["submission_id"] == sid and job["code"] == "print(3)"
    assert [t["is_sample"] for t in job["tests"]] == [True, True] + [False] * 5


def test_teardown_with_a_busy_worker_is_quick_and_does_not_requeue(cfg):
    db = Database(cfg.db_path)
    db.migrate()
    problems.seed_problems(db, cfg.problems_dir)
    sid = insert_submission(db, add_user(db), problem_id="aplusb")
    app = create_app(cfg)
    started = []

    async def scenario():
        async with app.router.lifespan_context(app):
            def talk():
                w = FakeWorker(app.state.worker_server.port)
                w.register()
                w.take_job()
                return w

            w = await asyncio.to_thread(talk)
            started.append(time.monotonic())
            return w

    w = run(scenario())
    assert time.monotonic() - started[0] < 5
    w.close()
    row = Database(cfg.db_path).conn().execute("SELECT state, delivery_count FROM submissions WHERE id = ?",
                                                (sid,)).fetchone()
    assert (row["state"], row["delivery_count"]) == ("judging", 1)  # restart sau sẽ hoàn lại lần giao này


def test_load_config_reads_secret_from_file_first(tmp_path):
    secret_file = tmp_path / "secret"
    secret_file.write_text("from-file\n")
    cfg = config_module.load_config({"JH_WORKER_SECRET_FILE": str(secret_file), "JH_WORKER_SECRET": "from-env",
                                     "JH_WORKER_PORT": "9123", "JH_DB_PATH": "/x/y.db"})
    assert (cfg.secret, cfg.worker_port, cfg.db_path) == ("from-file", 9123, "/x/y.db")


def test_load_config_falls_back_to_env_secret():
    assert config_module.load_config({"JH_WORKER_SECRET": "abc"}).secret == "abc"


@pytest.mark.parametrize("env", [{}, {"JH_WORKER_SECRET": ""}, {"JH_WORKER_SECRET_FILE": "/nonexistent"},
                                 {"JH_WORKER_SECRET": "x", "JH_WORKER_PORT": "abc"}])
def test_load_config_refuses_to_start_without_a_secret_or_with_bad_numbers(env):
    with pytest.raises(config_module.ConfigError):
        config_module.load_config(env)


def test_default_worker_port_is_9000():
    assert config_module.load_config({"JH_WORKER_SECRET": "abc"}).worker_port == 9000


def scoreboard_threads():
    import threading

    return [t for t in threading.enumerate() if t.name.startswith("scoreboard-db")]


def test_the_scoreboard_is_stopped_when_startup_fails_after_it_started(cfg, monkeypatch):
    def boom(self):
        raise RuntimeError("không mở được cổng worker")

    monkeypatch.setattr(WorkerServer, "start", boom)
    app = create_app(cfg)

    async def scenario():
        with pytest.raises(RuntimeError):
            async with app.router.lifespan_context(app):
                pass

    run(scenario())
    assert scoreboard_threads() == []


def test_the_scoreboard_is_stopped_even_if_stopping_the_worker_port_fails(cfg, monkeypatch):
    original = WorkerServer.stop

    def bad_stop(self):
        original(self)
        raise RuntimeError("lỗi khi dừng cổng worker")

    monkeypatch.setattr(WorkerServer, "stop", bad_stop)
    app = create_app(cfg)

    async def scenario():
        with pytest.raises(RuntimeError):
            async with app.router.lifespan_context(app):
                pass

    run(scenario())
    assert scoreboard_threads() == []
    assert app.state.startup_steps[-1] == "stopped"


def test_a_failure_computing_the_first_scoreboard_stops_the_server_from_starting(cfg, monkeypatch):
    import server.main as main_module

    class Broken(main_module.ScoreboardService):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, compute=lambda db: 1 / 0, **{k: v for k, v in kwargs.items() if k != "compute"})

    monkeypatch.setattr(main_module, "ScoreboardService", Broken)
    app = create_app(cfg)

    async def scenario():
        with pytest.raises(ZeroDivisionError):
            async with app.router.lifespan_context(app):
                pass

    run(scenario())
    assert scoreboard_threads() == []
    assert "worker_port" not in app.state.startup_steps
