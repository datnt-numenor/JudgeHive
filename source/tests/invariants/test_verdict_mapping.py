"""Khóa cách worker xếp verdict từ kết quả chạy (Docker được thay bằng kết quả cho trước)."""
import threading

import pytest

from worker.runner import Runner, grade
from _support import fake_grade, outcome

JAVA_HEAP = 'Exception in thread "main" java.lang.OutOfMemoryError: Java heap space\n'


@pytest.mark.parametrize("language,test_outcome,expected", [
    ("python", outcome(), "AC"),
    ("python", outcome(stdout="  5 \n\n"), "AC"),
    ("python", outcome(stdout="6\n"), "WA"),
    ("python", outcome(reason="output"), "WA"),
    ("python", outcome(reason="timeout", exit=137), "TLE"),
    ("python", outcome(oom=True, exit=137), "MLE"),
    ("cpp", outcome(oom=True, exit=137, reason="timeout"), "MLE"),
    ("java", outcome(exit=1, stderr_tail=JAVA_HEAP), "MLE"),
    ("java", outcome(exit=1, stderr_tail="java.lang.ArithmeticException: / by zero\n"), "RE"),
    ("python", outcome(exit=1, stderr_tail=JAVA_HEAP), "RE"),
    ("cpp", outcome(exit=139), "RE"),
])
def test_outcome_to_verdict(monkeypatch, language, test_outcome, expected):
    assert fake_grade(monkeypatch, language, test_outcome)["verdict"] == expected


@pytest.mark.parametrize("compile_outcome", [outcome(exit=1, stderr="main.cpp:1: error"), outcome(reason="timeout"),
                                             outcome(oom=True)])
def test_compile_failure_is_ce(monkeypatch, compile_outcome):
    assert fake_grade(monkeypatch, "cpp", outcome(), compile_outcome)["verdict"] == "CE"


def test_docker_failure_is_se(monkeypatch):
    monkeypatch.setattr(Runner, "grade", lambda self, job, progress: (_ for _ in ()).throw(OSError("docker")))
    monkeypatch.setattr(Runner, "cleanup", lambda self: [])
    assert grade({}, threading.Event(), lambda n: None)["verdict"] == "SE"


def test_cleanup_failure_is_se(monkeypatch):
    monkeypatch.setattr(Runner, "grade", lambda self, job, progress: {"verdict": "AC", "message": "", "time_ms": 1})
    monkeypatch.setattr(Runner, "cleanup", lambda self: ["volume busy"])
    assert grade({}, threading.Event(), lambda n: None)["verdict"] == "SE"


def test_stops_at_first_failing_test_and_reports_slowest_time(monkeypatch):
    results = iter([outcome(time_ms=40), outcome(time_ms=90, stdout="0\n"), outcome(time_ms=5)])
    monkeypatch.setattr(Runner, "command", lambda self, *args, check=True: b"")
    monkeypatch.setattr(Runner, "create", lambda self, suffix, *args, **kwargs: suffix)
    monkeypatch.setattr(Runner, "remove", lambda self, name: None)
    monkeypatch.setattr(Runner, "run", lambda self, name, stdin, timeout: next(results))
    progress = []
    job = {"language": "python", "code": "x", "time_limit_ms": 2000, "memory_limit_kib": 65536,
           "tests": [{"input": "", "output": "5\n"}] * 3}
    result = grade(job, threading.Event(), progress.append)
    assert result["verdict"] == "WA" and result["time_ms"] == 90 and progress == [1]
