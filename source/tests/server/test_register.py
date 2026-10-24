"""Cổng worker, giai đoạn trước và lúc `REGISTER` (README 2.3, 2.6, D15, D16)."""

import time

import pytest

from _support import SECRET, wait_until


def reg(**over):
    msg = {"type": "REGISTER", "req_id": "r1", "name": "w1", "secret": SECRET, "protocol_version": 1}
    msg.update(over)
    return msg


def test_register_ok_returns_worker_id_and_req_id(stack):
    w = stack.connect()
    w.send(reg())
    assert w.recv() == {"type": "REGISTER_OK", "req_id": "r1", "worker_id": 1}


def test_each_connection_gets_its_own_worker_id_even_with_same_name(stack):
    first, second = stack.connect(), stack.connect()
    assert first.register("same", req_id="a") != second.register("same", req_id="b")
    assert wait_until(lambda: len(stack.server.snapshot()) == 2)


def test_reconnect_gets_a_new_worker_id(stack):
    w = stack.connect()
    first = w.register()
    w.close()
    again = stack.connect()
    assert again.register() != first


def test_wrong_secret_is_rejected_and_closed(stack):
    w = stack.connect()
    w.send(reg(secret="nope"))
    assert w.expect_error("REGISTER_REJECTED")["req_id"] == "r1"
    assert w.closed()


def test_wrong_secret_beats_wrong_version(stack):
    w = stack.connect()
    w.send(reg(secret="nope", protocol_version=99))
    w.expect_error("REGISTER_REJECTED")
    assert w.closed()


def test_right_secret_wrong_version_has_its_own_code(stack):
    w = stack.connect()
    w.send(reg(protocol_version=2))
    assert w.expect_error("UNSUPPORTED_VERSION")["req_id"] == "r1"
    assert w.closed()


@pytest.mark.parametrize("bad", [
    pytest.param({"type": "PING", "seq": 1}, id="first-message-is-not-register"),
    pytest.param(reg(name=""), id="empty-name"),
    pytest.param(reg(name="x" * 65), id="name-too-long"),
    pytest.param(reg(name=5), id="name-not-string"),
    pytest.param(reg(secret=None), id="secret-null"),
    pytest.param(reg(secret=5), id="secret-not-string"),
    pytest.param(reg(protocol_version="1"), id="version-string"),
    pytest.param(reg(protocol_version=True), id="version-bool"),
    pytest.param({k: v for k, v in reg().items() if k != "req_id"}, id="missing-req-id"),
    pytest.param({k: v for k, v in reg().items() if k != "secret"}, id="missing-secret"),
    pytest.param({k: v for k, v in reg().items() if k != "protocol_version"}, id="missing-version"),
])
def test_malformed_register_is_rejected_with_the_common_code(stack, bad):
    w = stack.connect()
    w.send(bad)
    w.expect_error("REGISTER_REJECTED")
    assert w.closed()


def test_secret_with_non_ascii_does_not_crash_the_comparison(stack):
    w = stack.connect()
    w.send(reg(secret="bí-mật-é"))
    w.expect_error("REGISTER_REJECTED")
    assert w.closed()


@pytest.mark.parametrize("raw", [b"not json\n", b"[1, 2]\n", b"\xff\xfe\n"], ids=["not-json", "not-object", "bad-utf8"])
def test_broken_json_before_register_is_bad_json_and_closed(stack, raw):
    w = stack.connect()
    w.send_raw(raw)
    w.expect_error("BAD_JSON")
    assert w.closed()


@pytest.mark.parametrize("raw", [b'{"no_type": 1}\n', b'{"type": 5}\n'], ids=["missing-type", "type-not-string"])
def test_missing_type_before_register_counts_as_rejected_registration(stack, raw):
    w = stack.connect()
    w.send_raw(raw)
    w.expect_error("REGISTER_REJECTED")
    assert w.closed()


def test_line_too_long_before_register(stack):
    from common import protocol

    w = stack.connect()
    try:
        w.send_raw(b"x" * (protocol.MAX_LINE_WORKER_TO_SERVER + 1))  # chưa có \n mà buffer đã vượt giới hạn
    except OSError:
        pass  # server đóng giữa chừng khi đang gửi
    w.expect_error("LINE_TOO_LONG")
    assert w.closed()


def test_register_timeout(make_stack):
    stack = make_stack(register_timeout_s=0.3)
    w = stack.connect()
    started = time.monotonic()
    w.expect_error("REGISTER_TIMEOUT", timeout=3)
    assert w.closed()
    assert time.monotonic() - started < 3


def test_register_split_across_many_small_writes(stack):
    from common import protocol

    w = stack.connect()
    raw = protocol.encode(reg())
    for i in range(len(raw)):
        w.send_raw(raw[i:i + 1])
    assert w.recv()["type"] == "REGISTER_OK"


def test_no_job_and_no_ping_before_register_ok(make_stack):
    stack = make_stack(ping_interval_s=0.05)
    stack.submit()
    w = stack.connect()
    with pytest.raises(TimeoutError):
        w.recv(0.5, skip_ping=False)  # chưa REGISTER: không có JOB, không có PING
    w.register()
    assert w.take_job()["type"] == "JOB"


def test_only_max_unauthenticated_connections_are_held(make_stack):
    stack = make_stack(max_unauthenticated=2, register_timeout_s=30)
    held = [stack.connect(), stack.connect()]
    assert wait_until(lambda: stack.server._unauth == 2)
    over = stack.connect()
    assert over.closed(timeout=3)  # bị đóng ngay, không đọc message nào
    assert all(not w.closed(timeout=0.2) for w in held)


def test_unauthenticated_slot_is_freed_after_register_and_after_close(make_stack):
    stack = make_stack(max_unauthenticated=1, register_timeout_s=30)
    first = stack.connect()
    first.register()
    assert wait_until(lambda: stack.server._unauth == 0)
    second = stack.connect()
    assert wait_until(lambda: stack.server._unauth == 1)
    second.close()
    assert wait_until(lambda: stack.server._unauth == 0)
    stack.connect().register("third")


def test_register_twice_is_an_error_and_closes(stack):
    w = stack.worker()
    w.send(reg())
    w.expect_error("ALREADY_REGISTERED")
    assert w.closed()
