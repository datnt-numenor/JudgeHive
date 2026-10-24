"""Cổng worker sau `REGISTER` thành công: lỗi từng loại message (README 2.3, D16)."""

import pytest

from _support import wait_until
from common import protocol


def test_broken_json_after_register_keeps_the_connection(stack):
    w = stack.worker()
    w.send_raw(b"not json\n")
    w.expect_error("BAD_JSON")
    stack.submit()
    assert w.take_job()["type"] == "JOB"  # kết nối vẫn dùng được


@pytest.mark.parametrize("raw", [b'{"no_type": 1}\n', b'{"type": 5}\n'], ids=["missing-type", "type-not-string"])
def test_missing_type_after_register_is_bad_json_and_keeps_the_connection(stack, raw):
    w = stack.worker()
    w.send_raw(raw)
    w.expect_error("BAD_JSON")
    stack.submit()
    w.take_job()


def test_unknown_type_keeps_the_connection(stack):
    w = stack.worker()
    w.send({"type": "SURPRISE"})
    w.expect_error("UNKNOWN_TYPE")
    w.send({"type": "JOB"})  # JOB là type của chiều ngược lại, với worker cũng là lạ
    w.expect_error("UNKNOWN_TYPE")
    stack.submit()
    w.take_job()


@pytest.mark.parametrize("pong", [{"type": "PONG"}, {"type": "PONG", "seq": "1"}, {"type": "PONG", "seq": True}])
def test_malformed_pong_is_bad_message_and_keeps_the_connection(stack, pong):
    w = stack.worker()
    w.send(pong)
    w.expect_error("BAD_MESSAGE")
    stack.submit()
    w.take_job()


@pytest.mark.parametrize("missing", ["submission_id", "attempt_id", "tests_done", "tests_total"])
def test_progress_with_missing_field_is_bad_message_and_keeps_the_connection(stack, missing):
    w = stack.worker()
    job = (stack.submit(), w.take_job())[1]
    msg = {"type": "PROGRESS", "submission_id": job["submission_id"], "attempt_id": job["attempt_id"],
           "tests_done": 0, "tests_total": 3}
    del msg[missing]
    w.send(msg)
    w.expect_error("BAD_MESSAGE")
    w.finish_ac(job)
    assert stack.wait_row(job["submission_id"], lambda r: r["state"] == "done")["verdict"] == "AC"


def test_progress_with_wrong_type_is_bad_message(stack):
    w = stack.worker()
    job = (stack.submit(), w.take_job())[1]
    w.send({"type": "PROGRESS", "submission_id": job["submission_id"], "attempt_id": job["attempt_id"],
            "tests_done": "0", "tests_total": 3})
    w.expect_error("BAD_MESSAGE")


def test_worker_error_message_is_only_logged(stack):
    w = stack.worker()
    w.send({"type": "ERROR", "code": "WHATEVER", "message": "x" * 5000})
    stack.submit()
    assert w.take_job()["type"] == "JOB"  # không phản hồi, không đóng


def test_line_too_long_after_register_closes_and_redelivers(stack):
    first = stack.worker("a")
    sid = stack.submit()
    first.take_job()
    second = stack.worker("b")
    try:
        first.send_raw(b"x" * (protocol.MAX_LINE_WORKER_TO_SERVER + 1))
    except OSError:
        pass
    first.expect_error("LINE_TOO_LONG")
    assert first.closed()
    job = second.take_job()
    assert job["submission_id"] == sid and job["attempt_id"] == 2


def test_progress_for_another_job_is_dropped_without_error(stack):
    w = stack.worker()
    job = (stack.submit(), w.take_job())[1]
    w.send({"type": "PROGRESS", "submission_id": job["submission_id"], "attempt_id": job["attempt_id"] + 5,
            "tests_done": 0, "tests_total": 3})
    w.send({"type": "PROGRESS", "submission_id": 999, "attempt_id": job["attempt_id"], "tests_done": 0,
            "tests_total": 3})
    with pytest.raises(TimeoutError):  # không có ERROR
        w.recv(0.4)
    w.finish_ac(job)  # và kết nối vẫn dùng được
    assert stack.wait_row(job["submission_id"], lambda r: r["state"] == "done")["verdict"] == "AC"


def test_progress_while_idle_is_dropped_without_error(stack):
    w = stack.worker()
    w.send({"type": "PROGRESS", "submission_id": 1, "attempt_id": 1, "tests_done": 0, "tests_total": 1})
    with pytest.raises(TimeoutError):
        w.recv(0.3)


@pytest.mark.parametrize("sequence", [
    pytest.param([0, 0], id="repeat"),
    pytest.param([1], id="skip-start"),
    pytest.param([0, 2], id="skip"),
    pytest.param([0, 1, 2, 3, 4], id="beyond-total"),
    pytest.param([0, 1, 0], id="backwards"),
    pytest.param([-1], id="negative"),
])
def test_out_of_order_progress_is_bad_message_and_keeps_the_connection(stack, sequence):
    w = stack.worker()
    job = (stack.submit(), w.take_job())[1]  # 3 test
    expected_ok = 0
    for done in sequence:
        w.progress(job, done)
        if done == expected_ok:
            expected_ok += 1
        else:
            w.expect_error("BAD_MESSAGE")
    for done in range(expected_ok, 4):
        w.progress(job, done)
    w.result(job)
    assert stack.wait_row(job["submission_id"], lambda r: r["state"] == "done")["verdict"] == "AC"


