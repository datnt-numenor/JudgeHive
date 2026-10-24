"""Kiểm tra field của JOB (README mục JOB): `executor.parse_job` nhận message hợp lệ, từ chối mọi message sai."""

import pytest

from common import protocol
from worker import executor


def job_msg(**over):
    msg = {"type": "JOB", "submission_id": 1, "attempt_id": 2, "language": "python", "code": "print(1)",
           "time_limit_ms": 1000, "memory_limit_kib": 1024,
           "tests": [{"input": "", "output": "1\n", "is_sample": True}]}
    msg.update(over)
    return msg


def test_parse_job_ok():
    job = executor.parse_job(job_msg())
    assert len(job.tests) == 1


@pytest.mark.parametrize("over", [
    {"language": "ruby"}, {"language": 1}, {"language": None},
    {"code": None}, {"code": 5}, {"code": "x" * (protocol.MAX_CODE_BYTES + 1)},
    {"submission_id": "1"}, {"submission_id": True}, {"attempt_id": 1.5},
    {"time_limit_ms": 0}, {"time_limit_ms": "1000"}, {"memory_limit_kib": -1},
    {"tests": []}, {"tests": None}, {"tests": [1]},
    {"tests": [{"input": "", "output": "", "is_sample": 1}]},
    {"tests": [{"input": 1, "output": "", "is_sample": True}]},
    {"tests": [{"input": "", "is_sample": True}]},
])
def test_parse_job_rejects(over):
    with pytest.raises(executor.BadJob):
        executor.parse_job(job_msg(**over))


@pytest.mark.parametrize("missing", ["submission_id", "attempt_id", "language", "code", "time_limit_ms",
                                     "memory_limit_kib", "tests"])
def test_parse_job_rejects_missing_field(missing):
    msg = job_msg()
    del msg[missing]
    with pytest.raises(executor.BadJob):
        executor.parse_job(msg)
