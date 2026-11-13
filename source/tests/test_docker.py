"""Kiểm thử thật, bật bằng JH_DOCKER_TESTS=1 sau khi build image runner."""
import os
import subprocess
import threading
from pathlib import Path

import pytest
from worker.runner import Runner, grade

pytestmark = pytest.mark.skipif(os.environ.get("JH_DOCKER_TESTS") != "1", reason="Cần Docker daemon và image runner")
SAMPLES = Path(__file__).parents[1] / "samples"


@pytest.mark.parametrize("file,language,expected", [
    ("ac.py", "python", "AC"), ("ac.cpp", "cpp", "AC"), ("Main.java", "java", "AC"),
    ("wa.py", "python", "WA"), ("tle.py", "python", "TLE"), ("mle.py", "python", "MLE"),
    ("re.py", "python", "RE"), ("ce.cpp", "cpp", "CE"), ("output.py", "python", "WA"),
    ("MainMle.java", "java", "MLE")])
def test_real_verdicts(file, language, expected):
    job = {"code": (SAMPLES / file).read_text(encoding="utf-8"), "language": language,
           "time_limit_ms": 10000 if expected == "MLE" else 2000,
           "memory_limit_kib": 65536 if expected == "MLE" else 262144,
           "tests": [{"input": "2 3\n", "output": "5\n"}, {"input": "-7 9\n", "output": "2\n"}]}
    result = grade(job, threading.Event(), lambda count: None)
    assert result["verdict"] == expected, result


def test_docker_cancel_cleans_containers_and_volume():
    cancel = threading.Event()
    runner = Runner(cancel)
    volume = runner.volume
    try:
        runner.command("volume", "create", volume)
        container = runner.create("cancel", 65536, ["python3", "-c", "while True: pass"], readonly=True)
        timer = threading.Timer(1, cancel.set)
        timer.start()
        from worker.runner import Cancelled
        with pytest.raises(Cancelled):
            runner.run(container, "", 20)
        timer.join()
    finally:
        assert not runner.cleanup()
    assert subprocess.run(["docker", "volume", "inspect", volume], capture_output=True).returncode != 0
