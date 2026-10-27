"""Cổng worker phía server, giai đoạn trước và lúc `REGISTER` (README 2.3, 2.5, 2.6, D15, D16)."""

import pytest

from _server_contract_support import (  # noqa: F401 - fixture
    SECRET, make_stack, register_eventually, stack, wait_until)
from common import protocol


def reg(**over):
    msg = {"type": "REGISTER", "req_id": "r1", "name": "w1", "secret": SECRET, "protocol_version": 1}
    msg.update(over)
    return msg


def test_register_ok_echoes_req_id_and_returns_an_integer_worker_id(stack):
    w = stack.connect()
    w.send(reg())
    reply = w.recv()
    assert reply["type"] == "REGISTER_OK" and reply["req_id"] == "r1"
    assert type(reply["worker_id"]) is int


def test_each_connection_gets_its_own_worker_id_even_with_the_same_name(stack):
    first, second = stack.connect(), stack.connect()
    assert first.register("same", req_id="a") != second.register("same", req_id="b")


def test_reconnecting_gets_a_new_worker_id(stack):
    w = stack.connect()
    first = w.register()
    w.close()
    assert stack.connect().register() != first


def test_wrong_secret_is_rejected_with_the_req_id_and_closed(stack):
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


def test_secret_with_non_ascii_characters_is_just_a_wrong_secret(stack):
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
def test_missing_type_before_register_counts_as_a_rejected_registration(stack, raw):
    w = stack.connect()
    w.send_raw(raw)
    w.expect_error("REGISTER_REJECTED")
    assert w.closed()


def test_line_too_long_before_register_is_reported_and_closed(stack):
    w = stack.connect()
    try:
        w.send_raw(b"x" * (protocol.MAX_LINE_WORKER_TO_SERVER + 1))  # chưa có \n mà buffer đã vượt giới hạn
    except OSError:
        pass  # server đóng giữa chừng khi đang gửi
    w.expect_error("LINE_TOO_LONG")
    assert w.closed()


def test_register_split_across_many_small_writes(stack):
    w = stack.connect()
    raw = protocol.encode(reg())
    for i in range(len(raw)):
        w.send_raw(raw[i:i + 1])
    assert w.recv()["type"] == "REGISTER_OK"


def test_register_accepts_unicode_escapes_as_well_as_raw_utf8(stack):
    """README 2.1: bên nhận phải chấp nhận cả dạng escape unicode của JSON lẫn UTF-8 thô."""
    w = stack.connect()
    w.send_raw(b'{"type":"REGISTER","req_id":"r1","name":"w\\u00e9","secret":"test-secret","protocol_version":1}\n')
    assert w.recv()["type"] == "REGISTER_OK"


def test_server_sends_no_job_before_register_ok(stack):
    stack.submit()
    w = stack.connect()
    with pytest.raises(TimeoutError):
        w.recv(0.5, skip_ping=False)
    w.register()
    assert w.take_job()["type"] == "JOB"


def test_connections_beyond_the_unauthenticated_limit_are_closed_without_being_read(make_stack):
    stack = make_stack(max_unauthenticated=2)
    held = [stack.connect(), stack.connect()]
    over = stack.connect()  # kết nối thứ ba: hàng đợi accept theo thứ tự nên chắc chắn là kết nối vượt giới hạn
    assert over.closed(timeout=30)
    assert all(not w.closed(timeout=0.3) for w in held)


def test_an_unauthenticated_slot_is_freed_when_the_connection_closes(make_stack):
    stack = make_stack(max_unauthenticated=1)
    holder = stack.connect()
    assert stack.connect().closed(timeout=30)  # chỗ duy nhất đang bị giữ
    holder.close()
    register_eventually(stack)  # chỗ được nhả (bất đồng bộ), kết nối sau đó đăng ký được


def test_an_unauthenticated_slot_is_freed_when_registration_completes(make_stack):
    stack = make_stack(max_unauthenticated=1)
    register_eventually(stack)
    register_eventually(stack)  # worker đầu đã đăng ký xong nên không còn chiếm chỗ
    assert wait_until(lambda: stack.registered() == 2)  # server thêm worker vào danh sách sau khi gửi REGISTER_OK


def test_register_twice_is_an_error_and_closes(stack):
    w = stack.worker()
    w.send(reg())
    w.expect_error("ALREADY_REGISTERED")
    assert w.closed()
