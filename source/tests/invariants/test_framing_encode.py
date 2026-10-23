"""Phía gửi: README 2.1 (UTF-8 thô, một dòng, kết thúc `\\n`) và 2.2 (giới hạn độ dài)."""

import json

import pytest

from common import protocol
from common.protocol import EncodeError, LineDecoder, Message, encode

from helpers import ONE_MB

TEXT = "Xin chào, Đà Nẵng ạ 😀"


def test_non_ascii_is_written_as_raw_utf8():
    data = encode({"type": "T", "text": TEXT})
    assert TEXT.encode("utf-8") in data
    assert b"\\u" not in data


def test_ends_with_exactly_one_newline_and_has_no_other_raw_newline():
    data = encode({"type": "T", "text": "dòng 1\ndòng 2\r\n\ttab"})
    assert data.endswith(b"\n")
    assert b"\n" not in data[:-1]
    assert b"\r" not in data
    assert json.loads(data.decode("utf-8"))["text"] == "dòng 1\ndòng 2\r\n\ttab"


def test_round_trip_through_decoder():
    obj = {"type": "RESULT", "text": TEXT, "n": [1, 2, {"k": None, "b": True}], "f": 1.5}
    assert LineDecoder(ONE_MB).feed(encode(obj)) == [Message(obj)]


def test_several_encoded_messages_concatenate_into_separate_lines():
    a, b = {"type": "PING", "seq": 1}, {"type": "PING", "seq": 2}
    assert LineDecoder(ONE_MB).feed(encode(a) + encode(b)) == [Message(a), Message(b)]


def test_line_exactly_at_limit_is_allowed_and_one_over_is_rejected():
    limit = 1000
    base = len(encode({"type": "T", "pad": ""}))
    obj = {"type": "T", "pad": "a" * (limit - base)}
    assert len(encode(obj, max_line_bytes=limit)) == limit
    obj["pad"] += "a"
    with pytest.raises(EncodeError):
        encode(obj, max_line_bytes=limit)


def test_limit_counts_bytes_not_characters():
    base = len(encode({"type": "T", "pad": ""}))
    obj = {"type": "T", "pad": "ệ" * 100}  # 100 ký tự, 300 byte
    assert len(encode(obj)) == base + 300
    assert len(encode(obj, max_line_bytes=base + 300)) == base + 300
    with pytest.raises(EncodeError):
        encode(obj, max_line_bytes=base + 299)


def test_no_limit_by_default():
    assert len(encode({"type": "T", "pad": "a" * (2 * ONE_MB)})) > 2 * ONE_MB


@pytest.mark.parametrize("bad", [
    pytest.param(["type", "T"], id="list"),
    pytest.param("T", id="str"),
    pytest.param(None, id="none"),
    pytest.param(42, id="int"),
    pytest.param(b"{}", id="bytes"),
    pytest.param((("type", "T"),), id="tuple"),
])
def test_non_dict_is_rejected(bad):
    with pytest.raises(EncodeError):
        encode(bad)


@pytest.mark.parametrize("obj", [
    pytest.param({"type": "T", "v": "\ud800"}, id="lone-surrogate-value"),
    pytest.param({"type": "T", "\udc00": 1}, id="lone-surrogate-key"),
    pytest.param({"type": "T", "v": object()}, id="not-serializable"),
    pytest.param({"type": "T", "v": {1, 2}}, id="set"),
    pytest.param({"type": "T", "v": float("nan")}, id="nan"),
    pytest.param({"type": "T", "v": float("inf")}, id="inf"),
])
def test_unserializable_message_raises_encode_error(obj):
    with pytest.raises(EncodeError):
        encode(obj)


def test_encode_error_is_a_value_error():
    assert issubclass(EncodeError, ValueError)


def test_deeply_nested_message_raises_encode_error():
    obj = {"type": "T", "v": None}
    node = obj
    for _ in range(100_000):
        node["v"] = {"v": None}
        node = node["v"]
    with pytest.raises(EncodeError):
        encode(obj)


def test_protocol_module_exposes_limits_for_both_directions():
    assert protocol.MAX_LINE_WORKER_TO_SERVER < protocol.MAX_LINE_SERVER_TO_WORKER
