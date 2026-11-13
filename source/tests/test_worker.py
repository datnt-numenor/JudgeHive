import asyncio
import json
import threading

import pytest
from common.protocol import MAX_LINE, receive, send
from worker.main import connection
from worker.runner import DockerError, Runner, container_ms, grade


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


def outcome(**changes):
    base = {"reason": None, "oom": False, "exit": 0, "stdout": "5\n", "stderr": "", "stderr_tail": "", "time_ms": 10}
    return {**base, **changes}


def grade_with(monkeypatch, language, test_outcome):
    """Chạy Runner.grade thật, chỉ thay các lời gọi Docker bằng kết quả cho trước."""
    monkeypatch.setattr(Runner, "command", lambda self, *args, check=True: b"")
    monkeypatch.setattr(Runner, "create", lambda self, suffix, *args, **kwargs: self.prefix + "-" + suffix)
    monkeypatch.setattr(Runner, "remove", lambda self, name: None)
    monkeypatch.setattr(Runner, "run", lambda self, name, stdin, timeout:
                        outcome() if name.endswith("compile") else test_outcome)
    job = {"language": language, "code": "x", "time_limit_ms": 2000, "memory_limit_kib": 65536,
           "tests": [{"input": "2 3\n", "output": "5\n"}]}
    return grade(job, threading.Event(), lambda count: None)


JAVA_HEAP = 'Exception in thread "main" java.lang.OutOfMemoryError: Java heap space\n'


def test_java_oom_signature_is_mle(monkeypatch):
    result = grade_with(monkeypatch, "java", outcome(exit=1, stderr_tail=JAVA_HEAP))
    assert result["verdict"] == "MLE"


def test_java_signature_only_counts_for_java(monkeypatch):
    assert grade_with(monkeypatch, "python", outcome(exit=1, stderr_tail=JAVA_HEAP))["verdict"] == "RE"
    assert grade_with(monkeypatch, "cpp", outcome(exit=1, stderr_tail=JAVA_HEAP))["verdict"] == "RE"


def test_java_nonzero_without_signature_is_re(monkeypatch):
    stderr = 'Exception in thread "main" java.lang.ArithmeticException: / by zero\n'
    assert grade_with(monkeypatch, "java", outcome(exit=1, stderr_tail=stderr))["verdict"] == "RE"


def test_java_signature_with_zero_exit_is_not_mle(monkeypatch):
    # Chương trình tự bắt OutOfMemoryError, in ra stderr rồi thoát 0: chấm theo output.
    assert grade_with(monkeypatch, "java", outcome(exit=0, stderr_tail=JAVA_HEAP))["verdict"] == "AC"


def docker_state(started, finished, **changes):
    return {"Running": False, "OOMKilled": False, "ExitCode": 0, "Error": "",
            "StartedAt": started, "FinishedAt": finished, **changes}


def test_container_ms_from_docker_timestamps():
    assert container_ms(docker_state("2026-11-13T01:02:03.123456789Z", "2026-11-13T01:02:03.373456789Z")) == 250
    assert container_ms(docker_state("2026-11-13T23:59:59.9Z", "2026-11-14T00:00:01.1Z")) == 1200
    assert container_ms(docker_state("2026-11-13T08:00:00+07:00", "2026-11-13T01:00:00.5Z")) == 500
    for bad in (docker_state("2026-11-13T01:02:03Z", "0001-01-01T00:00:00Z"),
                docker_state("2026-11-13T01:02:03Z", None), docker_state("hôm qua", "2026-11-13T01:02:03Z")):
        with pytest.raises(DockerError):
            container_ms(bad)


class FinishedProcess:
    def __init__(self, *args, **kwargs):
        pass

    def poll(self):
        return 0


def run_with_state(monkeypatch, state, timeout=2):
    """Runner.run thật; Docker CLI giả trả `state`, còn wall time phía CLI bị đẩy lên rất lớn."""
    clock = iter(range(0, 10**6, 100))
    monkeypatch.setattr("worker.runner.time.monotonic", lambda: next(clock))
    monkeypatch.setattr("worker.runner.subprocess.Popen", FinishedProcess)
    monkeypatch.setattr(Runner, "command", lambda self, *args, check=True:
                        json.dumps(state).encode() if args[0] == "inspect" else b"")
    return Runner(threading.Event()).run("container", "", timeout)


def test_time_uses_container_timestamps_not_cli_wall(monkeypatch):
    result = run_with_state(monkeypatch, docker_state("2026-11-13T01:00:00Z", "2026-11-13T01:00:01.9Z"))
    assert result["reason"] is None and result["time_ms"] == 1900


def test_tle_decided_by_container_time(monkeypatch):
    result = run_with_state(monkeypatch, docker_state("2026-11-13T01:00:00Z", "2026-11-13T01:00:02.1Z"))
    assert result["reason"] == "timeout" and result["time_ms"] == 2100


def test_time_ms_is_slowest_test(monkeypatch):
    times = iter([30, 70, 20])
    monkeypatch.setattr(Runner, "command", lambda self, *args, check=True: b"")
    monkeypatch.setattr(Runner, "create", lambda self, suffix, *args, **kwargs: self.prefix + "-" + suffix)
    monkeypatch.setattr(Runner, "remove", lambda self, name: None)
    monkeypatch.setattr(Runner, "run", lambda self, name, stdin, timeout: outcome(time_ms=next(times)))
    job = {"language": "python", "code": "x", "time_limit_ms": 2000, "memory_limit_kib": 65536,
           "tests": [{"input": "", "output": "5\n"}] * 3}
    result = grade(job, threading.Event(), lambda count: None)
    assert result == {"verdict": "AC", "message": "", "time_ms": 70}
