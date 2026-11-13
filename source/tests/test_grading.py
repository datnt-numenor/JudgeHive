"""Chấm bài ở worker: verdict, không lộ dữ liệu test ẩn, đo thời gian, hủy bài khi mất server."""
import asyncio
import threading

import pytest

from common.protocol import MAX_LINE, receive, send
from worker.main import connection
from worker.runner import DockerError, Runner, container_ms, grade
from conftest import docker_state, fake_grade, fake_run, outcome

JAVA_HEAP = 'Exception in thread "main" java.lang.OutOfMemoryError: Java heap space\n'


@pytest.mark.parametrize("language,result,expected", [
    ("python", outcome(), "AC"),
    ("python", outcome(stdout="  5 \n\n"), "AC"),  # so theo token
    ("java", outcome(stderr=JAVA_HEAP), "AC"),  # tự bắt OutOfMemoryError rồi thoát 0
    ("python", outcome(stdout="6\n"), "WA"),
    ("python", outcome(reason="output"), "WA"),
    ("python", outcome(reason="timeout", exit=137), "TLE"),
    ("python", outcome(oom=True, exit=137), "MLE"),
    ("cpp", outcome(oom=True, exit=137, reason="timeout"), "MLE"),
    ("java", outcome(exit=1, stderr=JAVA_HEAP), "MLE"),
    ("java", outcome(exit=1, stderr="java.lang.ArithmeticException: / by zero\n"), "RE"),
    ("python", outcome(exit=1, stderr=JAVA_HEAP), "RE"),  # chữ ký Java chỉ áp cho Java
    ("cpp", outcome(exit=139), "RE"),
])
def test_outcome_to_verdict(monkeypatch, language, result, expected):
    assert fake_grade(monkeypatch, language, result)["verdict"] == expected


@pytest.mark.parametrize("compiled", [outcome(exit=1, stderr="main.cpp:1: error"), outcome(reason="timeout"), outcome(oom=True)])
def test_compile_failure_is_ce(monkeypatch, compiled):
    assert fake_grade(monkeypatch, "cpp", outcome(), compiled)["verdict"] == "CE"


@pytest.mark.parametrize("cleanup_errors", [None, ["volume busy"]])
def test_docker_or_cleanup_failure_is_se(monkeypatch, cleanup_errors):
    if cleanup_errors is None:
        monkeypatch.setattr(Runner, "grade", lambda self, job, progress: (_ for _ in ()).throw(OSError("docker")))
    else:
        monkeypatch.setattr(Runner, "grade", lambda self, job, progress: {"verdict": "AC", "message": "", "time_ms": 1})
    monkeypatch.setattr(Runner, "cleanup", lambda self: cleanup_errors or [])
    assert grade({}, threading.Event(), lambda n: None)["verdict"] == "SE"


def test_stops_at_first_failing_test_and_reports_slowest_time(monkeypatch):
    results = [outcome(time_ms=40), outcome(time_ms=90, stdout="0\n"), outcome(time_ms=5)]
    result = fake_grade(monkeypatch, "python", results, tests=[{"input": "", "output": "5\n"}] * 3)
    assert result["verdict"] == "WA" and result["time_ms"] == 90 and result["progress"] == [1]


def test_messages_never_contain_hidden_output_or_exit_code(monkeypatch):
    leaked = "SECRET-HIDDEN-INPUT-42"
    hidden = [{"input": leaked, "output": "x"}]
    for code in (97, 113, 200):
        result = fake_grade(monkeypatch, "cpp", outcome(exit=code, stdout=leaked, stderr=leaked), tests=hidden)
        assert result["verdict"] == "RE" and leaked not in result["message"] and str(code) not in result["message"]
    result = fake_grade(monkeypatch, "python", outcome(stdout=leaked), tests=hidden)
    assert result["verdict"] == "WA" and leaked not in result["message"]


# ---------- Thời gian theo mốc Docker ----------