def test_progress_with_wrong_tests_total_is_bad_message(stack):
    w = stack.worker()
    job = (stack.submit(), w.take_job())[1]
    w.send({"type": "PROGRESS", "submission_id": job["submission_id"], "attempt_id": job["attempt_id"],
            "tests_done": 0, "tests_total": 99})
    w.expect_error("BAD_MESSAGE")


@pytest.mark.parametrize("change", [
    pytest.param({"verdict": "ZZ"}, id="unknown-verdict"),
    pytest.param({"verdict": None}, id="verdict-null"),
    pytest.param({"verdict": ["AC"]}, id="verdict-unhashable"),
    pytest.param({"submission_id": "1"}, id="submission-id-string"),
    pytest.param({"attempt_id": None}, id="attempt-id-null"),
    pytest.param({"time_ms": "5"}, id="time-string"),
    pytest.param({"time_ms": -1}, id="time-negative"),
    pytest.param({"memory_kib": None}, id="memory-null"),
    pytest.param({"time_ms": True}, id="time-bool"),
    pytest.param({"time_ms": 2**31}, id="time-over-int32"),
    pytest.param({"memory_kib": 2**70}, id="memory-beyond-sqlite-integer"),
])
def test_malformed_result_is_bad_message_closes_and_redelivers(stack, change):
    first = stack.worker("a")
    sid = stack.submit()
    job = first.take_job()
    second = stack.worker("b")
    msg = {"type": "RESULT", "submission_id": sid, "attempt_id": job["attempt_id"], "verdict": "AC",
           "time_ms": 5, "memory_kib": 1024}
    msg.update(change)
    first.send(msg)
    first.expect_error("BAD_MESSAGE")
    assert first.closed()
    again = second.take_job()
    assert again["submission_id"] == sid and again["attempt_id"] == job["attempt_id"] + 1


@pytest.mark.parametrize("verdict,fields", [
    ("WA", {"time_ms": 1, "memory_kib": 1, "failed_test": 1, "failed_is_sample": True}),  # thiếu sample_stdout
    ("WA", {"time_ms": 1, "memory_kib": 1, "failed_test": 2}),  # thiếu failed_is_sample
    ("TLE", {"time_ms": 1, "memory_kib": 1, "failed_is_sample": False}),  # thiếu failed_test
    ("RE", {"failed_test": 2, "failed_is_sample": False}),  # thiếu re_kind
    ("RE", {"failed_test": 2, "failed_is_sample": False, "re_kind": "SIGWHATEVER"}),
    ("CE", {}),  # thiếu ce_message
    ("CE", {"ce_message": 5}),
    ("SE", {"se_detail": 5}),
])
def test_result_missing_fields_for_its_verdict_is_bad_message(stack, verdict, fields):
    w = stack.worker()
    job = (stack.submit(), w.take_job())[1]
    w.result(job, verdict, **fields)
    w.expect_error("BAD_MESSAGE")
    assert w.closed()


def test_result_without_a_job_is_unexpected_and_closes(stack):
    w = stack.worker()
    w.send({"type": "RESULT", "submission_id": 1, "attempt_id": 1, "verdict": "AC", "time_ms": 1, "memory_kib": 1})
    w.expect_error("UNEXPECTED_RESULT")
    assert w.closed()


def test_result_for_another_attempt_is_unexpected_closes_and_redelivers(stack):
    first = stack.worker("a")
    sid = stack.submit()
    job = first.take_job()
    second = stack.worker("b")
    first.send({"type": "RESULT", "submission_id": sid, "attempt_id": job["attempt_id"] + 1, "verdict": "AC",
                "time_ms": 1, "memory_kib": 1})
    first.expect_error("UNEXPECTED_RESULT")
    assert first.closed()
    again = second.take_job()
    assert again["submission_id"] == sid and again["attempt_id"] == job["attempt_id"] + 1
    assert stack.row(sid)["verdict"] is None


@pytest.mark.parametrize("fields", [
    pytest.param({"failed_test": 4, "failed_is_sample": False}, id="failed-test-beyond-total"),
    pytest.param({"failed_test": 0, "failed_is_sample": False}, id="failed-test-zero"),
    pytest.param({"failed_test": 2, "failed_is_sample": True, "sample_stdout": "SECRET-HIDDEN-OUTPUT"},
                 id="hidden-test-claimed-as-sample"),
    pytest.param({"failed_test": 1, "failed_is_sample": False}, id="sample-test-claimed-as-hidden"),
])
def test_result_with_inconsistent_failed_test_is_rejected_and_nothing_is_stored(stack, fields):
    first = stack.worker("a")
    sid = stack.submit()  # test 1 là mẫu, test 2 và 3 là ẩn
    job = first.take_job()
    second = stack.worker("b")
    first.result(job, "WA", time_ms=1, memory_kib=1, **fields)
    first.expect_error("BAD_MESSAGE")
    assert first.closed()
    second.take_job()
    row = stack.row(sid)
    assert row["verdict"] is None and row["sample_stdout"] is None


def test_idle_worker_dying_has_no_effect_on_the_queue(stack):
    gone = stack.worker("gone")
    gone.close()
    assert wait_until(lambda: not stack.server.snapshot())
    stack.submit()
    stack.worker("alive").take_job()
