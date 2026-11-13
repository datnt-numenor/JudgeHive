import asyncio
import threading

import pytest
from common.protocol import MAX_LINE, receive, send
from worker.main import connection
from worker.runner import Runner, grade


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