def test_container_ms_parses_docker_timestamps():
    assert container_ms(docker_state("2026-11-13T01:02:03.123456789Z", "2026-11-13T01:02:03.373456789Z")) == 250
    assert container_ms(docker_state("2026-11-13T23:59:59.9Z", "2026-11-14T00:00:01.1Z")) == 1200
    assert container_ms(docker_state("2026-11-13T08:00:00+07:00", "2026-11-13T01:00:00.5Z")) == 500
    for finished in ("0001-01-01T00:00:00Z", None, "hôm nay", "2026-11-13T01:00:00Z"):
        with pytest.raises(DockerError):
            container_ms(docker_state("2026-11-13T01:00:01Z", finished))


@pytest.mark.parametrize("finished,still_running,reason", [
    ("01:00:01.9", False, None),        # 1,9 giây < 2 giây dù đồng hồ CLI chạy rất lâu
    ("01:00:02.001", False, "timeout"),  # vượt giới hạn theo thời gian container
    ("01:00:02.9", True, "timeout"),     # watchdog kill sau giới hạn + START_SLACK
])
def test_time_and_tle_come_from_container(monkeypatch, finished, still_running, reason):
    result = fake_run(monkeypatch, docker_state("2026-11-13T01:00:00Z", f"2026-11-13T{finished}Z"),
                      still_running=still_running)
    assert result["reason"] == reason
    assert result["time_ms"] == container_ms(docker_state("2026-11-13T01:00:00Z", f"2026-11-13T{finished}Z"))


def test_watchdog_kill_before_program_used_its_limit_is_se(monkeypatch):
    with pytest.raises(DockerError):
        fake_run(monkeypatch, docker_state("2026-11-13T01:00:00Z", "2026-11-13T01:00:01.5Z"), still_running=True)


# ---------- Kết nối của worker ----------

def test_worker_cancels_running_work_before_reconnect(monkeypatch):
    started, cleaned = threading.Event(), threading.Event()

    def slow_grade(job, cancel, progress):
        started.set()
        assert cancel.wait(3)
        cleaned.set()
    monkeypatch.setattr("worker.main.grade", slow_grade)

    async def scenario():
        async def server(reader, writer):
            await receive(reader)
            await send(writer, {"type": "REGISTER_OK", "worker_id": 1})
            await send(writer, {"type": "JOB", "submission_id": 1, "attempt_id": 1, "language": "python", "code": "pass",
                                "tests": [{"input": "", "output": ""}], "time_limit_ms": 2000, "memory_limit_kib": 262144})
            await asyncio.to_thread(started.wait, 2)
            writer.close()
        listener = await asyncio.start_server(server, "127.0.0.1", 0, limit=MAX_LINE)
        try:
            with pytest.raises(ConnectionError):
                await asyncio.wait_for(connection("127.0.0.1", listener.sockets[0].getsockname()[1], "s", "w",
                                                  asyncio.Event()), 5)
        finally:
            listener.close(); await listener.wait_closed()
        assert cleaned.is_set(), "Mất server thì phải hủy và dọn bài đang chấm trước khi nối lại"
    asyncio.run(scenario())


@pytest.mark.parametrize("payload", ["too_long", "bad_job"])
def test_worker_closes_on_oversized_line_or_invalid_job(monkeypatch, payload):
    graded = []
    monkeypatch.setattr("worker.main.grade", lambda *args: graded.append(args))

    async def scenario():
        async def server(reader, writer):
            await receive(reader)
            await send(writer, {"type": "REGISTER_OK", "worker_id": 1})
            if payload == "too_long":
                writer.write(b"x" * (MAX_LINE + 10))
            else:
                await send(writer, {"type": "JOB", "submission_id": 1, "attempt_id": 1, "language": "ruby",
                                    "code": "", "tests": [], "time_limit_ms": 1, "memory_limit_kib": 1})
            try:
                await writer.drain()
                await asyncio.wait_for(reader.read(), 5)
            except ConnectionError:
                pass
        listener = await asyncio.start_server(server, "127.0.0.1", 0, limit=MAX_LINE)
        try:
            with pytest.raises(ValueError):
                await asyncio.wait_for(connection("127.0.0.1", listener.sockets[0].getsockname()[1], "s", "w",
                                                  asyncio.Event()), 10)
        finally:
            listener.close(); await listener.wait_closed()
    asyncio.run(scenario())
    assert not graded
