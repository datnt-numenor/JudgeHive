import asyncio
import threading

import pytest
from common.protocol import MAX_LINE, receive, send
from worker.main import connection
from worker.runner import grade


def test_worker_cancels_running_work_before_reconnect(monkeypatch):
    started, cleaned = threading.Event(), threading.Event()

    def fake_grade(job, cancel, progress):
        started.set()
        assert cancel.wait(3)
        cleaned.set()

    monkeypatch.setattr("worker.main.grade", fake_grade)

    async def scenario():
        async def server(reader, writer):
            await receive(reader)
            await send(writer, {"type": "REGISTER_OK"})
            await send(writer, {"type": "JOB", "submission_id": 1, "attempt_id": 1, "language": "python", "code": "pass",
                                "tests": [{"input": "", "output": ""}], "time_limit_ms": 2000, "memory_limit_kib": 262144})
            await asyncio.to_thread(started.wait, 2)
            writer.close()
            await writer.wait_closed()
        listener = await asyncio.start_server(server, "127.0.0.1", 0, limit=MAX_LINE)
        try:
            with pytest.raises(ConnectionError):
                await asyncio.wait_for(connection("127.0.0.1", listener.sockets[0].getsockname()[1], "secret", "worker", asyncio.Event()), 5)
        finally:
            listener.close(); await listener.wait_closed()
        assert cleaned.is_set()
    asyncio.run(scenario())


def test_docker_failure_is_se_and_cleanup_attempted(monkeypatch):
    cleaned = []
    monkeypatch.setattr("worker.runner.Runner.grade", lambda *args: (_ for _ in ()).throw(OSError("Docker unavailable")))
    monkeypatch.setattr("worker.runner.Runner.cleanup", lambda self: cleaned.append(True) or [])
    result = grade({}, threading.Event(), lambda count: None)
    assert result["verdict"] == "SE" and cleaned
